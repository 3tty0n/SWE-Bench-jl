#!/usr/bin/env bash
# run_tierA.sh — end-to-end Layer-A pipeline (scaling_plan.md M1: P1 -> P2 -> P4).
#
# Resume-safe: every stage skips work it already finished, so re-running after an
# interruption continues instead of restarting. See docs/tierA_runbook.md.
#
# Tunables (env vars):
#   TOP        repos to mine from the top of repos.tsv         (default 30)
#   JOBS       concurrency for mining and validation           (default: nproc)
#   SINCE      mine commits on/after this date                 (default 2019-01-01)
#   ENRICH     repos to enrich via gh in P1                    (default 150)
#   OUT        validated instances output                      (default data/instances.jsonl)
#   JULIA_BIN  Julia binary (set to the real one, not juliaup) (default: autodetect)
#   HARD       if set to 1, mine the strict non-leaky predicate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TOP="${TOP:-30}"
JOBS="${JOBS:-$(nproc)}"
SINCE="${SINCE:-2019-01-01}"
ENRICH="${ENRICH:-150}"
OUT="${OUT:-data/instances.jsonl}"
REGISTRY="${REGISTRY:-work/General}"
REPOS_TSV="${REPOS_TSV:-collect/repos.tsv}"
POOL="${POOL:-work/candidates_all.jsonl}"
HARD_FLAG=""; [ "${HARD:-0}" = "1" ] && HARD_FLAG="--hard"

# Prefer the real Julia binary: the juliaup launcher takes a global lock that
# serializes parallel validator workers.
if [ -z "${JULIA_BIN:-}" ]; then
  cand="$(ls -d "$HOME"/.julia/juliaup/julia-*/bin/julia 2>/dev/null | head -1 || true)"
  JULIA_BIN="${cand:-julia}"
fi
export JULIA_BIN

echo "== Tier A: TOP=$TOP JOBS=$JOBS SINCE=$SINCE JULIA_BIN=$JULIA_BIN ${HARD_FLAG:+(hard)}"

# P0: registry checkout (offline source of truth for P1)
if [ ! -f "$REGISTRY/Registry.toml" ]; then
  echo "== [P0] cloning JuliaRegistries/General -> $REGISTRY"
  git clone --depth 1 --filter=blob:none https://github.com/JuliaRegistries/General "$REGISTRY"
fi

# P1: discover + rank repos
echo "== [P1] discover_repos -> $REPOS_TSV"
python3 collect/discover_repos.py --registry "$REGISTRY" \
  --enrich "$ENRICH" --require-tests --require-permissive \
  --exclude-covered "$OUT" --out "$REPOS_TSV"

# P2: parallel mine top repos into one candidate pool
echo "== [P2] mine_all (top $TOP) -> $POOL"
python3 collect/mine_all.py --repos "$REPOS_TSV" --top "$TOP" \
  --jobs "$JOBS" --since "$SINCE" --prefilter $HARD_FLAG --out "$POOL"

# P4: parallel, resumable validation
echo "== [P4] validate -> $OUT"
python3 harness/swebench_eval.py validate "$POOL" --out "$OUT" \
  --jobs "$JOBS" --resume --gc-env --timeout "${TIMEOUT:-900}"

# strict view + harness sanity
echo "== deriving hard split + self-test"
python3 collect/build_hard_split.py --in "$OUT" --out "data/instances_hard.jsonl" || true

echo "== Tier A done. Validated: $(wc -l < "$OUT") instances in $OUT"
