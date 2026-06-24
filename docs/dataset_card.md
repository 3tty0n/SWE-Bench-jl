# Dataset card

## Overview

**1,888** execution-validated instances mined from **165** pure-Julia packages, released
in three nested tiers (each a strict subset of the one above) plus a non-leaky Hard split:

| tier | instances | repos | file |
|---|---:|---:|---|
| Full | 1,888 | 165 | `data/instances.jsonl` |
| Lite | 1,518 | 157 | `data/instances_lite.jsonl` |
| Verified | 1,021 | 128 | `data/instances_verified.jsonl` |
| Hard (non-leaky) | 243 | — | `data/instances_hard.jsonl` |

Tier criteria are defined and reproducible in [`collect/split_tiers.py`](../collect/split_tiers.py).
By problem-statement provenance the dataset is **1,406 PR-sourced, 320 issue-sourced, 162
commit-sourced**; all instances were validated under Julia 1.12.6.

## Per-repo distribution (top contributors)

| repo | instances |
|---|---|
| AlgebraicJulia/Catlab.jl | 129 |
| JuliaEarth/CoordRefSystems.jl | 108 |
| jump-dev/MutableArithmetics.jl | 79 |
| ITensor/ITensors.jl | 78 |
| KeitaNakamura/Tensorial.jl | 72 |
| SciML/SciMLOperators.jl | 52 |
| ajwheeler/Korg.jl | 47 |
| Sienna-Platform/InfrastructureSystems.jl | 44 |
| JuliaArrays/FillArrays.jl | 42 |
| JuliaData/Parsers.jl | 35 |
| … and 155 more repositories | 1,202 |
| **total** | **1,888** |

The full per-repo breakdown is in `data/instances.jsonl` (each record carries its `repo`).

## Mining protocol

Instances were mined from each repo's git history by `collect/mine_repo.py`, which walks
first-parent commits from a cutoff date and accepts a commit as a candidate when it:

- touches both `src/**` (or `ext/**`) **and** `test/**` in the same commit,
- has a fix-like commit message (contains `fix`, `bug`, `close`, or a GitHub issue
  reference such as `#NNN`),
- stays within size caps (`--max-src-files`, `--max-src-lines`) so diffs are
  model-tractable,
- does not touch `Project.toml` or `Manifest.toml` (so the dependency set is stable
  across the `base_commit` → `fix_commit` transition).

The gold `patch` is the `src/**` + `ext/**` slice of the accepted commit; `test_patch`
is the `test/**` slice. Issue and PR text is fetched with the `gh` CLI when
authenticated; `statement_source` records the provenance:

- `issue` — text came from a linked GitHub issue (most reliable; cannot contain the fix).
- `pr` — text came from the pull request body.
- `commit` — text is the commit message itself (can leak the fix — filter on
  `statement_source == "commit"` for strict evaluation).

**Mining yield:** 12,384 candidates were mined across 300 repositories and run through
validation; 1,850 passed (≈15% yield). After dropping 6 instances whose suites generate
non-deterministic `@testset` names (≈1M unstable node IDs each) and adding 44 earlier seed
instances, the dataset is 1,888. The rejection breakdown over the 12,384 candidates was:

| rejection | count | meaning |
|---|---:|---|
| `no_fail_to_pass` | 6,060 | suite already passed at base even with `test_patch` applied |
| `env_failed` | 3,077 | per-instance environment failed to build/instantiate |
| *(valid)* | 1,850 | passed all gates |
| `error` | 553 | worker crashed or unexpected error |
| `timeout_pre` | 550 | base + `test_patch` suite exceeded the run timeout |
| `regression` | 113 | gold patch broke a previously passing testset |
| `test_patch_failed` | 61 | `test_patch` did not apply at base |
| `broken_at_base` | 47 | suite errored (not just failed) at base |
| `timeout_post` | 45 | post-patch suite exceeded the run timeout |
| `gold_patch_failed` | 25 | gold `patch` did not apply at base |
| `gold_broken` | 3 | gold patch left the suite broken |

## Validation protocol

Every instance in `data/instances.jsonl` was execution-validated before inclusion:

1. Clone the repo; create a detached git worktree at `base_commit`; apply `test_patch`.
2. Build the per-instance test environment (`Pkg.instantiate`) and run the suite
   (**pre** run): it must produce at least one failure.
3. Apply the gold `patch` and run the suite again (**post** run): the pre-failures must
   flip to pass and no previously passing testset may regress.
4. Record `FAIL_TO_PASS` (fail → pass) and `PASS_TO_PASS` (pass in both runs) from
   these two real runs, never inferred from the diff.

## Environment model

Each instance is evaluated in a hermetic Julia environment. The evaluator:

- creates a git worktree at `base_commit`,
- `Pkg.develop`s the package into an environment derived from the package's
  `test/Project.toml` (or `[extras]` + `[targets].test` in the root `Project.toml`),
- runs `Pkg.instantiate` to pin all deps to the `Manifest.toml` at `base_commit`, and
- spawns Julia with `--project=<that env>`.

`julia_version` records the Julia binary used during validation (e.g.
`"julia version 1.12.6"`). The environment is the unit of dependency isolation; the
benchmark does not depend on any outer Julia project state.

## Limitations

- **No human-verified subset yet:** the Verified tier is rule-based (well-specified,
  size-bounded, edits-only), not human-audited like SWE-bench Verified.
- **Pure-Julia packages only:** Packages with C/Fortran extensions, Python wrappers, or
  GPU backends are excluded because environment build and test execution are more complex.
- **Single Julia version:** all instances were validated under Julia 1.12.6; behavior on
  other versions is not guaranteed.
- **`statement_source` caveat:** for the 1,406 PR-sourced and 162 commit-sourced instances,
  the `problem_statement` may contain or hint at the fix. Use `data/instances_hard.jsonl`
  (243 issue-sourced, non-leaky instances) for strict zero-contamination evaluation.
