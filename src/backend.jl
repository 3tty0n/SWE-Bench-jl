# backend.jl -- pluggable execution backend.
#
# `AbstractBackend` lets the orchestration (evaluate.jl) drive the env build + test
# run through a swappable interface. `ColdBackend` is the faithful port of the
# Python harness: it builds a fresh env via testenv.jl and runs a FRESH run_tests.jl
# subprocess for each check. kiln can later provide a warm backend that reuses
# compilation, without changing the verdict logic.

# Locate the standalone subprocess scripts shipped with the package. These run in
# the per-instance test env (which does NOT contain SWEBenchJl), so they must stay
# stdlib-only and be invoked by subprocess, never `include`d here.
const _SCRIPTS_DIR = joinpath(@__DIR__, "scripts")
testenv_script() = joinpath(_SCRIPTS_DIR, "testenv.jl")
run_tests_script() = joinpath(_SCRIPTS_DIR, "run_tests.jl")

"""
    build_env(julia, wt, env, timeout, log) -> Bool

Build the per-instance test env by SUBPROCESS:
`julia --startup-file=no <scripts/testenv.jl> <wt> <env>`. Mirrors Python `build_env`.
"""
function build_env(julia::AbstractString, wt::AbstractString, env::AbstractString,
                   timeout::Integer, log::AbstractString)
    script = testenv_script()
    rc, _, _ = run_cmd([String(julia), "--startup-file=no", script,
                        String(wt), String(env)];
                       timeout=timeout, log_path=joinpath(log, "env.log"))
    return rc == 0
end

"""
    run_suite(julia, env, wt, out_json, timeout, log, label) -> (report_or_nothing, elapsed_ms)

Run the test suite by SUBPROCESS:
`julia --startup-file=no --project=<env> <scripts/run_tests.jl> <wt> <out_json>`.
Returns the parsed report dict (or `nothing` if the JSON is absent/unparseable) and
the wall-clock elapsed milliseconds. Mirrors Python `run_tests` (renamed `run_suite`
in the public API to avoid colliding with the script name). Propagates `TimeoutError`
on timeout, exactly as Python lets `subprocess.TimeoutExpired` propagate.
"""
function run_suite(julia::AbstractString, env::AbstractString, wt::AbstractString,
                   out_json::AbstractString, timeout::Integer,
                   log::AbstractString, label::AbstractString)
    script = run_tests_script()
    t0 = time()
    run_cmd([String(julia), "--startup-file=no", "--project=$(env)", script,
             String(wt), String(out_json)];
            timeout=timeout, log_path=joinpath(log, "$(label).out"))
    elapsed_ms = (time() - t0) * 1000.0
    if !isfile(out_json)
        return nothing, elapsed_ms
    end
    try
        return read_report(out_json), elapsed_ms
    catch
        return nothing, elapsed_ms
    end
end

# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------

"""
    AbstractBackend

Interface for running an instance's test suite. Concrete backends implement
`prepare!(b, instance, workdir)` (set up / build the env for a prepared worktree)
and `run_check(b, workdir) -> report` (run the suite and return the parsed report
dict, or `nothing`).
"""
abstract type AbstractBackend end

"""
    ColdBackend(julia; timeout=900)

Faithful port of the Python harness backend: builds a fresh env with testenv.jl and
runs a fresh run_tests.jl subprocess per check. No state is reused between checks.
"""
struct ColdBackend <: AbstractBackend
    julia::String
    timeout::Int
end
ColdBackend(julia::AbstractString; timeout::Integer=900) = ColdBackend(String(julia), Int(timeout))

# A prepared unit of work that `prepare!`/`run_check` operate on. `workdir` bundles
# the worktree, env, and log dirs plus a label for this run.
struct WorkDir
    wt::String
    env::String
    log::String
    label::String
end

"""
    prepare!(b::ColdBackend, instance, workdir) -> Bool

Build the env for the (already patched) worktree, mirroring the Python harness'
"always rebuild" step. Returns `true` on success.
"""
function prepare!(b::ColdBackend, instance, workdir::WorkDir)
    mkpath(workdir.env)
    return build_env(b.julia, workdir.wt, workdir.env, b.timeout, workdir.log)
end

"""
    run_check(b::ColdBackend, workdir) -> (report_or_nothing, check_ms)

Run a fresh run_tests.jl subprocess and return the parsed report (or `nothing`) and
elapsed milliseconds. Propagates `TimeoutError` on timeout.
"""
function run_check(b::ColdBackend, workdir::WorkDir)
    out_json = joinpath(workdir.log, "report_$(workdir.label).json")
    return run_suite(b.julia, workdir.env, workdir.wt, out_json, b.timeout,
                     workdir.log, workdir.label)
end
