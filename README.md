# SWE-bench-jl

**SWE-bench-jl** is, to our knowledge, the first **curated, execution-validated, Julia-specific SWE-bench-style benchmark with a pure-Julia evaluation harness**.

It packages **115 real GitHub bug-fix tasks** from **32 established Julia packages** using a SWE-bench-compatible instance schema. A candidate patch is judged by actually running the target package's Julia test suite before and after the patch. Evaluation requires Julia and git, but **does not require Docker or a Python runtime**.

## Why SWE-bench-jl?

SWE-bench-style benchmarks evaluate whether language models and coding agents can resolve real repository-level software problems. Existing benchmarks now cover a growing number of languages, and broad multilingual datasets such as SWE-rebench-V2 include Julia instances. However, Julia needs more than a mechanical port of a Python- or Docker-centered benchmark.

Julia packages commonly use:

* `Project.toml` / `Manifest.toml` for environment resolution,
* `test/runtests.jl` as the standard test-suite entry point,
* `@testset` rather than pytest-style node IDs,
* package-local dependency resolution through `Pkg`,
* and Julia-specific assumptions about load paths, precompilation, and test execution.

SWE-bench-jl adapts the SWE-bench contract to these realities. It replaces pytest-style node IDs with `@testset`-path identifiers, uses `Pkg.instantiate()` for per-instance environments, records `julia_version` per instance, and validates each instance through actual Julia test execution.

## Dataset card

SWE-bench-jl currently contains **115 execution-validated instances** mined from **32 pure-Julia packages** (growing as validation proceeds). Top contributors:

| Repository                               | Instances |
| ---------------------------------------- | --------: |
| `JuliaCollections/DataStructures.jl`     |        19 |
| `JuliaStats/Distances.jl`                |        10 |
| `JuliaMath/QuadGK.jl`                     |        10 |
| `JuliaCollections/OrderedCollections.jl` |         9 |
| `JuliaMath/Combinatorics.jl`             |         8 |
| `AlgebraicJulia/CliqueTrees.jl`          |         7 |
| `JuliaMath/Primes.jl`                    |         6 |
| `JuliaCollections/IterTools.jl`          |         5 |
| … and 24 more repositories               |        41 |
| **Total**                                |   **115** |

The full per-repo breakdown is in `data/instances.jsonl` (each record carries its `repo`).

The validated dataset is stored in:

```text
data/instances.jsonl
```

Each line is one JSON object representing a single benchmark instance.

See also:

* [`docs/dataset_card.md`](docs/dataset_card.md) — dataset card, mining protocol, validation protocol, and limitations
* [`docs/schema.md`](docs/schema.md) — instance schema, prediction schema, F2P/P2P semantics, and `@testset`-path convention
* [`docs/comparison_to_swebench.md`](docs/comparison_to_swebench.md) — comparison with the original SWE-bench design
* [`NOTICES.md`](NOTICES.md) — third-party data provenance and upstream licenses

## Scaling progress

SWE-bench-jl is actively scaling toward **SWE-bench Lite size (300 validated instances)** and beyond. The discovery → mining → validation pipeline now covers **300 Julia repositories**:

