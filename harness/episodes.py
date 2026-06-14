"""
episodes.py -- live-episode + replay runner for SWE-bench-jl.

Subcommands
-----------
prepare <instances.jsonl> <instance_id> --mode <cold|daemon|kiln> --dest DIR
    Clone repo, apply test_patch, build env, write ./check shim.

check-impl <DIR>
    Run one check against the current workspace state; append telemetry.

stop <DIR>
    Send QUIT to resident server; kill pid-file process if alive.

replay <instances.jsonl> <instance_id> --attempts-dir DIR --modes cold,daemon,kiln
       --dest-root DIR2 --out replay.jsonl
    Replay recorded attempt patches across one or more modes.

summarize --telemetry <file-or-glob> [--replay replay.jsonl]
    Print a compact per-(instance,mode) table from telemetry files.

Usage examples
--------------
    python3 episodes.py prepare /tmp/one_inst.jsonl JuliaCollections__OrderedCollections-151 \\
        --mode cold --dest work/eval/ws1
    # then inside ws1:
    ./check
    python3 episodes.py stop work/eval/ws1
    python3 episodes.py summarize --telemetry work/eval/ws1/.kilnswe/telemetry.jsonl
"""

import argparse
import glob
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

JULIA = os.environ.get("JULIA_BIN", "julia")
HERE = os.path.dirname(os.path.abspath(__file__))
SWE_ROOT = os.path.normpath(os.path.join(HERE, ".."))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_instance(jsonl_path, instance_id):
    with open(jsonl_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj["instance_id"] == instance_id:
                return obj
    return None


def _repo_clone_path(swe_root, repo):
    safe = repo.replace("/", "__")
    if safe.endswith(".jl"):
        safe = safe[:-3]
    return os.path.join(swe_root, "work", "repos", safe)


def _run(cmd, cwd=None, timeout=None, log_path=None):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
    )
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "ab") as fh:
            fh.write(b"=== CMD: " + b" ".join(c.encode() if isinstance(c, str) else c for c in cmd) + b"\n")
            fh.write(b"=== RC: " + str(result.returncode).encode() + b"\n")
            if result.stdout:
                fh.write(b"--- stdout ---\n" + result.stdout)
            if result.stderr:
                fh.write(b"--- stderr ---\n" + result.stderr)
    return result


def _apply_patch_text(patch_text, repo_dir, log_path=None):
    fd, tmp = tempfile.mkstemp(suffix=".patch")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(patch_text)
        r = _run(["git", "-C", repo_dir, "apply", tmp], log_path=log_path)
        if r.returncode != 0:
            raise RuntimeError(
                "git apply failed: " + (r.stderr or r.stdout or b"").decode(errors="replace")[:500]
            )
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# socket client helpers
# ---------------------------------------------------------------------------

def _sock_path_for(kilnswe_dir):
    # UNIX socket paths are capped at ~108 bytes; deep workspaces exceed it,
    # so sockets live in /tmp keyed by a digest of the workspace path
    import hashlib
    h = hashlib.sha1(os.path.abspath(kilnswe_dir).encode()).hexdigest()[:12]
    return os.path.join("/tmp", "kilnswe-%s.sock" % h)


def _sock_ping(sock_path, timeout=2.0):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        sf = s.makefile("rw")
        sf.write("PING\n")
        sf.flush()
        line = sf.readline()
        s.close()
        return line.strip() == "PONG"
    except Exception:
        return False


