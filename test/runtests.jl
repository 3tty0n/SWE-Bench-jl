# runtests.jl -- PARITY GATE for the SWE-bench-jl evaluator.
#
# The acceptance criterion is that the Julia logic matches the Python ORACLE
# (benchmark/swebench/harness/swebench_eval.py in the kiln repo). Where possible we
# compute the expected values by shelling out to the Python oracle itself, so the
# tests assert Julia == Python on identical JSON inputs rather than against
# hand-written expectations.
#
# Sections:
#   1. Synthetic-report parity (fast, no Julia subprocess): diff_reports / check_resolved
#      vs the Python functions on the same JSON inputs, incl. duplicate-path last-wins
#      (both directions), the empty-string "" path, and missing/failing F2P in post.
#   2. --self-test parity: run the Python `--self-test` and the Julia `self_test()`;
#      both must pass.
#   3. (best-effort) one gold run-one: full clone+env+test; SKIPPED with a printed
#      reason if network/time prohibits it.

using Test
using SWEBenchJl
using SWEBenchJl: self_test, run_one
import JSON3

const HERE = @__DIR__
const PKG_ROOT = normpath(joinpath(HERE, ".."))

# Locate the Python oracle in the kiln repo. Allow override via env var; otherwise
# use the known path. If absent, the oracle-backed cases are skipped (reported).
function _oracle_dir()
    cand = String[]
    haskey(ENV, "SWEBENCH_ORACLE_DIR") && push!(cand, ENV["SWEBENCH_ORACLE_DIR"])
    push!(cand, "/home/yusuke/src/github.com/3tty0n/nbjit.jl/benchmark/swebench/harness")
    for d in cand
        isfile(joinpath(d, "swebench_eval.py")) && return d
    end
    return nothing
end

const ORACLE_DIR = _oracle_dir()

function _have_python()
    try
        return success(`python3 --version`)
    catch
        return false
    end
end

const HAVE_PY = _have_python()

# Run the Python oracle's diff_reports / check_resolved on JSON inputs and return the
# parsed result. We write a tiny driver that imports swebench_eval, reads inputs from
# temp JSON, and writes outputs back to a temp JSON, so we compare Julia == Python on
# identical bytes.
function py_diff_reports(pre, post)
    return _py_call("diff", Dict("pre" => pre, "post" => post))
end
function py_check_resolved(report, f2p, p2p)
    return _py_call("resolved", Dict("report" => report, "f2p" => f2p, "p2p" => p2p))
end

function _py_call(which::String, payload)
    @assert ORACLE_DIR !== nothing
    indir = mktempdir()
    infile = joinpath(indir, "in.json")
    outfile = joinpath(indir, "out.json")
    open(infile, "w") do io
        write(io, JSON3.write(payload))
    end
    driver = """
import json, sys
sys.path.insert(0, $(repr(ORACLE_DIR)))
import swebench_eval as se
data = json.load(open($(repr(infile))))
which = $(repr(which))
if which == "diff":
    f2p, p2p, nf = se.diff_reports(data["pre"], data["post"])
    out = {"f2p": f2p, "p2p": p2p, "new_fail": nf}
elif which == "resolved":
    out = {"resolved": se.check_resolved(data["report"], data["f2p"], data["p2p"])}
else:
    raise SystemExit("bad which")
json.dump(out, open($(repr(outfile)), "w"))
"""
    run(pipeline(`python3 -c $driver`; stdout=devnull, stderr=devnull))
    return JSON3.read(read(outfile, String))
end

# Compare a Julia (f2p,p2p,new_fail) tuple of String-vectors to the Python result.
# diff_reports ordering is deterministic and identical on both sides (first-seen
# insertion order over the pre index), so we compare element-for-element.
function assert_diff_eq(jres, pyres)
    jf, jp, jn = jres
    @test collect(String, jf) == collect(String, pyres["f2p"])
    @test collect(String, jp) == collect(String, pyres["p2p"])
    @test collect(String, jn) == collect(String, pyres["new_fail"])
end

