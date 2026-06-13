# SWE-bench-jl

SWE-bench-jl is, to our knowledge, the first curated, execution-validated, Julia-specific
SWE-bench-style benchmark. It packages 45 real GitHub bug-fix tasks from five established
Julia packages with a SWE-bench-compatible instance schema and a pure-Julia evaluation
harness that decides *resolved* by actually running each package's test suite before and
after a candidate patch — no Docker, no Python runtime needed at evaluation time.

## Why SWE-bench-jl

SWE-bench and its successors cover Python and a handful of other languages, but as of
mid-2026 none of them include Julia. Julia's testing model is also different enough that a
mechanical port is not feasible: there are no pytest-style node IDs, the de-facto suite
entry point is `test/runtests.jl`, and environments are resolved through
`Project.toml`/`Manifest.toml` rather than pip/conda. SWE-bench-jl adapts the SWE-bench
contract to these realities — replacing pytest node-IDs with `@testset`-path identifiers,
using `Pkg.instantiate` for hermetic per-instance environments, and pinning
`julia_version` per instance — so that the resolved criterion, the F2P/P2P split, and the
validation protocol carry over faithfully.

## Dataset card

45 execution-validated instances mined from 5 pure-Julia packages.

| repo | instances |
|---|---|
| JuliaCollections/DataStructures.jl | 19 |
| JuliaCollections/OrderedCollections.jl | 9 |
| JuliaMath/Combinatorics.jl | 8 |
| JuliaCollections/IterTools.jl | 5 |
| JuliaIO/JSON.jl | 4 |
| **total** | **45** |

The validated dataset is in `data/instances.jsonl` (one JSON object per line).
See [docs/dataset_card.md](docs/dataset_card.md) for the full dataset card and mining
protocol, and [docs/schema.md](docs/schema.md) for the instance and prediction schemas.

## Install

```sh
julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

Requires **Julia >= 1.12** and **git** on `PATH`. Python is needed only to re-mine
instances, not to evaluate predictions.

## Quickstart

**Run the in-process self-test** (no network or Julia subprocess needed):

```sh
bin/swebenchjl --self-test
```

**Evaluate one instance with the gold patch** (needs network for the first clone):

```sh
bin/swebenchjl run-one data/instances.jsonl JuliaCollections__IterTools-103 --gold
```

**Evaluate a predictions file:**

```sh
bin/swebenchjl eval data/instances.jsonl predictions.jsonl --out report.json
```

**Export to canonical SWE-bench JSONL** (remaps Julia-specific fields to the upstream
SWE-bench schema):

```sh
bin/swebenchjl export-official data/instances.jsonl --out official.jsonl
```

## Prediction format

Each line of a predictions file is a JSON object with two fields:

```json
{"instance_id": "JuliaCollections__DataStructures-966", "model_patch": "<unified diff>"}
```

## Resolved criterion

A prediction is **resolved** if and only if, at `base_commit + test_patch + model_patch`:

- every `FAIL_TO_PASS` testset path passes, **and**
- every `PASS_TO_PASS` testset path still passes (no regression).

Both sets are recorded from two real test runs during validation — never inferred from
the diff. See [docs/schema.md](docs/schema.md) for the `@testset`-path node-id convention.

## Relationship to SWE-bench

SWE-bench-jl follows the **same validation methodology and resolved contract** as the official
[SWE-bench](https://www.swebench.com), adapted to Julia: identical execution-based validation
(base-fails / gold-passes), identical `FAIL_TO_PASS` ∧ `PASS_TO_PASS` resolved criterion, and
src/test patch separation. Measured on the 2-per-repo subset, **gold patches resolve 100%** and
**empty patches resolve 0%** — the `FAIL_TO_PASS` tests genuinely require the fix. It differs in
environment (`Project.toml`/`Manifest.toml` instead of Docker), problem-statement provenance
(partly PR-sourced — filter to `statement_source == "issue"` for strict, non-leaky evaluation),
and scale (a 45-instance v0 seed, **not** human-Verified). Full comparison:
[docs/comparison_to_swebench.md](docs/comparison_to_swebench.md).

## Evaluation requirements

Running `eval` or `run-one` needs:
- Julia >= 1.12 (set `JULIA_BIN` or rely on `julia` on `PATH`)
- `git` (for worktrees and `git apply`)

Python is **not** needed to evaluate predictions. Python 3.10+ is needed only to
re-mine instances from scratch (`collect/mine_repo.py`).

## Documentation

- [docs/schema.md](docs/schema.md) — instance schema, prediction schema, F2P/P2P semantics, `@testset`-path convention
- [docs/dataset_card.md](docs/dataset_card.md) — dataset card, mining protocol, limitations
- [docs/comparison_to_swebench.md](docs/comparison_to_swebench.md) — what is the same / different vs the official SWE-bench
- [docs/baselines.md](docs/baselines.md) — baseline agent resolve-rates (Sonnet / Haiku / Codex)
- [NOTICES.md](NOTICES.md) — third-party data provenance and upstream licenses
- [LICENSE](LICENSE) — MIT license for the harness code

## Citation

```bibtex
@software{swebench_jl_2026,
  author    = {Yusuke Izawa},
  title     = {{SWE-bench-jl}: An Execution-Validated Julia SWE-bench-Style Benchmark},
  year      = {2026},
  url       = {https://github.com/3tty0n/SWE-bench-jl}
}
```

See also [CITATION.cff](CITATION.cff) for machine-readable citation metadata.
