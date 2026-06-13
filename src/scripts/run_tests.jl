include(joinpath(@__DIR__, "testreport.jl"))

function main()
    length(ARGS) >= 2 || (println(stderr, "usage: run_tests.jl <repo_dir> <out_json>"); exit(2))
    repo_dir = abspath(ARGS[1])
    out_json = abspath(ARGS[2])
    runtests = joinpath(repo_dir, "test", "runtests.jl")
    # many runtests.jl select test files from ARGS; ours must not leak in
    empty!(ARGS)

    cd(repo_dir)
    ts = nothing
    ok = true
    err = nothing
    elapsed = 0.0
    t0 = time()
    try
        ts = @testset ReportingTestSet "" begin
            include(runtests)
        end
        elapsed = time() - t0
    catch e
        elapsed = time() - t0
        ok = false
        msg = sprint(showerror, e)
        err = length(msg) > 4000 ? msg[1:4000] : msg
        ts = nothing
    end

    write_report(out_json, ts, elapsed; ok=ok, err=err)
    exit(0)
end

main()
