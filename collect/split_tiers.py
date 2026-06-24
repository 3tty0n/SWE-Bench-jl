"""
split_tiers.py — split the validated dataset into Full / Lite / Verified tiers.

Reads data/instances.jsonl (or --in) and writes three JSONL files:

    data/instances_full.jsonl      — all validated instances (sanity gates only)
    data/instances_lite.jsonl      — model-tractable subset
    data/instances_verified.jsonl  — stricter quality: well-specified, non-leaky, clean signal

Tier criteria (each tier is a strict subset of the one above):

  Full:
    - F2P >= 1                         (guaranteed by validation)
    - problem_statement >= 5 chars     (basic sanity)

  Lite = Full +:
    - problem_statement >= 20 chars    (actionable problem description)
    - patch <= 300 lines               (gold fix is model-tractable)
    - test_patch <= 500 lines          (test scope bounded)
    - F2P <= 10                        (not a noisy refactor)
    - P2P <= 1000                      (evaluation wall-clock bounded)

  Verified = Lite +:
    - problem_statement >= 50 chars    (meaningful description)
    - if statement_source == "commit": problem_statement >= 100 chars
    - patch <= 200 lines               (tighter tractability)
    - no "new file mode" in patch      (edits existing code only)

Usage:
    python3 collect/split_tiers.py
    python3 collect/split_tiers.py --in data/instances.jsonl --out-dir data/
    python3 collect/split_tiers.py --self-test
"""

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(ROOT, "data/instances.jsonl")
DEFAULT_OUT_DIR = os.path.join(ROOT, "data")

# ---------------------------------------------------------------------------
# Tier gate constants
# ---------------------------------------------------------------------------
FULL_MIN_PS_CHARS = 5

LITE_MIN_PS_CHARS = 20
LITE_MAX_PATCH_LINES = 300
LITE_MAX_TEST_PATCH_LINES = 500
LITE_MAX_F2P = 10
LITE_MAX_P2P = 1000

VERIFIED_MIN_PS_CHARS = 50
VERIFIED_COMMIT_MIN_PS_CHARS = 100
VERIFIED_MAX_PATCH_LINES = 200


def tier_full(inst):
    if len(inst["FAIL_TO_PASS"]) < 1:
        return False, "no_f2p"
    if len(inst.get("problem_statement", "").strip()) < FULL_MIN_PS_CHARS:
        return False, "no_ps"
    return True, ""


def tier_lite(inst):
    ok, reason = tier_full(inst)
    if not ok:
        return False, reason
    ps_len = len(inst["problem_statement"])
    if ps_len < LITE_MIN_PS_CHARS:
        return False, "ps_too_short"
    if len(inst["patch"].splitlines()) > LITE_MAX_PATCH_LINES:
        return False, "patch_too_large"
    if len(inst["test_patch"].splitlines()) > LITE_MAX_TEST_PATCH_LINES:
        return False, "test_patch_too_large"
    if len(inst["FAIL_TO_PASS"]) > LITE_MAX_F2P:
        return False, "f2p_too_many"
    if len(inst["PASS_TO_PASS"]) > LITE_MAX_P2P:
        return False, "p2p_too_many"
    return True, ""


def tier_verified(inst):
    ok, reason = tier_lite(inst)
    if not ok:
        return False, reason
    ps_len = len(inst["problem_statement"])
    src = inst.get("statement_source", "commit")
    if ps_len < VERIFIED_MIN_PS_CHARS:
        return False, "ps_too_short"
    if src == "commit" and ps_len < VERIFIED_COMMIT_MIN_PS_CHARS:
        return False, "commit_ps_too_short"
    if len(inst["patch"].splitlines()) > VERIFIED_MAX_PATCH_LINES:
        return False, "patch_too_large"
    if "new file mode" in inst["patch"]:
        return False, "adds_new_file"
    return True, ""


TIERS = [
    ("full", tier_full),
    ("lite", tier_lite),
    ("verified", tier_verified),
]


def write_tier(insts, path):
    with open(path, "w") as f:
        for inst in insts:
            f.write(json.dumps(inst, ensure_ascii=False) + "\n")


