"""
mine_all.py — parallel mining orchestrator (scaling plan P2).

Drives `mine_repo.py` across a whole `repos.tsv` (from P1 `discover_repos.py`)
instead of one hand-typed repo, then aggregates + de-duplicates into a single
candidate pool for validation (P4).

What it adds over a bare loop of `mine_repo.py`:

  * Parallel repos (`--jobs`): clone + mine several repos at once. Mining is
    git/network/subprocess bound, so threads suffice even on a 1-core box.
  * Uncapped mining: passes a huge `--max-candidates` so full history is scanned
    (the per-repo 50 cap was a scaling gap, G6).
  * Per-repo output `work/<org__name>.candidates.jsonl` (kept by .gitignore as
    provenance) + an aggregated `--out` pool.
  * Cross-repo de-dup: identical fixes re-registered via forks/mirrors are
    collapsed by (instance_id) and by content hash of (patch + test_patch).
  * G5 static pre-filter (`--prefilter`): drop candidates whose test_patch adds
    no `@test` referencing any symbol the fix patch touches — these almost always
    validate as `no_fail_to_pass` (wasted oracle runs). Validation stays the
    authority; this only trims obvious misses before the expensive step.
  * Resumable: a repo whose candidates file already exists is reused unless
    `--no-resume`.

Usage
-----
    python3 collect/mine_all.py --repos collect/repos.tsv --top 30 \\
        --jobs 4 --since 2019-01-01 --out work/candidates_all.jsonl

    # explicit repo list, hard (non-leaky) mining:
    python3 collect/mine_all.py --repo-list SciML/SciMLBase.jl,JuliaArrays/StaticArrays.jl \\
        --hard --out work/candidates_hard.jsonl

    python3 collect/mine_all.py --self-test
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_WORK = ROOT / "work"
MINE_REPO = HERE / "mine_repo.py"

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


# ── G5: static test/fix symbol-overlap pre-filter ────────────────────────────

# Julia keywords + Test macros + builtins that carry no signal about *which*
# function a test exercises; ignored when computing test/fix symbol overlap.
_STOPWORDS = {
    "test", "testset", "testsets", "test_throws", "test_broken", "test_logs",
    "test_nowarn", "inferred", "using", "import", "end", "begin", "function",
    "return", "true", "false", "nothing", "for", "while", "if", "else", "elseif",
    "do", "let", "const", "global", "local", "in", "isa", "where", "module",
    "struct", "mutable", "abstract", "type", "quote", "macro", "try", "catch",
    "finally", "throw", "error", "println", "print", "show", "length", "push",
    "isequal", "isapprox", "isempty", "Int", "Float64", "String", "Bool",
    "Vector", "Dict", "Set", "Array", "Tuple", "and", "or", "not",
}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_!]*")


def _symbols(lines, signs):
    """Identifiers (minus stopwords) on diff lines whose first char is in `signs`."""
    out = set()
    for line in lines:
        if not line or line[0] not in signs:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        for tok in _IDENT_RE.findall(line[1:]):
            if tok not in _STOPWORDS and len(tok) > 1:
                out.add(tok)
    return out


def test_overlaps_fix(patch: str, test_patch: str) -> bool:
    """True (keep) if the test additions reference a symbol the fix changed.

    Conservative: if either side yields no symbols we keep the candidate (can't
    confidently reject), since validation is the real oracle.
    """
    fix_syms = _symbols(patch.splitlines(), "+-")
    test_syms = _symbols(test_patch.splitlines(), "+")
    if not fix_syms or not test_syms:
        return True
    return bool(fix_syms & test_syms)


def content_hash(rec: dict) -> str:
    h = hashlib.sha256()
    h.update(rec.get("patch", "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update(rec.get("test_patch", "").encode("utf-8", "replace"))
    return h.hexdigest()


# ── clone + mine one repo ────────────────────────────────────────────────────

def safe_name(slug: str) -> str:
    return slug.replace("/", "__")


def ensure_clone(slug: str, work: pathlib.Path) -> pathlib.Path:
    """Full clone (history + blobs, needed for fast `git diff` while mining)."""
    dest = work / "repos" / safe_name(slug)
    url = f"https://github.com/{slug}"
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "--all", "--tags"],
                       capture_output=True)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "--quiet", url, str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"clone failed for {slug}: {r.stderr.strip()[:200]}")
    return dest


def mine_one(slug: str, args) -> tuple[str, str | None, int]:
    """Returns (slug, candidates_path or None, n_candidates)."""
    work = pathlib.Path(args.work)
    out_file = work / f"{safe_name(slug)}.candidates.jsonl"

    if out_file.exists() and not args.no_resume:
        n = sum(1 for _ in open(out_file))
        log(f"[mine_all] {slug}: reuse {n} cached candidates")
        return slug, str(out_file), n

    try:
        repo_dir = ensure_clone(slug, work)
    except RuntimeError as e:
        log(f"[mine_all] {slug}: SKIP ({e})")
        return slug, None, 0

    cmd = [sys.executable, str(MINE_REPO), str(repo_dir),
           "--repo-slug", slug,
           "--since", args.since,
           "--max-candidates", str(args.max_candidates),
           "--out", str(out_file)]
    if args.hard:
        cmd.append("--hard")
    if args.no_gh:
        cmd.append("--no-gh")

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"[mine_all] {slug}: mine_repo failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        return slug, None, 0
    n = sum(1 for _ in open(out_file)) if out_file.exists() else 0
    log(f"[mine_all] {slug}: {n} candidates")
    return slug, (str(out_file) if n else None), n


# ── aggregation ──────────────────────────────────────────────────────────────

def aggregate(paths, out_path, *, prefilter: bool):
    """Merge per-repo candidate files into one pool; dedupe; optional G5 prefilter."""
    seen_ids, seen_hashes = set(), set()
    n_in = n_dup = n_filtered = n_out = 0
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as out_fh:
        for p in paths:
            if not p:
                continue
            for line in open(p):
                line = line.strip()
                if not line:
                    continue
                n_in += 1
                rec = json.loads(line)
                iid = rec.get("instance_id")
                h = content_hash(rec)
                if iid in seen_ids or h in seen_hashes:
                    n_dup += 1
                    continue
                if prefilter and not test_overlaps_fix(rec.get("patch", ""), rec.get("test_patch", "")):
                    n_filtered += 1
                    continue
                seen_ids.add(iid)
                seen_hashes.add(h)
                out_fh.write(json.dumps(rec) + "\n")
                n_out += 1

    return {"in": n_in, "dup": n_dup, "prefiltered": n_filtered, "out": n_out}


# ── main ─────────────────────────────────────────────────────────────────────

def read_repos_tsv(path, top):
    slugs = []
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        try:
            slug_i = header.index("slug")
        except ValueError:
            slug_i = 1  # rank, slug, ...
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) > slug_i and cols[slug_i]:
                slugs.append(cols[slug_i])
            if top and len(slugs) >= top:
                break
    return slugs


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--repos", metavar="repos.tsv", help="repo table from discover_repos.py")
    src.add_argument("--repo-list", help="comma-separated org/name.jl slugs")
    p.add_argument("--top", type=int, default=0, help="use only the first N rows of --repos")
    p.add_argument("--out", default="work/candidates_all.jsonl", help="aggregated candidate pool")
    p.add_argument("--work", default=str(DEFAULT_WORK))
    p.add_argument("--jobs", type=int, default=3, help="repos mined concurrently")
    p.add_argument("--since", default="2019-01-01")
    p.add_argument("--max-candidates", type=int, default=100000, help="effectively uncapped")
    p.add_argument("--hard", action="store_true", help="pass --hard to mine_repo (non-leaky)")
    p.add_argument("--no-gh", action="store_true", help="pass --no-gh to mine_repo")
    p.add_argument("--prefilter", action="store_true",
                   help="apply the G5 test/fix symbol-overlap pre-filter during aggregation")
    p.add_argument("--no-resume", action="store_true", help="re-mine even if a candidates file exists")
    return p.parse_args()


def main():
    if "--self-test" in sys.argv:
        return _self_test()
    args = parse_args()

    if args.repo_list:
        slugs = [s.strip() for s in args.repo_list.split(",") if s.strip()]
    elif args.repos:
        slugs = read_repos_tsv(args.repos, args.top)
    else:
        raise SystemExit("provide --repos repos.tsv or --repo-list a,b,c")

    log(f"[mine_all] mining {len(slugs)} repos with jobs={args.jobs}")
    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(mine_one, s, args): s for s in slugs}
        for fut in cf.as_completed(futs):
            results.append(fut.result())

    paths = [path for _slug, path, _n in results]
    stats = aggregate(paths, args.out, prefilter=args.prefilter)
    total_repos = sum(1 for _s, p, _n in results if p)
    log(f"[mine_all] repos with candidates: {total_repos}/{len(slugs)}")
    log(f"[mine_all] aggregate: in={stats['in']} dup={stats['dup']} "
        f"prefiltered={stats['prefiltered']} -> out={stats['out']} ({args.out})")


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test():
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"FAIL {name}: got {got!r} want {want!r}")
        else:
            print(f"  ok  {name}")

    fix = (
        "--- a/src/foo.jl\n+++ b/src/foo.jl\n"
        "@@\n-    return bar(x)\n+    return baz(x)\n"
    )
    test_hit = (
        "--- a/test/runtests.jl\n+++ b/test/runtests.jl\n"
        "@@\n+    @test baz(2) == 4\n"
    )
    test_miss = (
        "--- a/test/runtests.jl\n+++ b/test/runtests.jl\n"
        "@@\n+    @test unrelated(2) == 4\n"
    )
    check("overlap_hit", test_overlaps_fix(fix, test_hit), True)
    check("overlap_miss", test_overlaps_fix(fix, test_miss), False)
    # conservative: empty test symbols -> keep
    check("overlap_empty_test", test_overlaps_fix(fix, "+++ b/test/x\n+ @test true\n"), True)
    # stopwords only on fix side -> keep (can't reject)
    check("overlap_empty_fix", test_overlaps_fix("+++ b/src/x\n+ end\n", test_hit), True)

    check("safe_name", safe_name("Org/Name.jl"), "Org__Name.jl")

    r1 = {"patch": "p", "test_patch": "t"}
    r2 = {"patch": "p", "test_patch": "t"}
    r3 = {"patch": "p", "test_patch": "u"}
    check("hash_eq", content_hash(r1) == content_hash(r2), True)
    check("hash_neq", content_hash(r1) == content_hash(r3), False)

    print()
    if fails:
        for f in fails:
            print(f, file=sys.stderr)
        sys.exit(1)
    print("all self-tests passed")


if __name__ == "__main__":
    main()
