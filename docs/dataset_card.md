# Dataset card

## Overview

**45** execution-validated instances mined from 5 pure-Julia packages.

## Per-repo distribution

| repo | instances |
|---|---|
| JuliaCollections/DataStructures.jl | 19 |
| JuliaCollections/OrderedCollections.jl | 9 |
| JuliaMath/Combinatorics.jl | 8 |
| JuliaCollections/IterTools.jl | 5 |
| JuliaIO/JSON.jl | 4 |
| **total** | **45** |

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

**Mining yield:** 80 candidates were mined across the 5 repos and run through validation;
45 became validated instances. The main rejection reasons were:

| rejection | count | meaning |
|---|---|---|
| `no_fail_to_pass` | 32 | suite already passed at base even with `test_patch` applied |
| `timeout_pre` | 2 | base + `test_patch` suite exceeded the run timeout |
| `broken_at_base` | 1 | suite errored (not just failed) at base |

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

- **Small N:** 45 instances is enough for an initial benchmark, but aggregate resolve
  rates will have high variance.
- **5 repos:** All instances come from five pure-Julia packages in the
  JuliaCollections/JuliaMath/JuliaIO GitHub organizations. Coverage of the broader Julia
  ecosystem is limited.
- **Pure-Julia packages only:** Packages with C/Fortran extensions, Python wrappers, or
  GPU backends are excluded because environment build and test execution are more complex.
- **`statement_source` caveat:** When `statement_source == "commit"`, the
  `problem_statement` is the commit message, which may contain or strongly hint at the
  fix. Filter these instances out for strict zero-contamination evaluation.