def _sock_send_run(sock_path, out_json, timeout=1200.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    sf = s.makefile("rw")
    sf.write(f"RUN\t{out_json}\n")
    sf.flush()
    line = sf.readline()
    s.close()
    return line.strip()


def _sock_quit(sock_path):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(sock_path)
        sf = s.makefile("rw")
        sf.write("QUIT\n")
        sf.flush()
        sf.readline()
        s.close()
    except Exception:
        pass


def _kill_pidfile(pid_path):
    try:
        with open(pid_path) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _start_server(kilnswe_dir, julia, env_dir, repo_dir, instance_json, mode, sock_path, server_log):
    if mode == "kiln-rt":
        server_jl = os.path.join(SWE_ROOT, "baselines", "kiln_rt_server.jl")
        # Default assumes this repo is mounted as <kiln-repo>/benchmark/swebench, so
        # SWE_ROOT/../../src is the kiln repo's src/. Override with KILN_CHECK_SESSION.
        src_check = os.environ.get("KILN_CHECK_SESSION", os.path.normpath(
            os.path.join(SWE_ROOT, "..", "..", "src", "check_session.jl")))
        if not os.path.isfile(src_check):
            raise RuntimeError(
                "mode kiln-rt requires the kiln runtime at %s (src/ absent)" % src_check)
    else:
        server_jl = os.path.join(SWE_ROOT, "baselines", "kiln_server.jl")
    cmd = [
        julia, "--startup-file=no", f"--project={env_dir}",
        server_jl, repo_dir, instance_json, mode, sock_path,
    ]
    with open(server_log, "ab") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=log_fh,
            start_new_session=True,
        )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server process ended before READY")
        if b"READY" in line:
            return
    raise RuntimeError("server did not print READY within 300s")


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def cmd_prepare(args):
    jsonl = args.instances
    instance_id = args.instance_id
    mode = args.mode
    dest = os.path.abspath(args.dest)

    inst = _load_instance(jsonl, instance_id)
    if inst is None:
        print(f"instance not found: {instance_id}", file=sys.stderr)
        sys.exit(1)

    repo = inst["repo"]
    base_commit = inst["base_commit"]
    test_patch = inst.get("test_patch", "")

    src_clone = _repo_clone_path(SWE_ROOT, repo)
    if not os.path.isdir(src_clone):
        src_clone_jl = src_clone + ".jl"
        if os.path.isdir(src_clone_jl):
            src_clone = src_clone_jl
        else:
            print(f"local repo clone not found: {src_clone}", file=sys.stderr)
            sys.exit(1)

    if os.path.isdir(dest):
        import shutil
        shutil.rmtree(dest)

    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)

    r = _run(["git", "clone", "--quiet", src_clone, dest])
    if r.returncode != 0:
        print("git clone failed:", r.stderr.decode(errors="replace")[:300], file=sys.stderr)
        sys.exit(1)

    clone_log = os.path.join(dest, ".kilnswe", "git_clone.log")
    os.makedirs(os.path.dirname(clone_log), exist_ok=True)

    r = _run(["git", "-C", dest, "checkout", "--detach", base_commit],
             log_path=clone_log)
    if r.returncode != 0:
        print("git checkout failed:", r.stderr.decode(errors="replace")[:300], file=sys.stderr)
        sys.exit(1)

    if test_patch:
        _apply_patch_text(test_patch, dest,
                          log_path=os.path.join(dest, ".kilnswe", "test_patch.log"))

    r = _run(["git", "-C", dest,
              "-c", "user.name=swe", "-c", "user.email=swe@local",
              "add", "-A"],
             log_path=clone_log)
    if r.returncode != 0:
        print("git add failed:", r.stderr.decode(errors="replace")[:300], file=sys.stderr)
        sys.exit(1)

    r = _run(["git", "-C", dest,
              "-c", "user.name=swe", "-c", "user.email=swe@local",
              "commit", "-m", "baseline"],
             log_path=clone_log)
    if r.returncode != 0:
        nothing_to_commit = b"nothing to commit" in r.stdout or b"nothing to commit" in r.stderr
        if not nothing_to_commit:
            print("git commit failed:", r.stderr.decode(errors="replace")[:300], file=sys.stderr)
            sys.exit(1)

    kilnswe = os.path.join(dest, ".kilnswe")
    env_dir = os.path.join(kilnswe, "env")
    os.makedirs(kilnswe, exist_ok=True)

    env_log = os.path.join(kilnswe, "env.log")
    testenv_jl = os.path.join(HERE, "testenv.jl")
    r = _run([JULIA, "--startup-file=no", testenv_jl, dest, env_dir],
             log_path=env_log)
    if r.returncode != 0:
        print("testenv.jl failed; see", env_log, file=sys.stderr)
        sys.exit(1)

    if mode != "cold":
        revise_log = os.path.join(kilnswe, "revise.log")
        r = _run([
            JULIA, "--startup-file=no", "-e",
            f'using Pkg; Pkg.activate("{env_dir}"); Pkg.add("Revise"); Pkg.precompile()',
        ], log_path=revise_log)
        if r.returncode != 0:
            print("Revise add/precompile failed; see", revise_log, file=sys.stderr)
            sys.exit(1)

    instance_json = os.path.join(kilnswe, "instance.json")
    with open(instance_json, "w") as fh:
        json.dump(inst, fh)

    config = {"mode": mode, "julia": JULIA, "swe_root": SWE_ROOT}
    with open(os.path.join(kilnswe, "config.json"), "w") as fh:
        json.dump(config, fh)

    problem_md = os.path.join(dest, "PROBLEM.md")
    with open(problem_md, "w") as fh:
        fh.write("# Problem\n\n")
        fh.write(inst.get("problem_statement", ""))
        fh.write("\n\n# Rules\n\n")
        fh.write("- Your fix must live in `src/` (or `ext/`).\n")
        fh.write("- Run `./check` after each edit to verify.\n")
        fh.write("- NEVER edit files under `test/`.\n")
        fh.write("- Iterate until `./check` prints PASS.\n")
        fh.write("- At most 4 checks.\n")

    episodes_py = os.path.abspath(__file__)
    check_sh = os.path.join(dest, "check")
    with open(check_sh, "w") as fh:
        fh.write("#!/bin/bash\n")
        fh.write(f'exec python3 {episodes_py} check-impl "$(cd "$(dirname "$0")" && pwd)"\n')
    os.chmod(check_sh, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    os.makedirs(os.path.join(kilnswe, "attempts"), exist_ok=True)

    print(dest)


# ---------------------------------------------------------------------------
# run_check -- shared by check-impl and replay
# ---------------------------------------------------------------------------

def run_check(repo_dir, cfg, inst):
    """Execute one check; return (kind, check_ms, restart, report_dict)."""
    mode = cfg["mode"]
    julia = cfg.get("julia", JULIA)
    kilnswe = os.path.join(repo_dir, ".kilnswe")
    env_dir = os.path.join(kilnswe, "env")
    report_json = os.path.join(kilnswe, "report.json")
    instance_json = os.path.join(kilnswe, "instance.json")

    restart = False

    if mode == "cold":
        run_tests_jl = os.path.join(HERE, "run_tests.jl")
        cold_log = os.path.join(kilnswe, "cold_run.log")
        t0 = time.monotonic()
        _run(
            [julia, "--startup-file=no", f"--project={env_dir}",
             run_tests_jl, repo_dir, report_json],
            log_path=cold_log,
            timeout=900,
        )
        check_ms = (time.monotonic() - t0) * 1000.0
        kind = "cold"

    else:
        sock_path = _sock_path_for(kilnswe)
        server_log = os.path.join(kilnswe, "server.log")

        def ensure_server():
            alive = os.path.exists(sock_path) and _sock_ping(sock_path, timeout=2.0)
            if not alive:
                if os.path.exists(sock_path):
                    os.unlink(sock_path)
                _start_server(kilnswe, julia, env_dir, repo_dir,
                               instance_json, mode, sock_path, server_log)

        ensure_server()

        t0 = time.monotonic()
        reply = _sock_send_run(sock_path, report_json, timeout=1200.0)
        restart = False

        if reply.startswith("RESTART\t"):
            restart = True
            pid_path = sock_path + ".pid"
            _kill_pidfile(pid_path)
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            _start_server(kilnswe, julia, env_dir, repo_dir,
                           instance_json, mode, sock_path, server_log)
            reply = _sock_send_run(sock_path, report_json, timeout=1200.0)

        check_ms = (time.monotonic() - t0) * 1000.0

        if reply.startswith("DONE\t"):
            parts = reply.split("\t")
            kind = parts[2] if len(parts) >= 3 else "full"
        else:
            kind = "full"

    report = None
    if os.path.exists(report_json):
        try:
            with open(report_json) as fh:
                report = json.load(fh)
        except Exception:
            pass

    return kind, check_ms, restart, report


# ---------------------------------------------------------------------------
# check-impl
# ---------------------------------------------------------------------------

def cmd_check_impl(args):
    repo_dir = os.path.abspath(args.dir)
    kilnswe = os.path.join(repo_dir, ".kilnswe")

    config_path = os.path.join(kilnswe, "config.json")
    instance_path = os.path.join(kilnswe, "instance.json")

    if not os.path.exists(config_path) or not os.path.exists(instance_path):
        print("Not a prepared workspace (missing .kilnswe/config.json or instance.json)",
              file=sys.stderr)
        sys.exit(1)

    with open(config_path) as fh:
        cfg = json.load(fh)
    with open(instance_path) as fh:
        inst = json.load(fh)

    _run(["git", "-C", repo_dir, "checkout", "--", "test/"])

    attempts_dir = os.path.join(kilnswe, "attempts")
    os.makedirs(attempts_dir, exist_ok=True)
    n = 1 + len([f for f in os.listdir(attempts_dir) if f.startswith("attempt_") and f.endswith(".patch")])

    patch_path = os.path.join(attempts_dir, f"attempt_{n}.patch")
    r = _run(["git", "-C", repo_dir, "diff", "HEAD", "--", "src", "ext"])
    with open(patch_path, "wb") as fh:
        fh.write(r.stdout)

    kind, check_ms, restart, report = run_check(repo_dir, cfg, inst)

    f2p = inst.get("FAIL_TO_PASS", [])
    p2p = inst.get("PASS_TO_PASS", [])
    needed = f2p + p2p

    if report is None:
        verdict = "fail"
        idx = {}
        failing = needed[:]
    elif report.get("partial"):
        verdict = "fail"
        idx = {t["path"]: t for t in report.get("tests", [])}
        failing = [p for p in needed if p in idx and idx[p]["status"] != "pass"]
    else:
        idx = {t["path"]: t for t in report.get("tests", [])}
        failing = [p for p in needed if idx.get(p, {}).get("status") != "pass"]
        verdict = "pass" if not failing else "fail"

    n_fail = len(failing)

    if verdict == "pass":
        print(f"PASS -- all target tests pass, no regressions (check {n}, {int(check_ms)} ms)")
    else:
        print(f"FAIL (check {n}): {n_fail} failing")
        for p in failing[:10]:
            node = idx.get(p)
            if node is None:
                print(f"  {p}  (did not run)")
            else:
                print(f"  {p}  fail={node.get('fail',0)} error={node.get('error',0)}")

    telem_path = os.path.join(kilnswe, "telemetry.jsonl")
    telem_row = {
        "attempt": n,
        "mode": cfg["mode"],
        "kind": kind,
        "check_ms": round(check_ms, 1),
        "restart": restart,
        "verdict": verdict,
        "n_fail": n_fail,
        "ts": time.time(),
    }
    with open(telem_path, "a") as fh:
        fh.write(json.dumps(telem_row) + "\n")


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

def cmd_stop(args):
    repo_dir = os.path.abspath(args.dir)
    kilnswe = os.path.join(repo_dir, ".kilnswe")
    sock_path = _sock_path_for(kilnswe)

    if os.path.exists(sock_path):
        _sock_quit(sock_path)
        pid_path = sock_path + ".pid"
        if os.path.exists(pid_path):
            _kill_pidfile(pid_path)
        try:
            os.unlink(sock_path)
        except Exception:
            pass

    print("stopped")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------

def cmd_replay(args):
    jsonl = args.instances
    instance_id = args.instance_id
    attempts_dir = os.path.abspath(args.attempts_dir)
    modes = [m.strip() for m in args.modes.split(",")]
    dest_root = os.path.abspath(args.dest_root)
    out_path = args.out

    inst = _load_instance(jsonl, instance_id)
    if inst is None:
        print(f"instance not found: {instance_id}", file=sys.stderr)
        sys.exit(1)

    patch_files = sorted(
        f for f in os.listdir(attempts_dir)
        if f.startswith("attempt_") and f.endswith(".patch")
    )

    def _patch_num(fname):
        try:
            return int(fname[len("attempt_"):-len(".patch")])
        except ValueError:
            return 0

    patch_files = sorted(patch_files, key=_patch_num)

    out_fh = open(out_path, "a") if out_path else None

    try:
        for mode in modes:
            ws = os.path.join(dest_root, f"{instance_id}__{mode}")

            class _PrepareArgs:
                instances = jsonl
                instance_id_val = instance_id
                mode_val = mode
                dest = ws

            prep_args = argparse.Namespace(
                instances=jsonl,
                instance_id=instance_id,
                mode=mode,
                dest=ws,
            )
            cmd_prepare(prep_args)

            kilnswe = os.path.join(ws, ".kilnswe")
            with open(os.path.join(kilnswe, "config.json")) as fh:
                cfg = json.load(fh)
            with open(os.path.join(kilnswe, "instance.json")) as fh:
                inst_local = json.load(fh)

            for pf in patch_files:
                k = _patch_num(pf)
                patch_path = os.path.join(attempts_dir, pf)

                _run(["git", "-C", ws, "checkout", "--", "src"])
                _run(["git", "-C", ws, "checkout", "--", "ext"], log_path=None)

                with open(patch_path, "rb") as fh:
                    patch_content = fh.read()

                if patch_content.strip():
                    fd, tmp = tempfile.mkstemp(suffix=".patch")
                    try:
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(patch_content)
                        _run(["git", "-C", ws, "apply", tmp])
                    finally:
                        os.unlink(tmp)

                kind, check_ms, restart, report = run_check(ws, cfg, inst_local)

                f2p = inst_local.get("FAIL_TO_PASS", [])
                p2p = inst_local.get("PASS_TO_PASS", [])
                needed = f2p + p2p

                if report is None or report.get("partial"):
                    verdict = "fail"
                    idx = {t["path"]: t for t in (report or {}).get("tests", [])}
                    failing = [p for p in needed if p in idx and idx[p]["status"] != "pass"]
                else:
                    idx = {t["path"]: t for t in report.get("tests", [])}
                    failing = [p for p in needed if idx.get(p, {}).get("status") != "pass"]
                    verdict = "pass" if not failing else "fail"

                row = {
                    "instance_id": instance_id,
                    "mode": mode,
                    "attempt": k,
                    "kind": kind,
                    "check_ms": round(check_ms, 1),
                    "restart": restart,
                    "verdict": verdict,
                    "n_fail": len(failing),
                }
                line = json.dumps(row)
                print(line, file=sys.stderr)
                if out_fh:
                    out_fh.write(line + "\n")
                    out_fh.flush()

            _stop_server_for_dir(ws)
    finally:
        if out_fh:
            out_fh.close()


def _stop_server_for_dir(repo_dir):
    kilnswe = os.path.join(repo_dir, ".kilnswe")
    sock_path = _sock_path_for(kilnswe)
    if os.path.exists(sock_path):
        _sock_quit(sock_path)
        pid_path = sock_path + ".pid"
        if os.path.exists(pid_path):
            _kill_pidfile(pid_path)
        try:
            os.unlink(sock_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def cmd_summarize(args):
    telem_rows = []
    for pattern in args.telemetry:
        for path in glob.glob(pattern):
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            telem_rows.append(json.loads(line))
                        except Exception:
                            pass

    replay_rows = []
    if args.replay:
        with open(args.replay) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        replay_rows.append(json.loads(line))
                    except Exception:
                        pass

    from collections import defaultdict

    groups = defaultdict(list)
    for r in telem_rows:
        inst_id = r.get("instance_id", "unknown")
        mode = r.get("mode", "?")
        groups[(inst_id, mode)].append(r)

    for r in replay_rows:
        inst_id = r.get("instance_id", "unknown")
        mode = r.get("mode", "?")
        groups[(inst_id, mode)].append(r)

    if not groups:
        print("no data")
        return

    header = f"{'instance_id':<50} {'mode':<8} {'checks':>6} {'median_ms':>10} {'total_ms':>10} {'verdict':<8}"
    print(header)
    print("-" * len(header))

    for (inst_id, mode), rows in sorted(groups.items()):
        ms_vals = sorted(r.get("check_ms", 0) for r in rows)
        n = len(ms_vals)
        median_ms = ms_vals[n // 2] if n else 0
        total_ms = sum(ms_vals)
        last = rows[-1]
        verdict = last.get("verdict", "?")
        print(f"{inst_id:<50} {mode:<8} {n:>6} {median_ms:>10.0f} {total_ms:>10.0f} {verdict:<8}")


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="episodes.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    prep = sub.add_parser("prepare", help="Prepare a workspace for one instance")
    prep.add_argument("instances", help="JSONL file with instance records")
    prep.add_argument("instance_id", help="Instance ID to prepare")
    prep.add_argument("--mode", choices=["cold", "daemon", "kiln", "kiln-rt"], required=True)
    prep.add_argument("--dest", required=True, help="Destination directory")

    ci = sub.add_parser("check-impl", help="Run one check (called by ./check)")
    ci.add_argument("dir", help="Workspace directory")

    st = sub.add_parser("stop", help="Stop resident server for workspace")
    st.add_argument("dir", help="Workspace directory")

    rp = sub.add_parser("replay", help="Replay recorded attempts across modes")
    rp.add_argument("instances", help="JSONL file with instance records")
    rp.add_argument("instance_id", help="Instance ID")
    rp.add_argument("--attempts-dir", required=True, help="Directory with attempt_N.patch files")
    rp.add_argument("--modes", default="cold", help="Comma-separated list: cold,daemon,kiln")
    rp.add_argument("--dest-root", required=True, help="Root for replay workspaces")
    rp.add_argument("--out", required=True, help="Output JSONL for replay results")

    sm = sub.add_parser("summarize", help="Print summary table from telemetry")
    sm.add_argument("--telemetry", nargs="+", required=True, metavar="FILE",
                    help="Telemetry JSONL files or globs")
    sm.add_argument("--replay", metavar="FILE", help="Optional replay JSONL to merge")

    return p


def main():
    p = build_parser()
    args = p.parse_args()

    dispatch = {
        "prepare": cmd_prepare,
        "check-impl": cmd_check_impl,
        "stop": cmd_stop,
        "replay": cmd_replay,
        "summarize": cmd_summarize,
    }

    fn = dispatch.get(args.subcommand)
    if fn is None:
        p.print_help()
        sys.exit(1)
    fn(args)


if __name__ == "__main__":
    main()
