"""
swebench_eval.py -- SWE-bench-jl orchestrator / evaluator.

Subcommands
-----------
validate  <candidates.jsonl> --out <instances.jsonl>
    Validate each candidate: clone repo, apply test+gold patches, run tests
    pre/post, compute F2P/P2P, emit valid instances.

eval  <instances.jsonl> <predictions.jsonl> --out <report.json>
    Apply model patches, run tests, check F2P/P2P coverage, emit resolve rate.

run-one  <instances.jsonl> <instance_id> [--gold | --model-patch FILE]
    Debug helper: one instance, leaves worktree in place.

Examples
--------
    python3 swebench_eval.py validate candidates.jsonl --out instances.jsonl
    python3 swebench_eval.py eval instances.jsonl preds.jsonl --out report.json
    python3 swebench_eval.py run-one instances.jsonl SomeOrg__SomeRepo-42 --gold
    python3 swebench_eval.py --self-test
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_JULIA = os.environ.get("JULIA_BIN", "julia")
DEFAULT_WORK = pathlib.Path(__file__).parent.parent / "work"
HARNESS_DIR = pathlib.Path(__file__).parent

# ---------------------------------------------------------------------------
# Pure-Python core: diff and resolve logic (unit-tested via --self-test)
# ---------------------------------------------------------------------------


def diff_reports(pre: dict, post: dict) -> tuple[list, list, list]:
    """Return (F2P, P2P, NEW_FAIL) keyed by test path.

    pre/post are run_tests.jl output dicts (schema documented in module docstring).
    F2P   : fail-in-pre  AND pass-in-post
    P2P   : pass-in-pre  AND pass-in-post
    NEW_FAIL: pass-in-pre AND (fail-or-missing in post)
    """
    def index(report):
        return {t["path"]: t["status"] for t in report.get("tests", [])}

    pre_idx = index(pre)
    post_idx = index(post)

    f2p, p2p, new_fail = [], [], []
    for path, pre_status in pre_idx.items():
        post_status = post_idx.get(path)
        if pre_status == "fail":
            if post_status == "pass":
                f2p.append(path)
        elif pre_status == "pass":
            if post_status == "pass":
                p2p.append(path)
            else:
                new_fail.append(path)
    return f2p, p2p, new_fail


def stable_status(reports: list) -> dict:
    """Collapse K repeated run reports into one status per test path.

    A path is `pass`/`fail` only if it has that status in *every* run; any
    disagreement (including being missing from some runs) marks it `flaky`.
    With a single run this is just that run's status (no path is ever flaky),
    so K=1 reproduces the non-flaky pipeline exactly.
    """
    paths = set()
    indexed = []
    for r in reports:
        idx = {t["path"]: t["status"] for t in r.get("tests", [])}
        indexed.append(idx)
        paths.update(idx.keys())

    out = {}
    for path in paths:
        statuses = {idx.get(path, "missing") for idx in indexed}
        if statuses == {"pass"}:
            out[path] = "pass"
        elif statuses == {"fail"}:
            out[path] = "fail"
        else:
            out[path] = "flaky"
    return out


def diff_reports_stable(pre_reports: list, post_reports: list) -> tuple[list, list, list]:
    """Flaky-aware (F2P, P2P, NEW_FAIL): paths flaky in pre OR post are excluded
    from all three sets (scaling plan G4). Reduces to diff_reports when K=1."""
    pre_idx = stable_status(pre_reports)
    post_idx = stable_status(post_reports)

    f2p, p2p, new_fail = [], [], []
    for path, pre_s in pre_idx.items():
        if pre_s == "flaky":
            continue
        post_s = post_idx.get(path, "missing")
        if post_s == "flaky":
            continue
        if pre_s == "fail":
            if post_s == "pass":
                f2p.append(path)
        elif pre_s == "pass":
            if post_s == "pass":
                p2p.append(path)
            else:
                new_fail.append(path)
    return f2p, p2p, new_fail


def check_resolved(report: dict, f2p: list, p2p: list) -> bool:
    """Return True iff every F2P path passes and every P2P path passes."""
    idx = {t["path"]: t["status"] for t in report.get("tests", [])}
    for path in f2p:
        if idx.get(path) != "pass":
            return False
    for path in p2p:
        if idx.get(path) != "pass":
            return False
    return True


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def run(cmd: list, *, cwd=None, timeout=None, log_path=None, env=None):
    """Run cmd; return (returncode, stdout_text, stderr_text).

    stdout/stderr captured to strings and optionally appended to log_path.
    """
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    out = result.stdout.decode(errors="replace")
    err = result.stderr.decode(errors="replace")
    if log_path:
        pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as fh:
            fh.write(f"=== CMD: {' '.join(str(c) for c in cmd)}\n")
            fh.write(f"=== RC: {result.returncode}\n")
            if out:
                fh.write("--- stdout ---\n" + out)
            if err:
                fh.write("--- stderr ---\n" + err)
    return result.returncode, out, err


def julia_version(julia: str) -> str:
    try:
        rc, out, _ = run([julia, "--version"])
        return out.strip() if rc == 0 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Repo / worktree management
# ---------------------------------------------------------------------------


def repo_dir(work: pathlib.Path, repo: str) -> pathlib.Path:
    safe = repo.replace("/", "__")
    return work / "repos" / safe


def wt_dir(work: pathlib.Path, instance_id: str, tag: str) -> pathlib.Path:
    return work / "wt" / f"{instance_id}.{tag}"


def env_dir(work: pathlib.Path, instance_id: str) -> pathlib.Path:
    return work / "envs" / instance_id


def log_dir(work: pathlib.Path, instance_id: str) -> pathlib.Path:
    d = work / "logs" / instance_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def repo_flock(work: pathlib.Path, repo: str):
    """Serialize clone/fetch/worktree mutations on one repo's shared clone across
    parallel validator processes (the test runs themselves stay unlocked, since
    each instance has its own worktree + env). A no-op bottleneck when --jobs=1."""
    safe = repo.replace("/", "__")
    lock_root = work / "repos"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f".{safe}.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def ensure_clone(repo_url: str, clone: pathlib.Path, commits: list, log: pathlib.Path):
    """Clone bare-ish repo if absent; fetch if any commit is missing."""
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        rc, _, err = run(["git", "clone", "--filter=blob:none", repo_url, str(clone)],
                         log_path=str(log / "clone.log"))
        if rc != 0:
            raise RuntimeError(f"clone failed: {err[:300]}")

    missing = []
    for sha in commits:
        rc, _, _ = run(["git", "-C", str(clone), "cat-file", "-e", f"{sha}^{{commit}}"])
        if rc != 0:
            missing.append(sha)

    if missing:
        rc, _, err = run(["git", "-C", str(clone), "fetch", "--quiet", "--tags",
                          "origin", "+refs/heads/*:refs/heads/*"],
                         log_path=str(log / "fetch.log"))
        if rc != 0:
            run(["git", "-C", str(clone), "fetch", "--unshallow"],
                log_path=str(log / "fetch_unshallow.log"))


def remove_worktree(clone: pathlib.Path, wt: pathlib.Path, log: pathlib.Path):
    if wt.exists():
        run(["git", "-C", str(clone), "worktree", "remove", "--force", str(wt)],
            log_path=str(log / "wt_remove.log"))
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)


def create_worktree(clone: pathlib.Path, wt: pathlib.Path, sha: str,
                    log: pathlib.Path) -> bool:
    wt.parent.mkdir(parents=True, exist_ok=True)
    remove_worktree(clone, wt, log)
    run(["git", "-C", str(clone), "worktree", "prune"],
        log_path=str(log / "wt_prune.log"))
    rc, _, err = run(
        ["git", "-C", str(clone), "worktree", "add", "--detach", str(wt), sha],
        log_path=str(log / "wt_add.log"),
    )
    return rc == 0


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def apply_patch(patch_text: str, wt: pathlib.Path, log: pathlib.Path,
                label: str) -> bool:
    """Try three methods; return True on first success."""
    with tempfile.NamedTemporaryFile(suffix=".patch", delete=False,
                                    mode="w", encoding="utf-8") as f:
        f.write(patch_text)
        patch_file = f.name

    lp = str(log / f"{label}.log")
    try:
        rc, _, _ = run(
            ["git", "-C", str(wt), "apply", "--whitespace=nowarn", patch_file],
            log_path=lp,
        )
        if rc == 0:
            return True

        rc, _, _ = run(
            ["git", "-C", str(wt), "apply", "--3way", patch_file],
            log_path=lp,
        )
        if rc == 0:
            return True

        rc, _, _ = run(
            ["patch", "-d", str(wt), "-p1", "-f", "--fuzz=3", "-i", patch_file],
            log_path=lp,
        )
        return rc == 0
    finally:
        os.unlink(patch_file)


# ---------------------------------------------------------------------------
# Julia script runners
# ---------------------------------------------------------------------------


def build_env(julia: str, wt: pathlib.Path, env: pathlib.Path,
              timeout: int, log: pathlib.Path) -> bool:
    script = str(HARNESS_DIR / "testenv.jl")
    rc, _, _ = run(
        [julia, "--startup-file=no", script, str(wt), str(env)],
        timeout=timeout,
        log_path=str(log / "env.log"),
    )
    return rc == 0


def run_tests(julia: str, env: pathlib.Path, wt: pathlib.Path,
              out_json: pathlib.Path, timeout: int,
              log: pathlib.Path, label: str) -> tuple[dict | None, float]:
    script = str(HARNESS_DIR / "run_tests.jl")
    t0 = time.monotonic()
    rc, _, _ = run(
        [julia, "--startup-file=no", f"--project={env}", script, str(wt), str(out_json)],
        timeout=timeout,
        log_path=str(log / f"{label}.out"),
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    if not out_json.exists():
        return None, elapsed_ms
    try:
        with open(out_json) as fh:
            return json.load(fh), elapsed_ms
    except Exception:
        return None, elapsed_ms


def run_tests_k(julia: str, env: pathlib.Path, wt: pathlib.Path,
                out_json: pathlib.Path, timeout: int, log: pathlib.Path,
                label: str, k: int) -> tuple[list, float]:
    """Run the suite K times; return (list-of-reports, total_elapsed_ms).

    A run that produces no parseable report contributes None (caller decides how
    strict to be). Each repeat writes a distinct JSON so nothing is clobbered.
    """
    reports = []
    total_ms = 0.0
    for i in range(k):
        oj = out_json if k == 1 else out_json.with_name(f"{out_json.stem}_{i}{out_json.suffix}")
        lbl = label if k == 1 else f"{label}{i}"
        report, ms = run_tests(julia, env, wt, oj, timeout, log, lbl)
        total_ms += ms
        reports.append(report)
    return reports, total_ms


# ---------------------------------------------------------------------------
# validate subcommand
# ---------------------------------------------------------------------------


def cmd_validate(args):
    # Resolve to absolute: `git -C <clone> worktree add <path>` resolves a relative
    # <path> against the clone dir, not the caller's cwd, which silently nests the
    # worktree inside the clone and breaks later `git -C <path>` calls.
    work = pathlib.Path(args.work).resolve()
    julia = args.julia
    timeout = args.timeout
    flaky_runs = max(1, getattr(args, "flaky_runs", 1))
    gc_env = getattr(args, "gc_env", False)
    jobs = max(1, getattr(args, "jobs", 1))
    resume = getattr(args, "resume", False)

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logs_root = work / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    summary_log = logs_root / "validate_summary.jsonl"
    validated_cache = logs_root / "validated.jsonl"  # durable enriched valids (resume artifact)

    jver = julia_version(julia)

    candidates = []
    with open(args.candidates) as fh:
        for line in fh:
            line = line.strip()
            if line:
                candidates.append(json.loads(line))
    if args.limit:
        candidates = candidates[: args.limit]

    # resume: skip instances already recorded in the summary
    done = {}
    if resume and summary_log.exists():
        for line in open(summary_log):
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[rec["instance_id"]] = rec["verdict"]
    todo = [c for c in candidates if c["instance_id"] not in done]
    if resume:
        print(f"[validate] resume: {len(candidates) - len(todo)} already done, "
              f"{len(todo)} to run (jobs={jobs}, flaky={flaky_runs})", file=sys.stderr)

    opts = {"work": str(work), "julia": julia, "timeout": timeout,
            "keep_wt": args.keep_worktrees, "jver": jver,
            "flaky_runs": flaky_runs, "gc_env": gc_env}

    valid_now = {iid for iid, v in done.items() if v == "valid"}

    def record(res):
        iid, verdict, n_f2p, n_p2p, pre_ms, post_ms, inst = res
        with open(summary_log, "a") as sl:
            sl.write(json.dumps({
                "instance_id": iid, "verdict": verdict,
                "n_f2p": n_f2p, "n_p2p": n_p2p,
                "pre_ms": pre_ms, "post_ms": post_ms,
            }) + "\n")
        if verdict == "valid" and inst is not None:
            valid_now.add(iid)
            with open(validated_cache, "a") as vc:
                vc.write(json.dumps(inst) + "\n")
        else:
            valid_now.discard(iid)  # a re-run that regressed must not stay "valid"
        print(f"[validate] {iid}: {verdict} f2p={n_f2p} p2p={n_p2p}", file=sys.stderr)

    if jobs > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_validate_worker, c, opts): c["instance_id"] for c in todo}
            for fut in as_completed(futs):
                try:
                    record(fut.result())
                except Exception as exc:
                    iid = futs[fut]
                    print(f"[validate] {iid}: worker crashed: {exc}", file=sys.stderr)
                    record((iid, "error", 0, 0, 0.0, 0.0, None))
    else:
        for c in todo:
            record(_validate_worker(c, opts))

    # assemble out: the latest enriched record for every currently-valid candidate,
    # in candidate order, deduped (last write wins).
    emit_order = [c["instance_id"] for c in candidates if c["instance_id"] in valid_now]
    n = _assemble_out(validated_cache, emit_order, out_path)
    print(f"\n[validate] done: {n} valid instances -> {out_path}", file=sys.stderr)


def _validate_worker(inst: dict, opts: dict) -> tuple:
    """Top-level (picklable) entry point for the process pool. Returns a tuple
    carrying the enriched instance so the parent can assemble the output even
    when validation ran in a separate process."""
    work = pathlib.Path(opts["work"])
    verdict, n_f2p, n_p2p, pre_ms, post_ms, enriched = _validate_one(
        inst, work, opts["julia"], opts["timeout"], opts["keep_wt"], opts["jver"],
        flaky_runs=opts["flaky_runs"], gc_env=opts["gc_env"],
    )
    return (inst["instance_id"], verdict, n_f2p, n_p2p, pre_ms, post_ms, enriched)


def _assemble_out(cache_path: pathlib.Path, emit_order: list, out_path: pathlib.Path) -> int:
    latest = {}
    if cache_path.exists():
        for line in open(cache_path):
            line = line.strip()
            if line:
                rec = json.loads(line)
                latest[rec["instance_id"]] = rec
    n, missing = 0, []
    with open(out_path, "w") as fh:
        for iid in emit_order:
            rec = latest.get(iid)
            if rec is None:
                missing.append(iid)
                continue
            fh.write(json.dumps(rec) + "\n")
            n += 1
    if missing:
        print(f"[validate] WARN: {len(missing)} valid ids missing from "
              f"{cache_path.name} (deleted?): {missing[:5]}", file=sys.stderr)
    return n


def _validate_one(inst: dict, work: pathlib.Path, julia: str, timeout: int,
                  keep_wt: bool, jver: str, *, flaky_runs: int = 1,
                  gc_env: bool = False) -> tuple[str, int, int, float, float, dict | None]:
    iid = inst["instance_id"]
    repo = inst["repo"]
    repo_url = inst["repo_url"]
    base_sha = inst["base_commit"]
    fix_sha = inst["fix_commit"]
    test_patch = inst.get("test_patch", "")
    gold_patch = inst.get("patch", "")

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "base")
    env = env_dir(work, iid)

    n_f2p = n_p2p = 0
    pre_ms = post_ms = 0.0

    # clone/fetch + worktree creation mutate the shared per-repo clone: serialize
    # them across parallel workers. The build/test phase below runs lock-free.
    try:
        with repo_flock(work, repo):
            ensure_clone(repo_url, clone, [base_sha, fix_sha], logs)
    except Exception as exc:
        _log_err(logs, "clone_error.log", str(exc))
        return "clone_failed", n_f2p, n_p2p, pre_ms, post_ms, None

    with repo_flock(work, repo):
        wt_ok = create_worktree(clone, wt, base_sha, logs)
    if not wt_ok:
        return "worktree_failed", n_f2p, n_p2p, pre_ms, post_ms, None

    def finish(verdict, inst_out=None):
        if not keep_wt:
            with repo_flock(work, repo):
                remove_worktree(clone, wt, logs)
        if gc_env:
            _cleanup_env(env, logs)
        return verdict, n_f2p, n_p2p, pre_ms, post_ms, inst_out

    try:
        if test_patch and not apply_patch(test_patch, wt, logs, "test_patch"):
            return finish("test_patch_failed")

        env.mkdir(parents=True, exist_ok=True)
        if not build_env(julia, wt, env, timeout, logs):
            return finish("env_failed")

        pre_json = logs / "report_pre.json"
        try:
            pre_reports, pre_ms = run_tests_k(julia, env, wt, pre_json, timeout, logs, "pre", flaky_runs)
        except subprocess.TimeoutExpired:
            return finish("timeout_pre")

        if any(r is None or not r.get("ok", False) for r in pre_reports):
            return finish("broken_at_base")

        if gold_patch and not apply_patch(gold_patch, wt, logs, "gold_patch"):
            return finish("gold_patch_failed")

        post_json = logs / "report_post.json"
        try:
            post_reports, post_ms = run_tests_k(julia, env, wt, post_json, timeout, logs, "post", flaky_runs)
        except subprocess.TimeoutExpired:
            return finish("timeout_post")

        if any(r is None or not r.get("ok", False) for r in post_reports):
            return finish("gold_broken")

        f2p, p2p, new_fail = diff_reports_stable(pre_reports, post_reports)
        n_f2p = len(f2p)
        n_p2p = len(p2p)

        if n_f2p < 1:
            return finish("no_fail_to_pass")
        if len(new_fail) > 0:
            return finish("regression")

        inst["FAIL_TO_PASS"] = f2p
        inst["PASS_TO_PASS"] = p2p
        inst["julia_version"] = jver
        if flaky_runs > 1:
            inst["flaky_runs"] = flaky_runs
        return finish("valid", inst)

    except Exception as exc:
        _log_err(logs, "unexpected.log", str(exc))
        return finish("error")


def _cleanup_env(env: pathlib.Path, logs: pathlib.Path):
    """Drop the per-instance env to bound disk, keeping the resolved Manifest as
    a reproducibility artifact (scaling plan P4 GC)."""
    try:
        manifest = env / "Manifest.toml"
        if manifest.exists():
            shutil.copy2(manifest, logs / "Manifest.resolved.toml")
    except Exception:
        pass
    shutil.rmtree(env, ignore_errors=True)


# ---------------------------------------------------------------------------
# eval subcommand
# ---------------------------------------------------------------------------


def cmd_eval(args):
    work = pathlib.Path(args.work)
    julia = args.julia
    timeout = args.timeout
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    instances = _load_jsonl_by_id(args.instances)
    predictions = _load_jsonl_by_id(args.predictions)

    results = []
    n_resolved = 0
    ids = list(instances.keys())

    for iid in ids:
        inst = instances[iid]
        pred = predictions.get(iid)
        if pred is None:
            continue
        rec = _eval_one(inst, pred.get("model_patch", ""), work, julia, timeout,
                        args.keep_worktrees)
        results.append(rec)
        if rec["resolved"]:
            n_resolved += 1
        print(f"[eval] {iid}: {rec['status']} f2p={rec['f2p_pass']}/{rec['f2p_total']}", file=sys.stdout)

    total = len(results)
    rate = n_resolved / total if total else 0.0
    report = {
        "total": total,
        "resolved": n_resolved,
        "resolve_rate": rate,
        "instances": results,
    }
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print(f"\n[eval] resolved {n_resolved}/{total} ({rate:.1%})")


def _eval_one(inst: dict, model_patch: str, work: pathlib.Path,
              julia: str, timeout: int, keep_wt: bool) -> dict:
    iid = inst["instance_id"]
    repo = inst["repo"]
    repo_url = inst["repo_url"]
    base_sha = inst["base_commit"]
    test_patch = inst.get("test_patch", "")
    f2p = inst.get("FAIL_TO_PASS", [])
    p2p = inst.get("PASS_TO_PASS", [])

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "pred")
    env = env_dir(work, iid)

    base_rec = {"instance_id": iid, "resolved": False, "f2p_pass": 0,
                "f2p_total": len(f2p), "p2p_ok": False, "status": "error",
                "check_ms": 0.0}

    try:
        ensure_clone(repo_url, clone, [base_sha], logs)
    except Exception as exc:
        _log_err(logs, "clone_error.log", str(exc))
        return base_rec

    if not create_worktree(clone, wt, base_sha, logs):
        return {**base_rec, "status": "error"}

    try:
        if test_patch and not apply_patch(test_patch, wt, logs, "test_patch_pred"):
            _cleanup_wt(clone, wt, logs, keep_wt)
            return {**base_rec, "status": "error"}

        if model_patch and not apply_patch(model_patch, wt, logs, "model_patch"):
            _cleanup_wt(clone, wt, logs, keep_wt)
            return {**base_rec, "status": "patch_apply_failed"}

        # always rebuild: the env's Manifest dev-paths the worktree it was
        # built against, which may no longer exist (deps stay depot-cached)
        env.mkdir(parents=True, exist_ok=True)
        if not build_env(julia, wt, env, timeout, logs):
            _cleanup_wt(clone, wt, logs, keep_wt)
            return {**base_rec, "status": "error"}

        out_json = logs / "report_pred.json"
        try:
            report, check_ms = run_tests(julia, env, wt, out_json, timeout, logs, "pred")
        except subprocess.TimeoutExpired:
            _cleanup_wt(clone, wt, logs, keep_wt)
            return {**base_rec, "status": "timeout", "check_ms": timeout * 1000.0}

        if report is None:
            _cleanup_wt(clone, wt, logs, keep_wt)
            return {**base_rec, "status": "error", "check_ms": check_ms}

        idx = {t["path"]: t["status"] for t in report.get("tests", [])}
        f2p_pass = sum(1 for p in f2p if idx.get(p) == "pass")
        p2p_ok = all(idx.get(p) == "pass" for p in p2p)
        resolved = check_resolved(report, f2p, p2p)
        status = "resolved" if resolved else "unresolved"

        _cleanup_wt(clone, wt, logs, keep_wt)
        return {
            "instance_id": iid,
            "resolved": resolved,
            "f2p_pass": f2p_pass,
            "f2p_total": len(f2p),
            "p2p_ok": p2p_ok,
            "status": status,
            "check_ms": check_ms,
        }

    except Exception as exc:
        _log_err(logs, "unexpected.log", str(exc))
        _cleanup_wt(clone, wt, logs, keep_wt)
        return {**base_rec, "status": "error"}


# ---------------------------------------------------------------------------
# run-one subcommand
# ---------------------------------------------------------------------------


def cmd_run_one(args):
    work = pathlib.Path(args.work)
    julia = args.julia
    timeout = args.timeout

    instances = _load_jsonl_by_id(args.instances)
    inst = instances.get(args.instance_id)
    if inst is None:
        print(f"instance_id not found: {args.instance_id}", file=sys.stderr)
        sys.exit(1)

    iid = inst["instance_id"]
    repo = inst["repo"]
    repo_url = inst["repo_url"]
    base_sha = inst["base_commit"]
    test_patch = inst.get("test_patch", "")
    f2p = inst.get("FAIL_TO_PASS", [])
    p2p = inst.get("PASS_TO_PASS", [])

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "runone")
    env = env_dir(work, iid)

    ensure_clone(repo_url, clone, [base_sha], logs)
    if not create_worktree(clone, wt, base_sha, logs):
        print("worktree creation failed", file=sys.stderr)
        sys.exit(1)

    if test_patch and not apply_patch(test_patch, wt, logs, "test_patch_ro"):
        print("test_patch application failed", file=sys.stderr)
        sys.exit(1)

    if args.gold:
        gold_patch = inst.get("patch", "")
        if gold_patch and not apply_patch(gold_patch, wt, logs, "gold_patch_ro"):
            print("gold patch application failed", file=sys.stderr)
            sys.exit(1)
    elif args.model_patch:
        with open(args.model_patch) as fh:
            mp = fh.read()
        if mp and not apply_patch(mp, wt, logs, "model_patch_ro"):
            print("model patch application failed", file=sys.stderr)
            sys.exit(1)

    # always rebuild: the env Manifest dev-paths whichever worktree built it
    env.mkdir(parents=True, exist_ok=True)
    if not build_env(julia, wt, env, timeout, logs):
        print("env build failed", file=sys.stderr)
        sys.exit(1)

    out_json = logs / "report_runone.json"
    try:
        report, check_ms = run_tests(julia, env, wt, out_json, timeout, logs, "runone")
    except subprocess.TimeoutExpired:
        print("timeout", file=sys.stderr)
        sys.exit(1)

    print(f"report: {out_json}")
    print(f"worktree: {wt}")
    if report is None:
        print("run_tests produced no JSON")
        sys.exit(1)

    ok = report.get("ok", False)
    tests = report.get("tests", [])
    n_pass = sum(1 for t in tests if t["status"] == "pass")
    n_fail = sum(1 for t in tests if t["status"] == "fail")
    print(f"ok={ok}  pass={n_pass}  fail={n_fail}  elapsed_s={report.get('elapsed_s', 0):.2f}")

    if f2p or p2p:
        resolved = check_resolved(report, f2p, p2p)
        idx = {t["path"]: t["status"] for t in tests}
        f2p_pass = sum(1 for p in f2p if idx.get(p) == "pass")
        print(f"resolved={resolved}  f2p={f2p_pass}/{len(f2p)}  p2p_ok={all(idx.get(p)=='pass' for p in p2p)}")


# ---------------------------------------------------------------------------
# --self-test hidden subcommand
# ---------------------------------------------------------------------------


def cmd_self_test(_args):
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"FAIL {name}: got {got!r}, want {want!r}")
        else:
            print(f"  ok  {name}")

    print("=== self-test: diff_reports ===")

    pre = {"ok": True, "tests": [
        {"path": "a/b/test1", "status": "fail"},
        {"path": "a/b/test2", "status": "pass"},
        {"path": "a/b/test3", "status": "fail"},
        {"path": "a/b/test4", "status": "pass"},
    ]}
    post = {"ok": True, "tests": [
        {"path": "a/b/test1", "status": "pass"},   # F2P
        {"path": "a/b/test2", "status": "pass"},   # P2P
        {"path": "a/b/test3", "status": "fail"},   # still fail
        # test4 missing -> NEW_FAIL
    ]}

    f2p, p2p, new_fail = diff_reports(pre, post)
    check("f2p", sorted(f2p), ["a/b/test1"])
    check("p2p", sorted(p2p), ["a/b/test2"])
    check("new_fail", sorted(new_fail), ["a/b/test4"])

    pre2 = {"ok": True, "tests": [
        {"path": "x", "status": "fail"},
        {"path": "y", "status": "fail"},
    ]}
    post2 = {"ok": True, "tests": [
        {"path": "x", "status": "pass"},
        {"path": "y", "status": "pass"},
    ]}
    f2p2, p2p2, nf2 = diff_reports(pre2, post2)
    check("all_f2p", sorted(f2p2), ["x", "y"])
    check("no_p2p", p2p2, [])
    check("no_new_fail", nf2, [])

    print("\n=== self-test: stable_status / diff_reports_stable (flaky-K) ===")

    # single run: reduces to plain status, never flaky
    check("stable_k1", stable_status([{"tests": [
        {"path": "a", "status": "fail"}, {"path": "b", "status": "pass"}]}]),
        {"a": "fail", "b": "pass"})

    # 3 consistent runs -> stable; 1 disagreement -> flaky; missing-in-one -> flaky
    runs = [
        {"tests": [{"path": "a", "status": "fail"}, {"path": "b", "status": "pass"},
                   {"path": "c", "status": "pass"}, {"path": "d", "status": "pass"}]},
        {"tests": [{"path": "a", "status": "fail"}, {"path": "b", "status": "pass"},
                   {"path": "c", "status": "fail"}]},  # c flips, d missing
        {"tests": [{"path": "a", "status": "fail"}, {"path": "b", "status": "pass"},
                   {"path": "c", "status": "pass"}, {"path": "d", "status": "pass"}]},
    ]
    ss = stable_status(runs)
    check("stable_fail", ss["a"], "fail")
    check("stable_pass", ss["b"], "pass")
    check("stable_flaky_flip", ss["c"], "flaky")
    check("stable_flaky_missing", ss["d"], "flaky")

    # diff_reports_stable with K=1 == diff_reports
    f2p_s, p2p_s, nf_s = diff_reports_stable([pre], [post])
    check("stable_eq_plain_f2p", sorted(f2p_s), ["a/b/test1"])
    check("stable_eq_plain_p2p", sorted(p2p_s), ["a/b/test2"])
    check("stable_eq_plain_newfail", sorted(nf_s), ["a/b/test4"])

    # a path flaky in post is excluded from F2P (not counted as a fix)
    pre_k = [{"tests": [{"path": "x", "status": "fail"}]},
             {"tests": [{"path": "x", "status": "fail"}]}]
    post_k = [{"tests": [{"path": "x", "status": "pass"}]},
              {"tests": [{"path": "x", "status": "fail"}]}]  # flaky in post
    f2p_f, _, _ = diff_reports_stable(pre_k, post_k)
    check("flaky_post_excluded", f2p_f, [])

    print("\n=== self-test: check_resolved ===")

    report_ok = {"ok": True, "tests": [
        {"path": "a/b/test1", "status": "pass"},
        {"path": "a/b/test2", "status": "pass"},
    ]}
    check("resolved_true", check_resolved(report_ok, ["a/b/test1"], ["a/b/test2"]), True)

    report_bad = {"ok": True, "tests": [
        {"path": "a/b/test1", "status": "fail"},
        {"path": "a/b/test2", "status": "pass"},
    ]}
    check("resolved_false_f2p_fail", check_resolved(report_bad, ["a/b/test1"], ["a/b/test2"]), False)

    report_missing = {"ok": True, "tests": [
        {"path": "a/b/test2", "status": "pass"},
    ]}
    check("resolved_false_f2p_missing", check_resolved(report_missing, ["a/b/test1"], ["a/b/test2"]), False)

    report_p2p_bad = {"ok": True, "tests": [
        {"path": "a/b/test1", "status": "pass"},
        {"path": "a/b/test2", "status": "fail"},
    ]}
    check("resolved_false_p2p_fail", check_resolved(report_p2p_bad, ["a/b/test1"], ["a/b/test2"]), False)

    check("empty_f2p_p2p", check_resolved(report_ok, [], []), True)

    print()
    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        sys.exit(1)
    else:
        print("all self-tests passed")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _load_jsonl_by_id(path: str) -> dict:
    result = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                obj = json.loads(line)
                result[obj["instance_id"]] = obj
    return result


def _log_err(log: pathlib.Path, filename: str, msg: str):
    log.mkdir(parents=True, exist_ok=True)
    with open(log / filename, "a") as fh:
        fh.write(msg + "\n")


def _cleanup_wt(clone: pathlib.Path, wt: pathlib.Path, log: pathlib.Path, keep: bool):
    if not keep:
        remove_worktree(clone, wt, log)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swebench_eval.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--julia", default=DEFAULT_JULIA, help="Julia binary path")
    p.add_argument("--work", default=str(DEFAULT_WORK), help="Work root directory")

    sub = p.add_subparsers(dest="subcommand", required=False)

    # validate
    vp = sub.add_parser("validate", help="Validate candidates and compute F2P/P2P")
    vp.add_argument("candidates", help="Input JSONL of candidate instances")
    vp.add_argument("--out", required=True, help="Output JSONL for valid instances")
    vp.add_argument("--timeout", type=int, default=900, help="Test run timeout (s)")
    vp.add_argument("--keep-worktrees", action="store_true")
    vp.add_argument("--limit", type=int, default=0, help="Process at most N candidates")
    vp.add_argument("--jobs", type=int, default=1,
                    help="Validate N candidates in parallel (process pool)")
    vp.add_argument("--resume", action="store_true",
                    help="Skip candidates already in validate_summary.jsonl; rebuild --out from cache")
    vp.add_argument("--flaky-runs", type=int, default=1, metavar="K",
                    help="Run pre/post K times; drop tests inconsistent across runs (G4)")
    vp.add_argument("--gc-env", action="store_true",
                    help="Delete each per-instance env after validation (keep resolved Manifest)")

    # eval
    ep = sub.add_parser("eval", help="Evaluate model predictions")
    ep.add_argument("instances", help="Validated instances JSONL")
    ep.add_argument("predictions", help="Predictions JSONL (instance_id + model_patch)")
    ep.add_argument("--out", required=True, help="Output report JSON")
    ep.add_argument("--timeout", type=int, default=900)
    ep.add_argument("--keep-worktrees", action="store_true")

    # run-one
    ro = sub.add_parser("run-one", help="Debug: run one instance")
    ro.add_argument("instances", help="Instances JSONL")
    ro.add_argument("instance_id", help="Instance ID to run")
    ro.add_argument("--gold", action="store_true", help="Apply gold patch")
    ro.add_argument("--model-patch", metavar="FILE", help="Apply model patch from file")
    ro.add_argument("--timeout", type=int, default=900)

    # --self-test (hidden)
    sub.add_parser("--self-test", help=argparse.SUPPRESS)

    return p


def main():
    if "--self-test" in sys.argv:
        cmd_self_test(None)
        return

    p = build_parser()
    args = p.parse_args()

    dispatch = {
        "validate": cmd_validate,
        "eval": cmd_eval,
        "run-one": cmd_run_one,
        "--self-test": cmd_self_test,
    }

    if args.subcommand is None:
        p.print_help()
        sys.exit(0)

    fn = dispatch.get(args.subcommand)
    if fn is None:
        p.print_help()
        sys.exit(1)

    fn(args)


if __name__ == "__main__":
    main()