| stage | status |
| --- | --- |
| Repository discovery — rank the full [JuliaRegistries/General](https://github.com/JuliaRegistries/General) registry (~13.8k packages) into vetted, permissively-licensed, test-bearing repos | ✅ `collect/discover_repos.py` |
| Pull (clone + mine) the top **300 repositories** into a candidate pool | ✅ **9,916 candidate tasks** mined (280 / 300 repos producing) via `collect/pull_repos.sh` |
| Parallel, resumable validation of candidates into execution-validated instances | 🔄 in progress |

- **Validated dataset (today):** 115 instances from 32 repositories (`data/instances.jsonl`), growing as validation proceeds.
- **Candidate coverage:** 300 repositories / 9,916 mined candidates.
- **Reproduce the scale-up:** `N=300 collect/pull_repos.sh`, then validate with `harness/swebench_eval.py validate <pool> --out data/instances.jsonl --resume`; or run the whole pipeline with `collect/run_tierA.sh`. See [`docs/scaling_plan.md`](docs/scaling_plan.md) and [`docs/tierA_runbook.md`](docs/tierA_runbook.md).

## Relationship to SWE-bench

SWE-bench-jl follows the core SWE-bench idea: a task is resolved only if a model-generated patch makes previously failing tests pass while preserving previously passing tests.

A prediction is considered resolved if and only if, at:

```text
base_commit + test_patch + model_patch
```

both conditions hold:

1. every `FAIL_TO_PASS` testset path passes, and
2. every `PASS_TO_PASS` testset path still passes.

The `FAIL_TO_PASS` and `PASS_TO_PASS` sets are recorded from real validation runs. They are not inferred from the diff alone.

SWE-bench-jl differs from the original SWE-bench mainly in its Julia-specific execution model:

| Aspect             | SWE-bench                   | SWE-bench-jl                |
| ------------------ | --------------------------- | --------------------------- |
| Primary ecosystem  | Python                      | Julia                       |
| Test identifiers   | pytest node IDs             | `@testset`-path identifiers |
| Environment setup  | Docker-based images         | Julia `Pkg` environments    |
| Evaluation runtime | Python/Docker-based harness | pure-Julia harness          |
| Dataset scale      | large benchmark family      | 45-instance v0 seed         |
| Validation         | execution-based             | execution-based             |

SWE-bench-jl is therefore not intended to replace SWE-bench. It is a Julia-native adaptation of the same evaluation philosophy.

## Relationship to other Julia and multilingual benchmarks

SWE-bench-jl is related to, but distinct from, several existing benchmarks.

### SWE-bench Multilingual

[SWE-bench Multilingual](https://www.swebench.com/multilingual-leaderboard.html) extends SWE-bench to multiple programming languages. At the time of writing, it covers C, C++, Go, Java, JavaScript/TypeScript, PHP, Ruby, and Rust, but not Julia.

SWE-bench-jl complements SWE-bench Multilingual by targeting Julia specifically.

### SWE-rebench-V2

[SWE-rebench-V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2) is a broad multilingual SWE-bench-style dataset that includes Julia among many other languages.

SWE-bench-jl differs in scope and design. Rather than aiming for broad multilingual coverage, it provides a curated Julia-specific benchmark with a Julia-native execution harness, Julia package environment handling, and Julia testset-path based validation.

### JuliaBench

[JuliaBench](https://github.com/JuliaBench/JuliaBench) provides benchmark collections for evaluating LLMs on Julia software-engineering tasks. It is especially oriented toward benchmark creation and agent training/evaluation within the Julia ecosystem.

SWE-bench-jl is complementary: it focuses specifically on real GitHub bug-fix tasks, SWE-bench-compatible instance records, and execution-validated F2P/P2P resolution.

### Julia-LLM-Leaderboard

[Julia-LLM-Leaderboard](https://github.com/svilupp/Julia-LLM-Leaderboard) evaluates LLMs on Julia code-generation tasks. It is useful for measuring Julia coding ability, but it is not a repository-level issue-resolution benchmark.

SWE-bench-jl instead targets repository-level patch generation and regression-aware validation.

## Install

Clone the repository and instantiate the Julia environment:

```bash
git clone https://github.com/3tty0n/SWE-Bench-jl.git
cd SWE-Bench-jl
julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

Requirements:

* Julia `>= 1.12`
* `git` on `PATH`

Python is needed only to re-mine instances from scratch. It is not needed to evaluate predictions.

## Quickstart

Run the in-process self-test:

```bash
bin/swebenchjl --self-test
```

Evaluate one instance with the gold patch:

```bash
bin/swebenchjl run-one data/instances.jsonl JuliaCollections__IterTools-103 --gold
```

Evaluate a predictions file:

```bash
bin/swebenchjl eval data/instances.jsonl predictions.jsonl --out report.json
```

Export to canonical SWE-bench-style JSONL:

```bash
bin/swebenchjl export-official data/instances.jsonl --out official.jsonl
```

## Prediction format

A predictions file is a JSONL file. Each line must contain one prediction:

```json
{"instance_id": "JuliaCollections__DataStructures-966", "model_patch": "<unified diff>"}
```

The `model_patch` field should contain a unified diff that can be applied to the target repository at the instance's base commit.

## Instance format

Each instance records the information needed to reproduce the task and evaluate a candidate patch, including:

* `instance_id`
* `repo`
* `base_commit`
* `problem_statement`
* `patch`
* `test_patch`
* `FAIL_TO_PASS`
* `PASS_TO_PASS`
* Julia-specific environment metadata

See [`docs/schema.md`](docs/schema.md) for the complete schema.

## Resolved criterion

A prediction is resolved if the candidate patch satisfies the benchmark's F2P/P2P contract.

At evaluation time, SWE-bench-jl checks:

```text
base_commit + test_patch + model_patch
```

The result is considered resolved only when:

* all `FAIL_TO_PASS` testset paths pass, and
* all `PASS_TO_PASS` testset paths still pass.

This guards against patches that only fix the target failing behavior while breaking existing behavior.

## Problem-statement provenance

Some instances may use PR-derived problem statements. For stricter non-leaky evaluation, filter to instances whose statement source is issue-derived.

The relevant metadata is recorded in the dataset. See [`docs/dataset_card.md`](docs/dataset_card.md) for details.

## Re-mining instances

The validated dataset is already included in `data/instances.jsonl`.

To re-mine instances from scratch, use the scripts under:

```text
collect/
```

Python 3.10+ is required only for this mining workflow.

Evaluation of existing predictions does not require Python.

## Evaluation requirements

Running `eval` or `run-one` requires:

* Julia `>= 1.12`
* `git`
* network access for the first clone of each target repository
* enough disk space for temporary worktrees and package environments

Set `JULIA_BIN` if you want to use a specific Julia executable:

```bash
JULIA_BIN=/path/to/julia bin/swebenchjl eval data/instances.jsonl predictions.jsonl --out report.json
```

## Limitations

SWE-bench-jl is an initial v0 seed benchmark. Important limitations include:

* still modest scale: 115 instances from 32 repositories (scaling in progress; see above),
* focus on pure-Julia packages,
* no human-verified subset yet,
* possible leakage for PR-derived problem statements,
* dependency on package availability and Julia version compatibility,
* and incomplete coverage of the wider Julia package ecosystem.

The goal of this release is to provide a transparent, executable, Julia-native starting point for repository-level coding-agent evaluation.

## Recommended use

SWE-bench-jl is suitable for:

* evaluating coding agents on Julia repository-level bug fixing,
* testing Julia-aware patch generation systems,
* comparing model behavior on Julia against Python-centric SWE-bench results,
* studying how language-specific package managers and test frameworks affect SWE-style evaluation,
* and developing Julia-native evaluation harnesses for coding agents.

For strict evaluation, prefer issue-derived instances when available and report the exact instance subset used.

## Citation

If you use SWE-bench-jl in research, please cite:

```bibtex
@software{swebench_jl_2026,
  author    = {Yusuke Izawa},
  title     = {{SWE-bench-jl}: An Execution-Validated Julia SWE-bench-Style Benchmark},
  year      = {2026},
  url       = {https://github.com/3tty0n/SWE-Bench-jl}
}
```

See also [`CITATION.cff`](CITATION.cff) for machine-readable citation metadata.

## License

The SWE-bench-jl harness code is released under the MIT License. See [`LICENSE`](LICENSE).

Benchmark instances are derived from upstream open-source repositories. See [`NOTICES.md`](NOTICES.md) for provenance and third-party license information.

