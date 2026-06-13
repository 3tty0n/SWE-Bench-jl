module SWEBenchJl

# SWE-bench-jl evaluator: a pure-Julia port of benchmark/swebench/harness/swebench_eval.py.
#
# This module is the orchestrator/evaluator. It runs in the MAIN process and drives the
# per-instance env build + test run by SUBPROCESS, using the self-contained scripts in
# src/scripts/ (testenv.jl, run_tests.jl, testreport.jl) which are stdlib-only because
# the test env does NOT contain this package.

import JSON3
import Printf

# Package-code (main-process) sources, included in dependency order.
include("instances.jl")   # load_instances / dump / by-id loaders
include("report.jl")      # read_report / diff_reports / check_resolved (B1 last-wins, B2 "")
include("gitops.jl")      # run_cmd / ensure_clone / worktree / 3-method apply_patch
include("backend.jl")     # AbstractBackend / ColdBackend / build_env / run_suite
include("evaluate.jl")    # validate / evaluate / run_one / self_test
include("official.jl")    # export_official: canonical SWE-bench JSONL export

export load_instances, build_env, run_suite,
       read_report, diff_reports, check_resolved,
       validate, evaluate, run_one,
       AbstractBackend, ColdBackend,
       export_official

end # module SWEBenchJl
