"""
build_hard_split.py — derive the hard (strict, non-leaky) split from validated instances.

The --hard mining predicate (issue-sourced + non-feature + edits-existing-code) is a property
of each instance's stored fields, so the hard subset of an already-validated dataset can be
computed offline — no git, no GitHub, no re-validation. This reuses mine_repo.py's predicates
verbatim so the offline filter and the live miner cannot drift.

Usage:
    python3 build_hard_split.py [--in data/instances.jsonl] [--out data/instances_hard.jsonl]

A v0 instance is kept iff all three hold:
    statement_source == "issue"      # symptom-described, least likely to leak the fix
    not is_feature_commit(subject)   # a genuine bug fix, not an "add API X" task
    patch_modifies_existing(patch)   # the src patch edits existing code (has real `-` lines)
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mine_repo import is_feature_commit, patch_modifies_existing


def is_hard(rec):
    if rec.get("statement_source") != "issue":
        return False
    # The commit body is not stored on the instance; the subject is the faithful available
    # signal for the feature/bug-fix split (the miner ran this before fetching the issue).
    if is_feature_commit(rec.get("subject", ""), ""):
        return False
    if not patch_modifies_existing(rec.get("patch", "")):
        return False
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="inp", default="data/instances.jsonl")
    p.add_argument("--out", default="data/instances_hard.jsonl")
    args = p.parse_args()

    rows = [json.loads(l) for l in open(args.inp) if l.strip()]
    hard = [r for r in rows if is_hard(r)]

    with open(args.out, "w") as f:
        for r in hard:
            f.write(json.dumps(r) + "\n")

    by_repo = collections.Counter(r["repo"].split("/")[-1] for r in hard)
    print(f"{len(rows)} validated -> {len(hard)} hard ({args.out})", file=sys.stderr)
    print(f"  by repo: {dict(by_repo)}", file=sys.stderr)
    for r in hard:
        print(f"  {r['instance_id']}  {r['subject'][:64]}", file=sys.stderr)


if __name__ == "__main__":
    main()
