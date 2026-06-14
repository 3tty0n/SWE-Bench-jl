"""
enrich_statements.py — upgrade problem statements on already-validated instances.

When mining runs with --no-gh (fast, no API rate limit), each instance's
`problem_statement` falls back to the commit message and `statement_source` is
`"commit"`. Validation is the expensive filter, so it is far cheaper to mine +
validate offline first and then fetch GitHub text for *only the instances that
survived* — typically a few hundred calls instead of thousands.

For each instance whose `statement_source` is `"commit"` (or `--force` for all),
this re-runs `mine_repo.fetch_problem_statement` using the PR number encoded in
the `instance_id`, upgrading the statement to PR- or issue-sourced text and
filling `issue_url`. Instances are otherwise left untouched; F2P/P2P and patches
are never modified.

Usage:
    python3 collect/enrich_statements.py --in data/instances.jsonl --out data/instances.jsonl
    python3 collect/enrich_statements.py --in work/scale_valid.jsonl --out work/scale_valid.enriched.jsonl
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mine_repo import fetch_problem_statement, gh_available


def pr_from_instance_id(iid: str) -> int | None:
    """`org__name-123` -> 123; `org__name-ab12cd34` (sha fallback) -> None."""
    m = re.search(r"-(\d+)$", iid)
    return int(m.group(1)) if m else None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true",
                   help="re-fetch even instances already PR/issue-sourced")
    args = p.parse_args()

    if not gh_available():
        sys.exit("gh CLI not authenticated; cannot enrich statements")

    rows = [json.loads(l) for l in open(args.inp) if l.strip()]
    upgraded = {"issue": 0, "pr": 0, "commit": 0, "skipped": 0}

    for r in rows:
        if not args.force and r.get("statement_source") in ("issue", "pr"):
            upgraded["skipped"] += 1
            continue
        pr_num = pr_from_instance_id(r["instance_id"])
        if pr_num is None:
            upgraded["skipped"] += 1
            continue
        statement, source, issue_url = fetch_problem_statement(
            r["repo"], pr_num, r.get("subject", ""), "", use_gh=True)
        r["problem_statement"] = statement
        r["statement_source"] = source
        r["issue_url"] = issue_url
        upgraded[source] = upgraded.get(source, 0) + 1
        print(f"  {r['instance_id']}: {source}", file=sys.stderr)

    with open(args.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print(f"[enrich] {len(rows)} instances -> {args.out}  "
          f"issue={upgraded['issue']} pr={upgraded['pr']} commit={upgraded['commit']} "
          f"skipped={upgraded['skipped']}", file=sys.stderr)


if __name__ == "__main__":
    main()
