# evaluate.jl -- orchestration: validate / eval / run-one.
#
# Faithful port of cmd_validate/_validate_one, cmd_eval/_eval_one, cmd_run_one from
# swebench_eval.py. EVERY verdict string, the per-instance eval record shape, and the
# aggregate report shape are preserved exactly. eval/run-one drive the test run through
# an `AbstractBackend` (default `ColdBackend`) so kiln can plug its own backend; the
# verdict logic is identical regardless.

const DEFAULT_TIMEOUT = 900

# ---------------------------------------------------------------------------
# small helpers (mirror the Python module utilities)
# ---------------------------------------------------------------------------

function _log_err(log::AbstractString, filename::AbstractString, msg::AbstractString)
    mkpath(log)
    open(joinpath(log, filename), "a") do fh
        write(fh, msg, "\n")
    end
end

function _cleanup_wt(clone::AbstractString, wt::AbstractString, log::AbstractString, keep::Bool)
    keep || remove_worktree(clone, wt, log)
end

# Index a report's tests for the eval/run-one coverage counts. Same last-wins
# semantics as report.jl's _index_report; we only need the lookup dict here.
function _coverage_index(report)
    _, idx = _index_report(report)
    return idx
end

# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

"""
    validate(candidates, out; julia=..., work=..., timeout=900, keep_worktrees=false, limit=0)

Validate each candidate instance: clone repo, apply test+gold patches, run tests
pre/post, compute F2P/P2P, and write valid instances to `out` (JSONL). Mirrors Python
`cmd_validate`. Returns `(valid_count, total_count)`.
"""
function validate(candidates::AbstractString, out::AbstractString;
                  julia::AbstractString="julia", work::AbstractString="work",
                  timeout::Integer=DEFAULT_TIMEOUT, keep_worktrees::Bool=false,
                  limit::Integer=0)
    out_path = String(out)
    mkpath(dirname(abspath(out_path)))

    summary_log = joinpath(work, "logs", "validate_summary.jsonl")
    mkpath(dirname(summary_log))

    jver = julia_version(julia)

    cands = load_instances(candidates)
    if limit > 0
        cands = cands[1:min(limit, length(cands))]
    end

    n_valid = 0
    n_total = 0

    open(out_path, "w") do out_fh
        for inst in cands
            iid = String(inst["instance_id"])
            n_total += 1
            verdict, n_f2p, n_p2p, pre_ms, post_ms =
                _validate_one(inst, work, julia, timeout, keep_worktrees, jver)
            if verdict == "valid"
                n_valid += 1
                write(out_fh, JSON3.write(inst), "\n")
            end
            println(stderr, "[validate] $(iid): $(verdict) f2p=$(n_f2p) p2p=$(n_p2p)")
            open(summary_log, "a") do sl
                write(sl, JSON3.write(Dict(
                    "instance_id" => iid, "verdict" => verdict,
                    "n_f2p" => n_f2p, "n_p2p" => n_p2p,
                    "pre_ms" => pre_ms, "post_ms" => post_ms,
                )), "\n")
            end
        end
    end

    println(stderr, "\n[validate] done: $(n_valid)/$(n_total) valid")
    return n_valid, n_total
end