def print_stats(name, insts, rejections):
    repos = collections.Counter(i["repo"] for i in insts)
    print(f"  {name:>10}: {len(insts):>5} instances / {len(repos):>3} repos", file=sys.stderr)
    if rejections:
        top = rejections.most_common(5)
        reasons = ", ".join(f"{r}={n}" for r, n in top)
        print(f"             rejections: {reasons}", file=sys.stderr)


def split(all_insts, out_dir):
    results = {}
    for tier_name, gate_fn in TIERS:
        kept, rejections = [], collections.Counter()
        for inst in all_insts:
            ok, reason = gate_fn(inst)
            if ok:
                kept.append(inst)
            else:
                rejections[reason] += 1
        path = os.path.join(out_dir, f"instances_{tier_name}.jsonl")
        write_tier(kept, path)
        print_stats(tier_name, kept, rejections)
        results[tier_name] = kept
    return results


def self_test():
    base = {
        "instance_id": "Org__Pkg-42",
        "repo": "Org/Pkg.jl",
        "repo_url": "https://github.com/Org/Pkg.jl",
        "base_commit": "aaa",
        "fix_commit": "bbb",
        "subject": "Fix off-by-one in frobnicator",
        "problem_statement": "The frobnicator returns wrong results for edge case inputs. " * 3,
        "statement_source": "issue",
        "issue_url": "https://github.com/Org/Pkg.jl/issues/41",
        "patch": "--- a/src/foo.jl\n+++ b/src/foo.jl\n@@ -1,3 +1,3 @@\n-old\n+new\n",
        "test_patch": "--- a/test/runtests.jl\n+++ b/test/runtests.jl\n@@ -1,3 +1,3 @@\n-old\n+new\n",
        "created_at": "2026-01-01T00:00:00Z",
        "FAIL_TO_PASS": ["root/frobnicator"],
        "PASS_TO_PASS": ["root/basic"],
        "julia_version": "julia version 1.12.6",
    }

    ok, _ = tier_full(base)
    assert ok, "base should pass full"
    ok, _ = tier_lite(base)
    assert ok, "base should pass lite"
    ok, _ = tier_verified(base)
    assert ok, "base should pass verified"

    # Empty F2P -> rejected at full
    bad = {**base, "FAIL_TO_PASS": []}
    ok, r = tier_full(bad)
    assert not ok and r == "no_f2p"

    # Huge patch -> rejected at lite
    big_patch = base.copy()
    big_patch["patch"] = "\n".join(["line"] * 400)
    ok, _ = tier_full(big_patch)
    assert ok, "big patch should pass full"
    ok, r = tier_lite(big_patch)
    assert not ok and r == "patch_too_large"

    # Short commit-sourced ps -> rejected at verified
    commit_short = {**base, "statement_source": "commit", "problem_statement": "Fix bug in X (60 chars padding" + "." * 30 + ")"}
    ok, _ = tier_lite(commit_short)
    assert ok, "commit_short should pass lite"
    ok, r = tier_verified(commit_short)
    assert not ok and r == "commit_ps_too_short"

    # New file in patch -> rejected at verified
    new_file = base.copy()
    new_file["patch"] = "diff --git a/src/new.jl b/src/new.jl\nnew file mode 100644\n--- /dev/null\n+++ b/src/new.jl\n@@ -0,0 +1 @@\n+code\n"
    ok, _ = tier_lite(new_file)
    assert ok, "new_file should pass lite"
    ok, r = tier_verified(new_file)
    assert not ok and r == "adds_new_file"

    # Many F2P -> rejected at lite
    many_f2p = {**base, "FAIL_TO_PASS": [f"test/{i}" for i in range(15)]}
    ok, _ = tier_full(many_f2p)
    assert ok
    ok, r = tier_lite(many_f2p)
    assert not ok and r == "f2p_too_many"

    # Many P2P -> rejected at lite
    many_p2p = {**base, "PASS_TO_PASS": [f"test/{i}" for i in range(2000)]}
    ok, _ = tier_full(many_p2p)
    assert ok
    ok, r = tier_lite(many_p2p)
    assert not ok and r == "p2p_too_many"

    print("self-test: all assertions passed", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="inp", default=DEFAULT_IN)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.self_test:
        self_test()
        return

    insts = [json.loads(l) for l in open(args.inp) if l.strip()]
    print(f"input: {len(insts)} instances from {args.inp}", file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)
    split(insts, args.out_dir)


if __name__ == "__main__":
    main()
