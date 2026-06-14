# Tier A runbook — scaling SWE-bench-jl toward 300 instances

Operational guide for the Layer-A pipeline from [`scaling_plan.md`](scaling_plan.md):
milestone **M1 = P1 (repo discovery) + P2 (parallel mining) + P4 (parallel/resumable
validation)**. This is the "how to actually run it" companion to that plan.

```
discover_repos.py  ──►  repos.tsv  ──►  mine_all.py  ──►  candidates_all.jsonl  ──►  swebench_eval.py validate  ──►  instances.jsonl
      (P1)                                  (P2)                                            (P4)
```

All three stages are **idempotent and resumable**, so a run that dies (network, OOM,
a hung Julia precompile) can be re-launched and picks up where it left off.

---

## 0. Prerequisites & environment gotchas

| need | why |
|---|---|
| `python3` ≥ 3.11 | `tomllib` is stdlib; the collect/ scripts are pure Python |
| `gh` CLI authenticated | repo enrichment (P1) + issue/PR fetch (P2); `gh auth status` to check |
| `git` | clones + worktrees |
| Julia ≥ 1.12 | the only stage that needs Julia is P4 (validation) |

**Use the direct Julia binary, not the `juliaup` launcher.** Under `juliaup`, the PATH
`julia` is a launcher that re-resolves its channel on every call and takes a global
config lock — concurrent validator workers then serialize (or hang) on that lock. Point
`JULIA_BIN` at the real binary:

```bash
export JULIA_BIN="$(juliaup status >/dev/null 2>&1 && echo "$HOME"/.julia/juliaup/julia-*/bin/julia)"
# e.g. ~/.julia/juliaup/julia-1.12.6+0.x64.linux.gnu/bin/julia
```

**Parallelism vs. cores.** P4 `--jobs` is *process-level* instance parallelism. Julia
test runs are CPU-bound, so set `--jobs ≈ cores`. On a 1-core box `--jobs 1` is the honest
setting (the pool still works at `--jobs N`, but the runs just time-slice one core). Mining
(P2) is git/network-bound, so a few `--jobs` help even on one core.

---

## 1. P1 — discover the repo universe

Clone the registry once (offline source of truth; refresh occasionally):

```bash
git clone --depth 1 --filter=blob:none \
    https://github.com/JuliaRegistries/General work/General
```

Rank every mineable, pure-Julia, non-GPU GitHub repo and enrich the top slice:

```bash
python3 collect/discover_repos.py --registry work/General \
    --enrich 150 --require-tests --require-permissive \
    --exclude-covered data/instances.jsonl \
    --out collect/repos.tsv
```

- Offline filters (no API): drop `*_jll` wrapper packages, GPU/CUDA/ROCm-dependent
  packages, non-GitHub hosts, and repos with `< --min-versions` releases.
- Online filters (only the top `--enrich N`, cached in `work/discover_cache.json`):
  permissive SPDX license, has `test/runtests.jl`, not archived/fork, `≥ --min-stars`.
- `repos.tsv` is ranked (stars, then release count) with a `tier` + `est_instances`
  column and a running `cum_est`. **Read down until `cum_est ≈ 300` — those are Tier A.**

`--enrich 0` runs fully offline (fast, no `gh`) if you just want the ranking.

## 2. P2 — mine candidates in parallel

```bash
python3 collect/mine_all.py --repos collect/repos.tsv --top 30 \
    --jobs 4 --since 2019-01-01 --prefilter \
    --out work/candidates_all.jsonl
```

- Clones each repo (full history) under `work/repos/`, runs `mine_repo.py` **uncapped**
  (no more `--max-candidates 50`), writes per-repo `work/<org__name>.candidates.jsonl`
  (kept by `.gitignore` as provenance), then aggregates + de-dups into `--out`.
- `--prefilter` applies the **G5** static test/fix symbol-overlap filter: candidates whose
  added `@test`s reference no symbol the fix touches are dropped before validation (they
  almost always come back `no_fail_to_pass`). Validation stays the oracle; this only trims
  guaranteed misses.
- `--hard` mines the strict, non-leaky predicate (issue-sourced ∧ non-feature ∧
  edits-existing) directly.
- Re-running reuses existing per-repo candidate files; pass `--no-resume` to re-mine.

## 3. P4 — validate in parallel, resumably

```bash
JULIA_BIN="$JULIA_BIN" python3 harness/swebench_eval.py \
    validate work/candidates_all.jsonl --out data/instances.jsonl \
    --jobs "$(nproc)" --resume --gc-env --timeout 900
```

- `--jobs N` validates N candidates concurrently (process pool). Each instance has its own
  worktree + env; clone/fetch/worktree mutations on a shared repo clone are serialized with
  a per-repo file lock, so parallel workers on the same repo are safe.
- `--resume` skips any instance already in `work/logs/validate_summary.jsonl` and rebuilds
  `--out` from the durable `work/logs/validated.jsonl` cache — so an interrupted multi-hour
  run continues instead of restarting. (Keep `validated.jsonl`; it is the resume artifact.)
- `--gc-env` deletes each per-instance env after validation (keeps the resolved
  `Manifest.toml` in the instance log dir) to bound disk on large runs.
- `--flaky-runs K` runs pre/post `K` times and excludes any test path whose pass/fail
  status is inconsistent across runs (**G4**). `K=1` (default) reproduces the original
  single-run pipeline exactly. Use `K=3` for the published set.

Then derive the strict view and check the harness:

```bash
python3 collect/build_hard_split.py --in data/instances.jsonl --out data/instances_hard.jsonl
bin/swebenchjl --self-test
```

---

## 4. End-to-end (the scale-up command)

[`collect/run_tierA.sh`](../collect/run_tierA.sh) chains all three stages with the defaults
above. Resume-safe: re-run it after any interruption.

```bash
# how many repos to mine (top rows of repos.tsv); jobs default to nproc
TOP=30 JOBS=4 collect/run_tierA.sh
```

## 5. Expected yield & runtime

- Density (current data): ~9 validated instances/repo; validation yield ~40–56%.
- Tier A (300): the top ~9–28 rows of `repos.tsv` (large repos dominate the estimate).
- Runtime is dominated by P4. Rule of thumb: pre+post ≈ 120 s/instance (×K for flaky),
  divided by `--jobs`. A multi-thousand-candidate pool is an overnight job on one machine,
  or shard `candidates_all.jsonl` across a CI matrix (each shard `--resume` into a shared
  `work/logs/`). On a single core, run it as a long background job and let `--resume`
  absorb restarts.

## 6. What Layer A does *not* include

Deferred to Tier B/C per the plan: per-instance Julia-version selection + Manifest-resolve
fallback (P3), Docker/Apptainer hermetic images (G3), and LLM/human Verified-grade
curation (P5). Layer A pins one Julia version (recorded per instance) and one resolve path.