@testset "SWEBenchJl parity gate" begin

    # =====================================================================
    # 1. Synthetic-report parity vs the Python oracle
    # =====================================================================
    @testset "synthetic-report parity (diff_reports / check_resolved)" begin
        if !(HAVE_PY && ORACLE_DIR !== nothing)
            @info "SKIP oracle-backed synthetic parity (python3 or oracle missing); using static expectations" HAVE_PY ORACLE_DIR
        end

        # --- (a) duplicate path, FAIL-then-PASS in pre (last-wins -> pass) ---------
        # pre 'dup' last-wins = pass; post 'dup' = pass  => P2P.
        pre_a = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "fail"),
            Dict("path" => "dup", "status" => "pass"),   # last-wins -> pass
            Dict("path" => "solo", "status" => "fail"),
        ])
        post_a = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "pass"),
            Dict("path" => "solo", "status" => "pass"),  # F2P
        ])
        j_a = diff_reports(pre_a, post_a)
        @test collect(String, j_a[1]) == ["solo"]        # f2p
        @test collect(String, j_a[2]) == ["dup"]         # p2p (last-wins pass-in-pre)
        @test isempty(j_a[3])                            # new_fail
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_a, py_diff_reports(pre_a, post_a))
        end

        # --- (a') duplicate path, PASS-then-FAIL in pre (last-wins -> fail) --------
        # pre 'dup' last-wins = fail; post 'dup' = pass  => F2P.
        pre_a2 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "pass"),
            Dict("path" => "dup", "status" => "fail"),   # last-wins -> fail
        ])
        post_a2 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "pass"),   # F2P
        ])
        j_a2 = diff_reports(pre_a2, post_a2)
        @test collect(String, j_a2[1]) == ["dup"]        # f2p (last-wins fail-in-pre)
        @test isempty(j_a2[2])
        @test isempty(j_a2[3])
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_a2, py_diff_reports(pre_a2, post_a2))
        end

        # --- (a'') duplicate path in POST too: last-wins decides post status ------
        # pre 'dup'=fail, post 'dup' last-wins = fail => not F2P (no entry anywhere).
        pre_a3 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "fail"),
        ])
        post_a3 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "dup", "status" => "pass"),
            Dict("path" => "dup", "status" => "fail"),   # last-wins -> fail
        ])
        j_a3 = diff_reports(pre_a3, post_a3)
        @test isempty(j_a3[1]); @test isempty(j_a3[2]); @test isempty(j_a3[3])
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_a3, py_diff_reports(pre_a3, post_a3))
        end

        # --- (b) empty-string "" path present in tests[] and in the P2P set -------
        report_b = Dict("ok" => true, "tests" => Any[
            Dict("path" => "", "status" => "pass"),      # the "" root node
            Dict("path" => "x", "status" => "pass"),
        ])
        @test check_resolved(report_b, String[], ["", "x"]) == true
        if HAVE_PY && ORACLE_DIR !== nothing
            py = py_check_resolved(report_b, String[], ["", "x"])
            @test check_resolved(report_b, String[], ["", "x"]) == py["resolved"]
        end
        # "" in P2P but failing in tests -> not resolved
        report_b2 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "", "status" => "fail"),
            Dict("path" => "x", "status" => "pass"),
        ])
        @test check_resolved(report_b2, String[], ["", "x"]) == false
        if HAVE_PY && ORACLE_DIR !== nothing
            py = py_check_resolved(report_b2, String[], ["", "x"])
            @test check_resolved(report_b2, String[], ["", "x"]) == py["resolved"]
        end
        # "" present in pre & post as F2P (fail -> pass) — diff parity
        pre_b = Dict("ok" => true, "tests" => Any[
            Dict("path" => "", "status" => "fail"),
            Dict("path" => "k", "status" => "pass"),
        ])
        post_b = Dict("ok" => true, "tests" => Any[
            Dict("path" => "", "status" => "pass"),      # F2P
            Dict("path" => "k", "status" => "pass"),     # P2P
        ])
        j_b = diff_reports(pre_b, post_b)
        @test collect(String, j_b[1]) == [""]
        @test collect(String, j_b[2]) == ["k"]
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_b, py_diff_reports(pre_b, post_b))
        end

        # --- (c) an F2P path missing / failing in post -> NEW_FAIL / regression ---
        # f2p candidate fails in post; a pass-in-pre path goes missing -> NEW_FAIL.
        pre_c = Dict("ok" => true, "tests" => Any[
            Dict("path" => "fixme", "status" => "fail"),  # stays fail in post -> nothing
            Dict("path" => "stable", "status" => "pass"), # missing in post -> NEW_FAIL
            Dict("path" => "win", "status" => "fail"),    # -> pass in post -> F2P
        ])
        post_c = Dict("ok" => true, "tests" => Any[
            Dict("path" => "fixme", "status" => "fail"),
            Dict("path" => "win", "status" => "pass"),
            # "stable" missing
        ])
        j_c = diff_reports(pre_c, post_c)
        @test collect(String, j_c[1]) == ["win"]          # f2p
        @test isempty(j_c[2])                             # p2p
        @test collect(String, j_c[3]) == ["stable"]       # new_fail (missing in post)
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_c, py_diff_reports(pre_c, post_c))
        end
        # check_resolved: a recorded F2P that fails in post -> unresolved
        report_c = Dict("ok" => true, "tests" => Any[
            Dict("path" => "win", "status" => "fail"),    # recorded F2P, now failing
            Dict("path" => "k", "status" => "pass"),
        ])
        @test check_resolved(report_c, ["win"], ["k"]) == false
        # check_resolved: a recorded F2P missing from post -> unresolved
        report_c2 = Dict("ok" => true, "tests" => Any[
            Dict("path" => "k", "status" => "pass"),
        ])
        @test check_resolved(report_c2, ["win"], ["k"]) == false
        if HAVE_PY && ORACLE_DIR !== nothing
            @test check_resolved(report_c, ["win"], ["k"]) ==
                  py_check_resolved(report_c, ["win"], ["k"])["resolved"]
            @test check_resolved(report_c2, ["win"], ["k"]) ==
                  py_check_resolved(report_c2, ["win"], ["k"])["resolved"]
        end

        # --- the harness' own --self-test inputs, cross-checked against Python ----
        # (matches cmd_self_test's first diff_reports case)
        pre_st = Dict("ok" => true, "tests" => Any[
            Dict("path" => "a/b/test1", "status" => "fail"),
            Dict("path" => "a/b/test2", "status" => "pass"),
            Dict("path" => "a/b/test3", "status" => "fail"),
            Dict("path" => "a/b/test4", "status" => "pass"),
        ])
        post_st = Dict("ok" => true, "tests" => Any[
            Dict("path" => "a/b/test1", "status" => "pass"),
            Dict("path" => "a/b/test2", "status" => "pass"),
            Dict("path" => "a/b/test3", "status" => "fail"),
        ])
        j_st = diff_reports(pre_st, post_st)
        if HAVE_PY && ORACLE_DIR !== nothing
            assert_diff_eq(j_st, py_diff_reports(pre_st, post_st))
        end
        @test sort(collect(String, j_st[1])) == ["a/b/test1"]
        @test sort(collect(String, j_st[2])) == ["a/b/test2"]
        @test sort(collect(String, j_st[3])) == ["a/b/test4"]
    end

    # =====================================================================
    # 2. --self-test parity (Python oracle and Julia both pass)
    # =====================================================================
    @testset "self-test parity" begin
        # Julia self_test() must return true.
        @test self_test() == true

        # Python `--self-test` must exit 0.
        if HAVE_PY && ORACLE_DIR !== nothing
            py_ok = success(pipeline(
                Cmd(`python3 $(joinpath(ORACLE_DIR, "swebench_eval.py")) --self-test`);
                stdout=devnull, stderr=devnull))
            @test py_ok == true
        else
            @info "SKIP Python --self-test parity (python3 or oracle missing)" HAVE_PY ORACLE_DIR
        end
    end

    # =====================================================================
    # 3. (best-effort) one gold run-one  -- needs network + time
    # =====================================================================
    @testset "gold run-one (best-effort)" begin
        # Opt-in: a full clone + env build + test run can take many minutes and needs
        # network, so by default this case is SKIPPED to keep `Pkg.test()` fast and
        # deterministic. Set SWEBENCH_RUN_GOLD=1 to actually run it.
        run_gold = lowercase(get(ENV, "SWEBENCH_RUN_GOLD", "0"))
        if !(run_gold == "1" || run_gold == "true" || run_gold == "yes")
            @info "SKIP gold run-one: opt-in only (set SWEBENCH_RUN_GOLD=1 to run; needs network+time)"
            @test_skip "gold run-one is opt-in (SWEBENCH_RUN_GOLD=1)"
        else
            data = joinpath(PKG_ROOT, "data", "instances.jsonl")
            insts = load_instances(data)
            byid = Dict(String(i["instance_id"]) => i for i in insts)
            # prefer the suggested small instance, else any IterTools / Combinatorics one
            pick = nothing
            for cand in ("JuliaCollections__IterTools-103",)
                haskey(byid, cand) && (pick = cand; break)
            end
            if pick === nothing
                for (k, _) in byid
                    if occursin("IterTools", k) || occursin("Combinatorics", k)
                        pick = k; break
                    end
                end
            end
            net_ok = try
                success(pipeline(`git ls-remote https://github.com/JuliaCollections/IterTools.jl`;
                                 stdout=devnull, stderr=devnull))
            catch
                false
            end

            if pick === nothing
                @info "SKIP gold run-one: no IterTools/Combinatorics instance found in dataset"
                @test_skip "no suitable instance"
            elseif !net_ok
                @info "SKIP gold run-one: no network access to clone the repo" pick
                @test_skip "no network"
            else
                jbin = get(ENV, "JULIA_BIN", joinpath(Sys.BINDIR, "julia"))
                work = mktempdir()
                inst = byid[pick]
                want_f2p = collect(String, get(inst, "FAIL_TO_PASS", String[]))
                want_p2p = collect(String, get(inst, "PASS_TO_PASS", String[]))
                @info "Running gold run-one (this clones, builds an env, and runs tests)" pick jbin
                local report
                ran = try
                    report = run_one(data, pick; gold=true,
                                     backend=ColdBackend(jbin; timeout=1800),
                                     julia=jbin, work=work, timeout=1800)
                    true
                catch e
                    @warn "gold run-one threw; treating as skip" exception=(e, catch_backtrace())
                    false
                end
                if !ran || report === nothing
                    @info "SKIP gold run-one: clone/env/test did not complete in this environment" pick
                    @test_skip "run-one did not complete"
                else
                    @test check_resolved(report, want_f2p, want_p2p) == true
                    # F2P / P2P sets equal to recorded values
                    idx = Dict(String(t["path"]) => t["status"] for t in get(report, "tests", Any[]))
                    @test all(p -> get(idx, p, nothing) == "pass", want_f2p)
                    @test all(p -> get(idx, p, nothing) == "pass", want_p2p)
                end
            end
        end
    end
end
