#!/usr/bin/env bash
# pull_repos.sh — discover, select, and pull (clone + mine) the top-N Julia repos.
#
# General, parameterized version of the "obtain N repositories" step: it ranks the
# whole JuliaRegistries/General registry, keeps the best permissively-licensed,
# test-bearing repos, then clones each and mines bug-fix candidates into one pool.
# Idempotent: a present registry / repos.tsv / per-repo candidate file is reused,
# so re-running resumes instead of redoing work.
#
# Usage:
#   N=300 JOBS=8 collect/pull_repos.sh           # pull the top 300 repos
#   N=50  collect/pull_repos.sh                   # pull the top 50
#   N=300 WITH_GH=1 collect/pull_repos.sh         # mine with PR/issue problem statements
#
# Tunables (env vars):
#   N         repos to pull                                    (default 300)
#   JOBS      repos mined concurrently                         (default: nproc)
#   SINCE     mine commits on/after this date                  (default 2018-01-01)
#   ENRICH    repos to license/test-check via gh in discovery  (default: N*2, min 150)
#   WITH_GH   1 = mine with gh problem statements (slower)     (default: 0, --no-gh)
#   HARD      1 = strict non-leaky mining predicate            (default: 0)
#   COVERED   instances file whose repos are skipped           (default data/instances.jsonl)
#   OUT       aggregated candidate pool path                   (default work/pool<N>_candidates.jsonl)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

N="${N:-300}"
JOBS="${JOBS:-$(nproc)}"
SINCE="${SINCE:-2018-01-01}"
ENRICH="${ENRICH:-$(( N*2 > 150 ? N*2 : 150 ))}"
REGISTRY="${REGISTRY:-work/General}"
REPOS_TSV="${REPOS_TSV:-collect/repos.tsv}"
LIST="${LIST:-work/pull_repos_list.tsv}"
META="${META:-work/pull_repos_meta.json}"
OUT="${OUT:-work/pool${N}_candidates.jsonl}"
COVERED="${COVERED:-data/instances.jsonl}"
GH_FLAG="--no-gh"; [ "${WITH_GH:-0}" = "1" ] && GH_FLAG=""
HARD_FLAG="";      [ "${HARD:-0}" = "1" ]    && HARD_FLAG="--hard"

echo "== pull_repos: N=$N JOBS=$JOBS SINCE=$SINCE ENRICH=$ENRICH ${GH_FLAG:+(no-gh)} ${HARD_FLAG:+(hard)}"

# 1. registry (offline source of truth for discovery)
if [ ! -f "$REGISTRY/Registry.toml" ]; then
  echo "== cloning JuliaRegistries/General -> $REGISTRY"
  git clone --depth 1 --filter=blob:none https://github.com/JuliaRegistries/General "$REGISTRY"
fi

# 2. discover + rank (cached: re-runs only fetch repos not already enriched)
echo "== discovering / ranking repos -> $REPOS_TSV"
python3 collect/discover_repos.py --registry "$REGISTRY" \
  --enrich "$ENRICH" --require-tests --require-permissive \
  --exclude-covered "$COVERED" --out "$REPOS_TSV"

# 3. select the top-N repos, preferring license-verified ones, into a mine list
echo "== selecting top $N repos -> $LIST"
python3 - "$REPOS_TSV" "$N" "$LIST" "$META" <<'PY'
import json, sys
tsv, N, list_path, meta_path = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
PERM = {"MIT","BSD-2-Clause","BSD-3-Clause","BSD-3-Clause-Clear","Apache-2.0","ISC","MPL-2.0","Zlib","BSL-1.0"}
rows = [l.rstrip("\n").split("\t") for l in open(tsv)]
hdr = rows[0]; si, ti, li = hdr.index("slug"), hdr.index("has_tests"), hdr.index("license")
data = rows[1:]
def gated(r): return len(r) > max(si, ti, li) and r[ti] == "yes" and r[li] in PERM
verified = [r[si] for r in data if gated(r)]
# prefer gated repos; if fewer than N, top up with the highest-ranked remaining rows
chosen = verified[:N]
if len(chosen) < N:
    extra = [r[si] for r in data if not gated(r)]
    chosen += extra[: N - len(chosen)]
need_recheck = [s for s in chosen if s not in set(verified)]
with open(list_path, "w") as f:
    f.write("slug\n"); [f.write(s + "\n") for s in chosen]
json.dump({"total": len(chosen), "license_verified": len(chosen) - len(need_recheck),
           "need_license_recheck": need_recheck}, open(meta_path, "w"), indent=1)
print(f"   selected {len(chosen)} repos: {len(chosen)-len(need_recheck)} license-verified, "
      f"{len(need_recheck)} need recheck before publish")
PY

# 4. pull: clone each repo and mine bug-fix candidates into one pool
echo "== pulling (clone + mine) -> $OUT"
python3 collect/mine_all.py --repos "$LIST" --jobs "$JOBS" \
  --since "$SINCE" --prefilter $GH_FLAG $HARD_FLAG --out "$OUT"

echo "== pull_repos done. pool: $OUT ($(grep -c . "$OUT" 2>/dev/null || echo 0) candidates); list: $LIST; meta: $META"