function _validate_one(inst, work::AbstractString, julia::AbstractString,
                       timeout::Integer, keep_wt::Bool, jver)
    iid = String(inst["instance_id"])
    repo = String(inst["repo"])
    repo_url = String(inst["repo_url"])
    base_sha = String(inst["base_commit"])
    fix_sha = String(inst["fix_commit"])
    test_patch = get(inst, "test_patch", "")
    gold_patch = get(inst, "patch", "")

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "base")
    env = env_dir(work, iid)

    n_f2p = 0; n_p2p = 0
    pre_ms = 0.0; post_ms = 0.0

    try
        ensure_clone(repo_url, clone, [base_sha, fix_sha], logs)
    catch exc
        _log_err(logs, "clone_error.log", sprint(showerror, exc))
        return "clone_failed", n_f2p, n_p2p, pre_ms, post_ms
    end

    if !create_worktree(clone, wt, base_sha, logs)
        return "worktree_failed", n_f2p, n_p2p, pre_ms, post_ms
    end

    try
        if !isempty(test_patch) && !apply_patch(test_patch, wt, logs, "test_patch")
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "test_patch_failed", n_f2p, n_p2p, pre_ms, post_ms
        end

        mkpath(env)
        if !build_env(julia, wt, env, timeout, logs)
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "env_failed", n_f2p, n_p2p, pre_ms, post_ms
        end

        pre_json = joinpath(logs, "report_pre.json")
        local pre_report
        try
            pre_report, pre_ms = run_suite(julia, env, wt, pre_json, timeout, logs, "pre")
        catch e
            e isa TimeoutError || rethrow()
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "timeout_pre", n_f2p, n_p2p, pre_ms, post_ms
        end

        if pre_report === nothing || !get(pre_report, "ok", false)
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "broken_at_base", n_f2p, n_p2p, pre_ms, post_ms
        end

        if !isempty(gold_patch) && !apply_patch(gold_patch, wt, logs, "gold_patch")
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "gold_patch_failed", n_f2p, n_p2p, pre_ms, post_ms
        end

        post_json = joinpath(logs, "report_post.json")
        local post_report
        try
            post_report, post_ms = run_suite(julia, env, wt, post_json, timeout, logs, "post")
        catch e
            e isa TimeoutError || rethrow()
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "timeout_post", n_f2p, n_p2p, pre_ms, post_ms
        end

        if post_report === nothing || !get(post_report, "ok", false)
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "gold_broken", n_f2p, n_p2p, pre_ms, post_ms
        end

        f2p, p2p, new_fail = diff_reports(pre_report, post_report)
        n_f2p = length(f2p)
        n_p2p = length(p2p)

        if n_f2p < 1
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "no_fail_to_pass", n_f2p, n_p2p, pre_ms, post_ms
        end

        if length(new_fail) > 0
            _cleanup_wt(clone, wt, logs, keep_wt)
            return "regression", n_f2p, n_p2p, pre_ms, post_ms
        end

        inst["FAIL_TO_PASS"] = f2p
        inst["PASS_TO_PASS"] = p2p
        inst["julia_version"] = jver
        _cleanup_wt(clone, wt, logs, keep_wt)
        return "valid", n_f2p, n_p2p, pre_ms, post_ms

    catch exc
        # Mirrors Python `except Exception`: catches everything reaching this point,
        # INCLUDING a build_env timeout (Python's TimeoutExpired is an Exception). The
        # inner pre/post blocks already intercept their own timeouts -> timeout_pre/post.
        _log_err(logs, "unexpected.log", sprint(showerror, exc))
        _cleanup_wt(clone, wt, logs, keep_wt)
        return "error", n_f2p, n_p2p, pre_ms, post_ms
    end
end

# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

"""
    evaluate(instances, predictions, out; backend=ColdBackend(julia; timeout),
             julia=..., work=..., timeout=900, keep_worktrees=false)

Apply model patches, run tests, check F2P/P2P coverage, and write a resolve-rate
report JSON to `out`. Mirrors Python `cmd_eval`. Returns the aggregate report dict
`{total, resolved, resolve_rate, instances}`.
"""
function evaluate(instances::AbstractString, predictions::AbstractString,
                  out::AbstractString; backend::AbstractBackend=ColdBackend("julia"),
                  julia::AbstractString="julia", work::AbstractString="work",
                  timeout::Integer=DEFAULT_TIMEOUT, keep_worktrees::Bool=false)
    out_path = String(out)
    mkpath(dirname(abspath(out_path)))

    ids, insts = load_instances_by_id_ordered(instances)
    preds = load_instances_by_id(predictions)

    results = Vector{Any}()
    n_resolved = 0

    for iid in ids
        inst = insts[iid]
        pred = get(preds, iid, nothing)
        pred === nothing && continue
        rec = _eval_one(inst, get(pred, "model_patch", ""), work, julia, timeout,
                        keep_worktrees, backend)
        push!(results, rec)
        rec.resolved && (n_resolved += 1)
        println(stdout, "[eval] $(iid): $(rec.status) f2p=$(rec.f2p_pass)/$(rec.f2p_total)")
    end

    total = length(results)
    rate = total > 0 ? n_resolved / total : 0.0
    # NamedTuple preserves Python's dict key ORDER {total, resolved, resolve_rate,
    # instances} on serialization (a Julia Dict would scramble it).
    report = (total=total, resolved=n_resolved, resolve_rate=rate, instances=results)
    open(out_path, "w") do fh
        JSON3.pretty(fh, JSON3.write(report), JSON3.AlignmentContext(indent=2))
    end

    println(stdout, "\n[eval] resolved $(n_resolved)/$(total) ($(_pct(rate)))")
    return report
