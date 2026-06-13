# Baseline agent results (v0.1.0)

We ran three coding agents — **Claude Sonnet 4.6**, **Claude Haiku 4.5**, and **OpenAI Codex
(codex-cli)** — through the edit → `./check` loop on a 4-instance, 4-repo subset. Each agent
read the instance's problem statement, edited `src/`, and iterated against a check harness.
Every agent's *final* patch was then scored **independently** by the standalone evaluator
(`run-one --model-patch`): a run counts as **resolved** only if the diff is `src`-only (no
`test/` edits) **and** `FAIL_TO_PASS` flips while `PASS_TO_PASS` holds.

| instance (task) | Sonnet | Haiku | Codex |
|---|---|---|---|
| JuliaIO/JSON-450 — `SubString` keys | ✓ (4) | ✓ (1) | ✓ (4) |
| JuliaCollections/IterTools-80 — `zip_longest` | ✓ (2) | ✓ (15) | ✓ |
| JuliaMath/Combinatorics-150 — `derangements` | ✓ (1) | ✓ (4) | ✓ |
| JuliaCollections/DataStructures-869 — heap `T` Union/abstract | ✓ (1) | ✓ (1) | ✓ |
| **resolved** | **4 / 4** | **4 / 4** | **4 / 4** |

All 12 runs were src-only (no test tampering). Parenthesised numbers are the agent's
self-reported `./check` count; Haiku exceeded the 4-check budget on `zip_longest` (15 checks).

## Interpretation

- **The harness is sound.** Real agents run the loop, edit only `src/`, and their patches are
  scored under the same SWE-bench contract used for validation.
- **These v0 instances are solvable by all three agents (12/12).** Three of the four are
  "implement a named missing function/method" tasks where the failing-test name and the issue
  strongly cue the target; the fourth is a type-parameterization fix with a clear MWE.
  **Resolve-rate does not discriminate the agents at this difficulty.**
- **What varies is effort/cost, not resolution.** Check counts ranged 1–15, and Codex used
  roughly 10× the tokens of Haiku per instance — so an efficiency/cost axis separates the
  agents even where the binary outcome does not.
- **Implication for the dataset.** A *discriminating* benchmark needs harder, less-cued
  instances — subtler bugs and issue text that describes a *symptom*, not the target API.
  This is the priority for v0.2: grow N and bias mining toward genuinely hard bugs (and
  filter PR/commit-sourced statements that over-specify the fix).

## Reproduce

For each instance: clone at `base_commit`, apply `test_patch`, let an agent edit `src/`, then
collect each agent's src diff as a `model_patch` into `predictions.jsonl` and score:

```sh
swebenchjl eval data/instances.jsonl predictions.jsonl --out report.json
```

The full 45 × {agents} grid runs through the same harness; this 4-instance subset is the v0
pilot, extensible to the full set.
