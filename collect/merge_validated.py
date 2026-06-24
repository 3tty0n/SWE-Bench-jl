"""
merge_validated.py — union an existing dataset with a freshly-validated pool.

The committed dataset and a new validation pool may overlap. This unions them by
`instance_id`, and on conflict keeps the record with the richer problem statement
(an already enriched PR/issue statement beats a raw commit message); ties go to
the pool record, whose F2P/P2P came from the most recent validation run.

Records present only in the existing dataset are preserved (e.g. earlier seed
instances that are not in the new pool), and records present only in the pool are
added. No field other than the chosen whole-record winner is modified.

Usage:
    python3 collect/merge_validated.py \
        --existing data/instances.jsonl \
        --pool work/scale/logs/validated.jsonl \
        --out work/merge_base.jsonl
"""

import argparse
import collections
import json
import sys

# problem-statement quality rank; higher wins on conflict
RANK = {"pr": 3, "issue": 2, "commit": 1}

# A handful of suites generate @testset names from random values (e.g.
# "a=0.1044.../x=5.8e-7"), yielding ~1M non-deterministic PASS_TO_PASS node IDs
# that can never re-match on evaluation and bloat the dataset to >100 MB/instance.
# Drop instances whose PASS_TO_PASS exceeds this sane ceiling.
MAX_P2P = 100_000


def load(fp):
    return [json.loads(l) for l in open(fp) if l.strip()]


def rank(rec):
    return RANK.get(rec.get("statement_source", "commit"), 0)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--existing", required=True)
    p.add_argument("--pool", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    existing = load(args.existing)
    pool = load(args.pool)

    merged = {}
    # pool first (fresh validation), then let existing win only when strictly richer
    for r in pool:
        merged[r["instance_id"]] = r
    kept_existing = added_existing = replaced = 0
    for r in existing:
        iid = r["instance_id"]
        if iid not in merged:
            merged[iid] = r
            added_existing += 1
        elif rank(r) > rank(merged[iid]):
            merged[iid] = r
            replaced += 1
        else:
            kept_existing += 1

    rows = list(merged.values())
    dropped_p2p = [r["instance_id"] for r in rows if len(r["PASS_TO_PASS"]) > MAX_P2P]
    rows = [r for r in rows if len(r["PASS_TO_PASS"]) <= MAX_P2P]
    with open(args.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    src = collections.Counter(r.get("statement_source", "?") for r in rows)
    print(f"[merge] existing={len(existing)} pool={len(pool)} -> union={len(rows)} "
          f"(dropped {len(dropped_p2p)} with PASS_TO_PASS>{MAX_P2P})", file=sys.stderr)
    print(f"        existing-only added={added_existing} "
          f"overlap-existing-richer={replaced} overlap-pool-kept={kept_existing}",
          file=sys.stderr)
    print(f"        statement_source in union: {dict(src)}", file=sys.stderr)


if __name__ == "__main__":
    main()