end

# Format a rate as Python's "{:.1%}" (e.g. 0.5 -> "50.0%").
_pct(rate::Real) = Printf.@sprintf("%.1f%%", rate * 100)

function _eval_one(inst, model_patch, work::AbstractString, julia::AbstractString,
                   timeout::Integer, keep_wt::Bool, backend::AbstractBackend)
    iid = String(inst["instance_id"])
    repo = String(inst["repo"])
    repo_url = String(inst["repo_url"])
    base_sha = String(inst["base_commit"])
    test_patch = get(inst, "test_patch", "")
    f2p = get(inst, "FAIL_TO_PASS", Any[])
    p2p = get(inst, "PASS_TO_PASS", Any[])

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "pred")
    env = env_dir(work, iid)

    # Per-instance record. NamedTuple preserves Python's exact key ORDER
    # {instance_id, resolved, f2p_pass, f2p_total, p2p_ok, status, check_ms}. Keyword
    # overrides mirror Python's `{**base_rec, "status": ...}` spread.
    base_rec(; resolved=false, f2p_pass=0, f2p_total=length(f2p), p2p_ok=false,
             status="error", check_ms=0.0) =
        (instance_id=iid, resolved=resolved, f2p_pass=f2p_pass, f2p_total=f2p_total,
         p2p_ok=p2p_ok, status=status, check_ms=check_ms)

    try
        ensure_clone(repo_url, clone, [base_sha], logs)
    catch exc
        _log_err(logs, "clone_error.log", sprint(showerror, exc))
        return base_rec()
    end

    if !create_worktree(clone, wt, base_sha, logs)
        return base_rec(status="error")
    end

    try
        if !isempty(test_patch) && !apply_patch(test_patch, wt, logs, "test_patch_pred")
            _cleanup_wt(clone, wt, logs, keep_wt)
            return base_rec(status="error")
        end

        if !isempty(model_patch) && !apply_patch(model_patch, wt, logs, "model_patch")
            _cleanup_wt(clone, wt, logs, keep_wt)
            return base_rec(status="patch_apply_failed")
        end

        # always rebuild: the env's Manifest dev-paths the worktree it was
        # built against, which may no longer exist (deps stay depot-cached)
        wd = WorkDir(wt, env, logs, "pred")
        if !prepare!(backend, inst, wd)
            _cleanup_wt(clone, wt, logs, keep_wt)
            return base_rec(status="error")
        end

        local report, check_ms
        try
            report, check_ms = run_check(backend, wd)
        catch e
            e isa TimeoutError || rethrow()
            _cleanup_wt(clone, wt, logs, keep_wt)
            return base_rec(status="timeout", check_ms=timeout * 1000.0)
        end

        if report === nothing
            _cleanup_wt(clone, wt, logs, keep_wt)
            return base_rec(status="error", check_ms=check_ms)
        end

        idx = _coverage_index(report)
        f2p_pass = count(p -> get(idx, String(p), nothing) == "pass", f2p)
        p2p_ok = all(p -> get(idx, String(p), nothing) == "pass", p2p)
        resolved = check_resolved(report, f2p, p2p)
        status = resolved ? "resolved" : "unresolved"

        _cleanup_wt(clone, wt, logs, keep_wt)
        return (instance_id=iid, resolved=resolved, f2p_pass=f2p_pass,
                f2p_total=length(f2p), p2p_ok=p2p_ok, status=status, check_ms=check_ms)

    catch exc
        # Mirrors Python `except Exception`: a build/prepare timeout (TimeoutExpired is
        # an Exception in Python) lands here as "error"; the run_check timeout is caught
        # by the inner block above -> "timeout".
        _log_err(logs, "unexpected.log", sprint(showerror, exc))
        _cleanup_wt(clone, wt, logs, keep_wt)
        return base_rec(status="error")
    end
