#!/bin/bash
# Reproduce the kiln x SWE-bench-jl execution-mode experiment end to end:
#   prepare  ->  drive (parallel)  ->  replay (SOLO, clean timing)  ->  score  ->  report
#
# Workload = real coding-agent edit->check loops over the SWE-bench-jl hard subset; each
# episode's recorded attempt sequence is replayed under cold / daemon / kiln and timed.
#
# Config via env (all optional):
#   SWEBENCH_JL  path to the standalone SWE-bench-jl repo   (has data/ + bin/swebenchjl)
#   INSTANCES    instances JSONL                            (default: hard subset)
#   AGENTS       comma list                                 (default: haiku,sonnet,codex)
#   MODES        comma list                                 (default: cold,daemon,kiln)
#   JOBS         parallel drive concurrency                 (default: 5)
#   MAX_CHECKS   per-episode ./check budget                 (default: 4)
#   DEST         episode-workspace root (under benchmark/swebench)  (default: work/kiln_eval)
#   JULIA_BIN    real julia binary (NEVER the juliaup launcher; auto-detected if unset)
#
# Usage:  benchmark/swebench/harness/reproduce_kiln_eval.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1                       # benchmark/swebench
KE="$HERE/kiln_eval.py"

# This script ships inside the SWE-bench-jl repo (harness/), so the repo root is $HERE/..
SWEBENCH_JL="${SWEBENCH_JL:-$(cd "$HERE/.." && pwd)}"
INSTANCES="${INSTANCES:-$SWEBENCH_JL/data/instances_hard.jsonl}"
AGENTS="${AGENTS:-haiku,sonnet,codex}"
MODES="${MODES:-cold,daemon,kiln}"
JOBS="${JOBS:-5}"
MAX_CHECKS="${MAX_CHECKS:-4}"
DEST="${DEST:-work/kiln_eval}"

# Real julia binary: bare `julia` is the juliaup launcher and blocks on a config lock.
export JULIA_BIN="${JULIA_BIN:-$(ls "$HOME"/.julia/juliaup/julia-*/bin/julia 2>/dev/null | tail -1)}"
[ -x "$JULIA_BIN" ] || { echo "ERROR: no real julia binary found; set JULIA_BIN" >&2; exit 1; }
[ -f "$INSTANCES" ] || { echo "ERROR: instances not found: $INSTANCES" >&2; exit 1; }

IDS="$(python3 -c "import json,sys; print(','.join(json.loads(l)['instance_id'] for l in open('$INSTANCES') if l.strip()))")"
echo "instances: $IDS"
echo "agents=$AGENTS modes=$MODES jobs=$JOBS julia=$JULIA_BIN"

echo "=== [1/5] prepare $(date -u +%H:%M:%S) ==="
python3 "$KE" prepare "$INSTANCES" --ids "$IDS" --agents "$AGENTS" --dest-root "$DEST"

echo "=== [2/5] drive (parallel x$JOBS) $(date -u +%H:%M:%S) ==="
drive_one() {
  ws="$1"; ag="${ws##*__}"
  big=$(ls -S "$ws"/.kilnswe/attempts/attempt_*.patch 2>/dev/null | head -1)
  if [ -n "$big" ] && [ -s "$big" ]; then echo "skip(driven) ${ws##*/}"; return; fi
  python3 "$KE" drive --dest-root "$DEST" --workspace "$ws" --agent "$ag" \
    --max-checks "$MAX_CHECKS" --timeout 1000 >/dev/null 2>&1
  echo "done ${ws##*/} ($(ls "$ws"/.kilnswe/attempts/attempt_*.patch 2>/dev/null | wc -l) attempts)"
}
export -f drive_one
export KE DEST MAX_CHECKS
ls -d "$DEST"/*__*/ | sed 's:/$::' | xargs -P "$JOBS" -I{} bash -c 'drive_one "$@"' _ {}

echo "=== [3/5] replay (SOLO, clean timing) [$MODES] $(date -u +%H:%M:%S) ==="
python3 "$KE" replay "$INSTANCES" --dest-root "$DEST" --modes "$MODES"

echo "=== [4/5] score $(date -u +%H:%M:%S) ==="
python3 "$KE" score "$INSTANCES" --dest-root "$DEST" --swebench-jl "$SWEBENCH_JL"

echo "=== [5/5] report $(date -u +%H:%M:%S) ==="
python3 "$KE" report --dest-root "$DEST" | tee "$DEST/report.txt"
echo "=== DONE $(date -u +%H:%M:%S) -> $DEST/report.txt ==="
