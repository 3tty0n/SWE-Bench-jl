# Relationship to SWE-bench

SWE-bench-jl is built to the **same validation methodology and resolved contract** as the
official SWE-bench (Jimenez et al., 2024), adapted faithfully to the Julia ecosystem. This
note states precisely what is the same, what is different, and how to evaluate rigorously —
so the benchmark is not mistaken for, nor undersold against, the official one.

## What is the same (methodology parity)

| property | both SWE-bench and SWE-bench-jl |
|---|---|
| Task | a real GitHub bug-fix: `problem_statement` + `base_commit` + gold src `patch` + `test_patch` |
| Execution-based validation | every instance is confirmed to **FAIL at `base_commit + test_patch`** and **PASS after the gold `patch`** (base-fails / gold-passes invariant) — no instance enters the set unvalidated |
| Resolved criterion | **identical**: all `FAIL_TO_PASS` pass **and** all `PASS_TO_PASS` still pass (no regression) |
| Patch separation | gold `patch` is **src-only**; `test_patch` is **test-only** (the model never sees the tests as part of the fix) |
| Prediction format | `{instance_id, model_patch}` |
| Discrimination | the gold patch **resolves** and an **empty patch does not** — the `FAIL_TO_PASS` tests genuinely require the fix (measured: gold 100% resolved, empty 0% resolved on the 2-per-repo subset) |
| Schema | `swebenchjl export-official` emits the canonical SWE-bench field set (`version`, `environment_setup_commit`, `hints_text`, …) |

On these axes — the ones that make SWE-bench *rigorous* rather than merely a bug corpus —
SWE-bench-jl meets the bar of the **base** (auto-mined, execution-validated) SWE-bench.

## What is different (and why)

| dimension | official SWE-bench | SWE-bench-jl | implication |
|---|---|---|---|
| Language / ecosystem | Python (pytest, pip/conda) | Julia (`Test.jl`, `Pkg`) | enables Julia coverage — absent from SWE-bench and SWE-bench Multilingual as of 2026 |
| Test identity | pytest node-ids (`path::Class::test`) | `@testset` path (`A/B/C`) | Julia has no node-ids; the `/`-joined testset description chain is the analogue |
| Environment | per-instance **Docker image** + pinned conda | `Project.toml`/`Manifest.toml` + `Pkg.instantiate`, pinned `julia_version` | reproducible via exact Manifest pinning; **lighter, but not containerized/hermetic** |
| Problem statement | GitHub **issue** (the problem, not the solution) | 32 PR · 12 issue · 1 commit | PR/commit text can **leak** the intended fix — see "Rigorous evaluation" below |
| Human curation | **SWE-bench Verified** (500 instances, human-filtered for solvability + test specification) | none | SWE-bench-jl is at **base** quality, **not** Verified quality |
| Scale | 2,294 (full) · 300 (Lite) · 500 (Verified) | **45 (v0 seed)** | smaller by design at this stage; intended to grow |

## Rigorous evaluation on SWE-bench-jl

The one dimension where ours is weaker than the official issue-sourced set is
**problem-statement leakage**. To evaluate without leakage:

- **Exclude** `statement_source == "commit"` (1 instance) — the commit message describes the
  implementation.
- For the strictest split, restrict to `statement_source == "issue"` (12 instances), which
  match the official "describe the symptom, not the fix" standard (often with an MWE).
- Always report resolved-rate under the standard contract (F2P-flip ∧ P2P-hold).
- The **`PASS_TO_PASS` mass** (median 96, up to 468) — not the F2P count — is the difficulty
  and cost signal: a patch that fixes the bug but breaks one of hundreds of regression tests
  fails, and per-instance check time scales with this mass.

## Honest positioning (one sentence)

> SWE-bench-jl is the first execution-validated, SWE-bench-contract-compatible Julia benchmark;
> it matches the **base** SWE-bench's validation rigor, is **not** human-Verified, leans on
> PR-sourced problem statements (filter to issue-sourced for strict, non-leaky evaluation), and
> is a **45-instance v0 seed** meant to grow.