end

# ---------------------------------------------------------------------------
# run-one
# ---------------------------------------------------------------------------

"""
    run_one(instances, instance_id; gold=false, model_patch=nothing,
            backend=ColdBackend(julia; timeout), julia=..., work=..., timeout=900)

Debug helper: run a single instance, leaving the worktree in place. Mirrors Python
`cmd_run_one`. Prints the same lines to stdout/stderr and throws on the same fatal
conditions (the CLI maps the thrown error to `exit(1)`).
"""
function run_one(instances::AbstractString, instance_id::AbstractString;
                 gold::Bool=false, model_patch=nothing,
                 backend::AbstractBackend=ColdBackend("julia"),
                 julia::AbstractString="julia", work::AbstractString="work",
                 timeout::Integer=DEFAULT_TIMEOUT)
    insts = load_instances_by_id(instances)
    inst = get(insts, instance_id, nothing)
    if inst === nothing
        println(stderr, "instance_id not found: $(instance_id)")
        throw(RunOneError(1))
    end

    iid = String(inst["instance_id"])
    repo = String(inst["repo"])
    repo_url = String(inst["repo_url"])
    base_sha = String(inst["base_commit"])
    test_patch = get(inst, "test_patch", "")
    f2p = get(inst, "FAIL_TO_PASS", Any[])
    p2p = get(inst, "PASS_TO_PASS", Any[])

    logs = log_dir(work, iid)
    clone = repo_dir(work, repo)
    wt = wt_dir(work, iid, "runone")
    env = env_dir(work, iid)

    ensure_clone(repo_url, clone, [base_sha], logs)
    if !create_worktree(clone, wt, base_sha, logs)
        println(stderr, "worktree creation failed")
        throw(RunOneError(1))
    end

    if !isempty(test_patch) && !apply_patch(test_patch, wt, logs, "test_patch_ro")
        println(stderr, "test_patch application failed")
        throw(RunOneError(1))
    end

    if gold
        gold_patch = get(inst, "patch", "")
        if !isempty(gold_patch) && !apply_patch(gold_patch, wt, logs, "gold_patch_ro")
            println(stderr, "gold patch application failed")
            throw(RunOneError(1))
        end
    elseif model_patch !== nothing
        mp = read(String(model_patch), String)
        if !isempty(mp) && !apply_patch(mp, wt, logs, "model_patch_ro")
            println(stderr, "model patch application failed")
            throw(RunOneError(1))
        end
    end

    # always rebuild: the env Manifest dev-paths whichever worktree built it
    wd = WorkDir(wt, env, logs, "runone")
    local built
    try
        built = prepare!(backend, inst, wd)
    catch e
        e isa TimeoutError || rethrow()
        println(stderr, "env build timeout")
        throw(RunOneError(1))
    end
    if !built
        println(stderr, "env build failed")
        throw(RunOneError(1))
    end

    out_json = joinpath(logs, "report_runone.json")
    local report, check_ms
    try
        report, check_ms = run_check(backend, wd)
    catch e
        e isa TimeoutError || rethrow()
        println(stderr, "timeout")
        throw(RunOneError(1))
    end

    println("report: $(out_json)")
    println("worktree: $(wt)")
    if report === nothing
        println("run_tests produced no JSON")
        throw(RunOneError(1))
    end

    ok = get(report, "ok", false)
    tests = get(report, "tests", Any[])
    n_pass = count(t -> t["status"] == "pass", tests)
    n_fail = count(t -> t["status"] == "fail", tests)
    elapsed_s = get(report, "elapsed_s", 0)
    println("ok=$(ok)  pass=$(n_pass)  fail=$(n_fail)  elapsed_s=$(_fmt2(elapsed_s))")

    if !isempty(f2p) || !isempty(p2p)
        resolved = check_resolved(report, f2p, p2p)
        idx = _coverage_index(report)
        f2p_pass = count(p -> get(idx, String(p), nothing) == "pass", f2p)
        p2p_ok = all(p -> get(idx, String(p), nothing) == "pass", p2p)
        println("resolved=$(resolved)  f2p=$(f2p_pass)/$(length(f2p))  p2p_ok=$(p2p_ok)")
    end
    return report
