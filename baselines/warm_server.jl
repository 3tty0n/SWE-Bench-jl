using Revise

include(joinpath(@__DIR__, "..", "harness", "testreport.jl"))

function strip_msg(s::AbstractString)
    s = replace(s, '\t' => ' ', '\n' => ' ', '\r' => ' ')
    length(s) > 200 ? s[1:200] : s
end

function run_check(repo_dir::String, out_json::String)
    runtests = joinpath(repo_dir, "test", "runtests.jl")
    ts = nothing
    ok = true
    err = nothing
    t0 = time()
    real_stdout = stdout
    redirect_stdout(stderr) do
        try
            Revise.revise()
        catch
        end
        try
            ts = @testset ReportingTestSet "" begin
                include(runtests)
            end
        catch e
            ok = false
            msg = sprint(showerror, e)
            err = length(msg) > 4000 ? msg[1:4000] : msg
            ts = nothing
        end
    end
    elapsed = time() - t0
    try
        write_report(out_json, ts, elapsed; ok=ok, err=err)
        println(real_stdout, "DONE\t", round(Int, elapsed * 1000))
    catch e
        println(real_stdout, "ERR\t", strip_msg(sprint(showerror, e)))
    end
    flush(real_stdout)
end

function main()
    length(ARGS) >= 1 || (println(stderr, "usage: warm_server.jl <repo_dir>"); exit(2))
    repo_dir = abspath(ARGS[1])
    empty!(ARGS)
    cd(repo_dir)

    println("READY")
    flush(stdout)

    while true
        line = readline(stdin; keep=false)
        if isempty(line) && eof(stdin)
            break
        end
        if line == "PING"
            println("PONG"); flush(stdout)
        elseif line == "QUIT"
            break
        elseif startswith(line, "RUN\t")
            out_json = abspath(String(split(line, '\t')[2]))
            run_check(repo_dir, out_json)
        elseif isempty(line)
            continue
        end
    end
    exit(0)
end

main()
