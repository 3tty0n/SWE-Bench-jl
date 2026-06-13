# Schema reference

## Instance schema

An **instance** is one JSON object in `data/instances.jsonl`. Each field:

| field | meaning |
|---|---|
| `instance_id` | `org__name-<PR#>` (or `-<sha8>` when no PR number is identifiable) |
| `repo` | the GitHub package repo slug (`org/name.jl`) |
| `repo_url` | full HTTPS clone URL |
| `base_commit` | parent of the fix commit — the starting point for the model |
| `fix_commit` | the human fix commit (provenance; not given to the model) |
| `subject` | one-line subject of the fix commit |
| `problem_statement` | linked issue text; fallback: PR body → commit message (see `statement_source`) |
| `statement_source` | `issue` \| `pr` \| `commit` — provenance of `problem_statement` |
| `issue_url` | the linked GitHub issue URL, when one was resolved; otherwise `""` |
| `patch` | the gold fix, **src-only** unified diff (`src/**`, `ext/**`) |
| `test_patch` | the **test-only** part of the same commit (`test/**`) |
| `created_at` | author date of the fix commit (ISO 8601) |
| `FAIL_TO_PASS` | testset paths failing at `base_commit + test_patch`, passing after `patch` |
| `PASS_TO_PASS` | testset paths passing in both the pre and post test runs |
| `julia_version` | the Julia version the instance was validated under (e.g. `"julia version 1.12.6"`) |

## Prediction schema

A **prediction** is one JSON object per line in a predictions file:

| field | meaning |
|---|---|
| `instance_id` | must match an instance's `instance_id` |
| `model_patch` | a unified diff to apply on top of `base_commit + test_patch` |

## F2P / P2P semantics

- **FAIL_TO_PASS (F2P):** testset paths that fail at `base_commit + test_patch` and pass
  after the gold `patch` is applied. These are the target failures a correct patch must fix.
- **PASS_TO_PASS (P2P):** testset paths that pass in both the pre (base + test\_patch) and
  post (base + test\_patch + gold\_patch) runs. These are the regression guard: a patch must
  not break any P2P path.

Both sets are recorded from two real test runs during the validation protocol — they are
never inferred from the diff.

## Resolved criterion

A prediction `model_patch` is **resolved** if and only if, when applied at
`base_commit + test_patch`:

1. Every path in `FAIL_TO_PASS` has status `pass` in the post-patch run, **and**
2. Every path in `PASS_TO_PASS` has status `pass` in the post-patch run (no regression).

A testset path that is absent from the post-patch report is treated as failing.

## @testset-path node-id convention

Julia's `Test` stdlib has no pytest-style node IDs. SWE-bench-jl identifies a test unit
by the **`/`-joined chain of `@testset` description strings** from the root of the
testset tree to the node of interest — for example, `"parser/error handling"` identifies
the `@testset "error handling"` nested inside `@testset "parser"`.

A node's status is determined by its **direct** `@test` results only (child testsets are
their own units): a node is `pass` if it has zero test failures and zero test errors.

The **empty string `""`** is the conventional path for the implicit root testset — the
testset context that wraps the top-level `include("test/runtests.jl")` call. It appears
as an element of `FAIL_TO_PASS` or `PASS_TO_PASS` when the root-level `@test` assertions
(outside any named `@testset`) are relevant to the instance.

This `/`-joined description chain is the Julia analogue of a pytest node-id, and the
empty-string root is the Julia analogue of the module-level test collection node.
