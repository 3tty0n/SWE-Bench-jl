"""
mine_repo.py — SWE-bench-jl candidate instance miner.

Walks a locally cloned Julia package repo's git history and emits JSONL
candidate task instances (issue text + base commit + gold src patch + test patch)
for later validation by the SWE-bench-jl harness.

Usage:
    python3 mine_repo.py <repo_dir> --repo-slug org/name [options]

Options:
    --since YYYY-MM-DD      Only commits after this date (default: 2021-01-01)
    --max-candidates N      Stop after N accepted candidates (default: 50)
    --out FILE              Output JSONL path (default: candidates.jsonl)
    --no-gh                 Skip GitHub API calls entirely
    --max-src-files N       Max source files changed per candidate (default: 8)
    --max-src-lines N       Max total source diff lines changed (default: 500)
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time


# ── git helpers ──────────────────────────────────────────────────────────────

def git(repo_dir, *args, check=True):
    result = subprocess.run(
        ["git", "-C", repo_dir] + list(args),
        capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def default_branch(repo_dir):
    try:
        ref = git(repo_dir, "symbolic-ref", "refs/remotes/origin/HEAD")
        return ref.split("/")[-1]
    except RuntimeError:
        return git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")


def commit_log(repo_dir, branch, since):
    out = git(repo_dir, "log", "--first-parent", branch,
              f"--since={since}", "--format=%H")
    return [h for h in out.splitlines() if h]


def commit_info(repo_dir, sha):
    fmt = "%P%x00%s%x00%b%x00%aI"
    raw = git(repo_dir, "log", "-1", f"--format={fmt}", sha)
    parts = raw.split("\x00")
    if len(parts) < 4:
        return None
    parents_str, subject, body, author_date = parts[0], parts[1], parts[2], parts[3]
    parents = parents_str.split()
    return {
        "parents": parents,
        "subject": subject.strip(),
        "body": body.strip(),
        "author_date": author_date.strip(),
    }


def name_status(repo_dir, parent, child):
    out = git(repo_dir, "diff", "--name-status", parent, child)
    entries = []
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, path = parts
            entries.append((status.strip(), path.strip()))
    return entries


def numstat(repo_dir, parent, child, files):
    if not files:
        return 0
    out = git(repo_dir, "diff", "--numstat", parent, child, "--", *files)
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                total += int(parts[0]) + int(parts[1])
            except ValueError:
                pass
    return total


def unified_diff(repo_dir, parent, child, files):
    if not files:
        return ""
    result = subprocess.run(
        ["git", "-C", repo_dir, "diff", parent, child, "--"] + list(files),
        capture_output=True, text=True, errors="replace"
    )
    return result.stdout


# ── classification helpers ────────────────────────────────────────────────────

_SRC_RE = re.compile(r"^(src|ext)/.*\.jl$")
_TEST_RE = re.compile(r"^test/.*\.jl$")
_SKIP_FILES = {"Project.toml", "Manifest.toml", "test/Project.toml"}

_FIX_RE = re.compile(
    r"\b(fix(es|ed)?|bug|issue|close[sd]?|resolve[sd]?)\b|#\d+",
    re.IGNORECASE,
)


def classify_paths(entries):
    src_files, test_files, skip = [], [], False
    for _status, path in entries:
        if path in _SKIP_FILES:
            skip = True
            break
        if _SRC_RE.match(path):
            src_files.append(path)
        elif _TEST_RE.match(path):
            test_files.append(path)
    return src_files, test_files, skip


def is_fix_commit(subject, body):
    text = subject + "\n" + body
    return bool(_FIX_RE.search(text))


# Subjects/bodies that signal a feature or new-API addition rather than a bug fix. These
# tasks ("implement X", "add method Y") are well-cued by the failing-test name and tend to
# be easy for agents; difficulty mining excludes them.
_FEAT_RE = re.compile(
    r"\b(feat|implement(s|ed)?|introduc(e|es|ed)|add(s|ed)?\s+(support|methods?|functions?|api|new))\b"
    r"|\bnew\s+(function|method|feature|api)\b",
    re.IGNORECASE,
)


def is_feature_commit(subject, body):
    return bool(_FEAT_RE.search(subject + "\n" + body))


def patch_modifies_existing(patch):
    """True if the src patch removes/changes existing lines (real `-` lines, not the `---`
    file header), i.e. it edits existing logic rather than purely adding new code. A robust
    difficulty signal: subtle bug fixes touch existing code; "add function X" tasks are
    addition-only and tend to be well-cued/easy."""
    for line in patch.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            return True
    return False


# ── PR / issue number extraction ──────────────────────────────────────────────

_SQUASH_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_MERGE_PR_RE = re.compile(r"Merge pull request #(\d+)", re.IGNORECASE)
_CLOSE_ISSUE_RE = re.compile(
    r"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)


def extract_pr_number(subject, body):
    m = _SQUASH_PR_RE.search(subject)
    if m:
        return int(m.group(1))
    m = _MERGE_PR_RE.search(subject)
    if m:
        return int(m.group(1))
    return None


def extract_issue_refs(text):
    return [int(m) for m in _CLOSE_ISSUE_RE.findall(text)]


# ── GitHub API ────────────────────────────────────────────────────────────────

def gh_available():
    r = subprocess.run(["gh", "auth", "status"], capture_output=True)
    return r.returncode == 0


def gh_api(path, jq=None):
    cmd = ["gh", "api", path]
    if jq:
        cmd += ["--jq", jq]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout.strip() or None


def fetch_problem_statement(slug, pr_num, subject, body, use_gh):
    statement = subject + ("\n\n" + body if body else "")
    source = "commit"
    issue_url = None

    if not use_gh or pr_num is None:
        return statement, source, issue_url

    time.sleep(0.3)
    pr_data = gh_api(f"repos/{slug}/pulls/{pr_num}",
                     jq="{title: .title, body: .body}")
    if pr_data and isinstance(pr_data, dict):
        pr_title = pr_data.get("title") or ""
        pr_body = pr_data.get("body") or ""
        statement = pr_title + ("\n\n" + pr_body if pr_body else "")
        source = "pr"

        refs = extract_issue_refs(body + "\n" + pr_body)
        for issue_num in refs:
            time.sleep(0.3)
            issue = gh_api(
                f"repos/{slug}/issues/{issue_num}",
                jq="{title,body,html_url,is_pr: (.pull_request != null)}",
            )
            if issue and isinstance(issue, dict) and not issue.get("is_pr"):
                ititle = issue.get("title") or ""
                ibody = issue.get("body") or ""
                statement = ititle + ("\n\n" + ibody if ibody else "")
                source = "issue"
                issue_url = issue.get("html_url")
                break

    return statement, source, issue_url


# ── instance_id ───────────────────────────────────────────────────────────────

def make_instance_id(slug, pr_num, sha8):
    org_name = slug.replace("/", "__")
    # strip .jl suffix from repo name part
    if org_name.endswith(".jl"):
        org_name = org_name[:-3]
    suffix = str(pr_num) if pr_num is not None else sha8
    return f"{org_name}-{suffix}"


# ── main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("repo_dir")
    p.add_argument("--repo-slug", required=True, metavar="org/name")
    p.add_argument("--since", default="2021-01-01")
    p.add_argument("--max-candidates", type=int, default=50)
    p.add_argument("--out", default="candidates.jsonl")
    p.add_argument("--no-gh", action="store_true")
    p.add_argument("--max-src-files", type=int, default=8)
    p.add_argument("--max-src-lines", type=int, default=500)
    # Difficulty mining (v0.2): bias toward harder, less-cued, discriminating instances.
    p.add_argument("--hard", action="store_true",
                   help="difficulty mode: --require-issue + --exclude-feat + --modifies-existing")
    p.add_argument("--require-issue", action="store_true",
                   help="only accept commits whose problem statement comes from a linked issue (symptom-described, least leaky)")
    p.add_argument("--exclude-feat", action="store_true",
                   help="skip feature/new-API commits; keep genuine bug fixes")
    p.add_argument("--modifies-existing", action="store_true",
                   help="require the src patch to edit existing code (have real deletion lines)")
    return p.parse_args()


def main():
    args = parse_args()
    if args.hard:
        args.require_issue = args.exclude_feat = args.modifies_existing = True

    repo_dir = os.path.abspath(args.repo_dir)
    slug = args.repo_slug
    repo_url = f"https://github.com/{slug}"

    use_gh = not args.no_gh and gh_available()
    if args.no_gh:
        pass
    elif not use_gh:
        print("gh CLI not authenticated; falling back to commit messages only.",
              file=sys.stderr)

    branch = default_branch(repo_dir)
    commits = commit_log(repo_dir, branch, args.since)
    print(f"Scanning {len(commits)} commits on {branch} since {args.since}",
          file=sys.stderr)

    accepted = 0
    out_path = args.out

    with open(out_path, "w") as out_fh:
        for sha in commits:
            if accepted >= args.max_candidates:
                break

            info = commit_info(repo_dir, sha)
            if info is None:
                continue

            parents = info["parents"]
            if not parents:
                continue
            parent = parents[0]

            if not is_fix_commit(info["subject"], info["body"]):
                continue

            if args.exclude_feat and is_feature_commit(info["subject"], info["body"]):
                continue

            try:
                entries = name_status(repo_dir, parent, sha)
            except RuntimeError:
                continue

            src_files, test_files, skip = classify_paths(entries)
            if skip or not src_files or not test_files:
                continue

            if len(src_files) > args.max_src_files:
                continue

            try:
                changed_lines = numstat(repo_dir, parent, sha, src_files)
            except RuntimeError:
                continue

            if changed_lines > args.max_src_lines:
                continue

            patch = unified_diff(repo_dir, parent, sha, src_files)
            test_patch = unified_diff(repo_dir, parent, sha, test_files)
            if not patch or not test_patch:
                continue

            if args.modifies_existing and not patch_modifies_existing(patch):
                continue

            pr_num = extract_pr_number(info["subject"], info["body"])
            sha8 = sha[:8]

            problem_statement, stmt_source, issue_url = fetch_problem_statement(
                slug, pr_num, info["subject"], info["body"], use_gh
            )

            if args.require_issue and stmt_source != "issue":
                continue

            instance_id = make_instance_id(slug, pr_num, sha8)

            record = {
                "instance_id": instance_id,
                "repo": slug,
                "repo_url": repo_url,
                "base_commit": parent,
                "fix_commit": sha,
                "subject": info["subject"],
                "problem_statement": problem_statement,
                "statement_source": stmt_source,
                "issue_url": issue_url,
                "patch": patch,
                "test_patch": test_patch,
                "created_at": info["author_date"],
                "FAIL_TO_PASS": [],
                "PASS_TO_PASS": [],
                "julia_version": None,
            }

            out_fh.write(json.dumps(record) + "\n")
            out_fh.flush()
            accepted += 1
            print(f"[{accepted}] {instance_id}  {info['subject'][:72]}",
                  file=sys.stderr)

    print(f"\nDone. {accepted} candidates written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