end

# Thrown to signal the CLI to exit(1) (mirrors the Python sys.exit(1) sites).
struct RunOneError <: Exception
    code::Int
end

# ---------------------------------------------------------------------------
# self-test (mirrors cmd_self_test)
# ---------------------------------------------------------------------------

"""
    self_test() -> Bool

Run the in-process unit checks for `diff_reports` / `check_resolved`, mirroring the
Python `--self-test`. Prints the same `ok`/`FAIL` lines. Returns `true` if all checks
pass, `false` otherwise (the CLI maps `false` to `exit(1)`).
"""
function self_test()
    failures = String[]

    function check(name, got, want)
        if got != want
            push!(failures, "FAIL $(name): got $(repr(got)), want $(repr(want))")
        else
            println("  ok  $(name)")
        end
    end

    println("=== self-test: diff_reports ===")

    pre = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test1", "status" => "fail"),
        Dict("path" => "a/b/test2", "status" => "pass"),
        Dict("path" => "a/b/test3", "status" => "fail"),
        Dict("path" => "a/b/test4", "status" => "pass"),
    ])
    post = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test1", "status" => "pass"),   # F2P
        Dict("path" => "a/b/test2", "status" => "pass"),   # P2P
        Dict("path" => "a/b/test3", "status" => "fail"),   # still fail
        # test4 missing -> NEW_FAIL
    ])

    f2p, p2p, new_fail = diff_reports(pre, post)
    check("f2p", sort(f2p), ["a/b/test1"])
    check("p2p", sort(p2p), ["a/b/test2"])
    check("new_fail", sort(new_fail), ["a/b/test4"])

    pre2 = Dict("ok" => true, "tests" => Any[
        Dict("path" => "x", "status" => "fail"),
        Dict("path" => "y", "status" => "fail"),
    ])
    post2 = Dict("ok" => true, "tests" => Any[
        Dict("path" => "x", "status" => "pass"),
        Dict("path" => "y", "status" => "pass"),
    ])
    f2p2, p2p2, nf2 = diff_reports(pre2, post2)
    check("all_f2p", sort(f2p2), ["x", "y"])
    check("no_p2p", p2p2, String[])
    check("no_new_fail", nf2, String[])

    println("\n=== self-test: check_resolved ===")

    report_ok = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test1", "status" => "pass"),
        Dict("path" => "a/b/test2", "status" => "pass"),
    ])
    check("resolved_true", check_resolved(report_ok, ["a/b/test1"], ["a/b/test2"]), true)

    report_bad = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test1", "status" => "fail"),
        Dict("path" => "a/b/test2", "status" => "pass"),
    ])
    check("resolved_false_f2p_fail", check_resolved(report_bad, ["a/b/test1"], ["a/b/test2"]), false)

    report_missing = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test2", "status" => "pass"),
    ])
    check("resolved_false_f2p_missing", check_resolved(report_missing, ["a/b/test1"], ["a/b/test2"]), false)

    report_p2p_bad = Dict("ok" => true, "tests" => Any[
        Dict("path" => "a/b/test1", "status" => "pass"),
        Dict("path" => "a/b/test2", "status" => "fail"),
    ])
    check("resolved_false_p2p_fail", check_resolved(report_p2p_bad, ["a/b/test1"], ["a/b/test2"]), false)

    check("empty_f2p_p2p", check_resolved(report_ok, String[], String[]), true)

    println()
    if !isempty(failures)
        for f in failures
            println(stderr, f)
        end
        return false
    else
        println("all self-tests passed")
        return true
    end
end

# Format a number like Python's "{:.2f}".
_fmt2(x::Real) = Printf.@sprintf("%.2f", float(x))
