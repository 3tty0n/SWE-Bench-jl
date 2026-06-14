#!/usr/bin/env bash
# validate_shards.sh — split a candidate pool into K disjoint repo-shards for
# parallel / distributed validation (the F2P/P2P-making step).
#
# Validation is CPU-bound Julia execution: on a single core, running shards in
# parallel does NOT add throughput (they time-slice one core). The win is on a
# MULTI-CORE box or a CI matrix, where each shard validates on its own core/node.
# Sharding is by REPO so each repository is cloned exactly once across shards
# (no duplicated clones), and each shard uses its own --work dir so their
# validate_summary / resume caches never collide.
#
# Usage:
#   collect/validate_shards.sh K [POOL]      # write K shard files + print run plan
#   RUN=1 collect/validate_shards.sh K [POOL]# also launch all K shards in background (multi-core)
#   collect/validate_shards.sh merge [OUT]   # merge every shard's valids into OUT
#
# Each shard k -> shard list work/vshard_k.tsv, work dir work/val_k, output work/val_k.valid.jsonl.
# Drive shards with Haiku/Sonnet agents (one per shard) on a multi-core machine, or
# fan them across CI jobs; then run the `merge` step.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

JULIA_BIN="${JULIA_BIN:-$(ls -d "$HOME"/.julia/juliaup/julia-*/bin/julia 2>/dev/null | head -1 || echo julia)}"
TIMEOUT="${TIMEOUT:-600}"

if [ "${1:-}" = "merge" ]; then
  OUT="${2:-work/pool_all.valid.jsonl}"
  python3 - "$OUT" <<'PY'
import glob, json, sys
out=sys.argv[1]; seen={}
for f in sorted(glob.glob('work/val_*.valid.jsonl')):
    for l in open(f):
        l=l.strip()
        if l:
            r=json.loads(l); seen[r['instance_id']]=r   # last wins
with open(out,'w') as fh:
    for r in seen.values(): fh.write(json.dumps(r)+'\n')
print(f"merged {len(seen)} validated instances from {len(glob.glob('work/val_*.valid.jsonl'))} shards -> {out}")
PY
  exit 0
fi

K="${1:?usage: validate_shards.sh K [POOL] | merge [OUT]}"
POOL="${2:-work/pool_all_candidates.jsonl}"
[ -f "$POOL" ] || { echo "pool not found: $POOL"; exit 1; }

# split candidates into K shards by repo (round-robin repos -> balanced, no shared clones)
python3 - "$POOL" "$K" <<'PY'
import json, sys, collections
pool, K = sys.argv[1], int(sys.argv[2])
rows=[json.loads(l) for l in open(pool) if l.strip()]
by=collections.OrderedDict()
for r in rows: by.setdefault(r['repo'], []).append(r)
repos=sorted(by, key=lambda k: len(by[k]))           # small repos first within each shard
shards=[[] for _ in range(K)]
for i,repo in enumerate(repos): shards[i%K].extend(by[repo])
for k,sh in enumerate(shards):
    with open(f'work/vshard_{k}.tsv','w') as f:
        for r in sh: f.write(json.dumps(r)+'\n')
print(f"{len(rows)} candidates / {len(repos)} repos -> {K} shards "
      f"(sizes: {[len(s) for s in shards]})")
PY

echo
echo "Run each shard (separate core / CI job / Haiku agent), then merge:"
for ((k=0; k<K; k++)); do
  echo "  JULIA_BIN=$JULIA_BIN python3 harness/swebench_eval.py --work work/val_$k validate work/vshard_$k.tsv --out work/val_$k.valid.jsonl --resume --gc-env --timeout $TIMEOUT"
done
echo "  collect/validate_shards.sh merge work/pool_all.valid.jsonl"

if [ "${RUN:-0}" = "1" ]; then
  echo; echo "RUN=1: launching $K shards in the background ..."
  for ((k=0; k<K; k++)); do
    JULIA_BIN=$JULIA_BIN nohup python3 harness/swebench_eval.py --work work/val_$k \
      validate work/vshard_$k.tsv --out work/val_$k.valid.jsonl \
      --resume --gc-env --timeout "$TIMEOUT" > work/val_$k.log 2>&1 &
    echo "  shard $k -> pid $! (log work/val_$k.log)"
  done
  echo "wait for all, then: collect/validate_shards.sh merge"
fi
