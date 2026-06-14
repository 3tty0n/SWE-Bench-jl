using Sockets

# Path to the kiln runtime's check_session.jl. Default assumes this repo is mounted as a
# submodule at <kiln-repo>/benchmark/swebench, so ../../../src is the kiln repo's src/.
# Override with KILN_CHECK_SESSION when the kiln runtime lives elsewhere.
const SRC_CHECK = get(ENV, "KILN_CHECK_SESSION",
                      normpath(joinpath(@__DIR__, "..", "..", "..", "src", "check_session.jl")))
isfile(SRC_CHECK) || (println(stderr, "kiln-rt requires src/check_session.jl (not found at $SRC_CHECK)"); exit(2))
include(SRC_CHECK)
using .CheckSession

function strip_msg(s::AbstractString, n::Int=120)
    s = replace(s, '\t' => ' ', '\n' => ' ', '\r' => ' ')
    length(s) > n ? s[1:n] : s
end

struct ReviseError <: Exception
    msg::String
end

function _jstr(s::AbstractString)
    io = IOBuffer(); print(io, '"')
    for c in s
        if     c == '"';  print(io, "\\\"")
        elseif c == '\\'; print(io, "\\\\")
        elseif c == '\n'; print(io, "\\n")
        elseif c == '\r'; print(io, "\\r")
        elseif c == '\t'; print(io, "\\t")
        elseif c < ' ';   print(io, "\\u", lpad(string(UInt32(c), base=16), 4, '0'))
        else              print(io, c)
        end
    end
    print(io, '"'); String(take!(io))
end

function write_report(path::String, nodes, elapsed_s::Float64;
                      ok::Bool=true, err::Union{Nothing,String}=nothing)
    tp = 0; tf = 0; te = 0; tb = 0
    for n in nodes
        tp += n.pass; tf += n.fail; te += n.error; tb += n.broken
    end
    io = IOBuffer()
    print(io, "{",
        "\"ok\":", ok ? "true" : "false", ",",
        "\"elapsed_s\":", elapsed_s, ",",
        "\"error\":", err === nothing ? "null" : _jstr(err), ",",
        "\"total\":{",
            "\"pass\":", tp, ",\"fail\":", tf, ",\"error\":", te, ",\"broken\":", tb,
        "},",
        "\"tests\":[")
    for (i, n) in enumerate(nodes)
        i > 1 && print(io, ",")
        print(io, "{\"path\":", _jstr(n.path),
                  ",\"pass\":", n.pass, ",\"fail\":", n.fail,
                  ",\"error\":", n.error, ",\"broken\":", n.broken,
                  ",\"status\":", _jstr(n.status), "}")
    end
    print(io, "]}")
    open(path, "w") do f; write(f, String(take!(io))); end
    path
end

const CTX = Ref{Any}(nothing)
const REPO = Ref{String}("")
const PROJECT = Ref{String}("")
const FIRST_DONE = Ref{Bool}(false)

function run_one(repo_dir::String)
    if !FIRST_DONE[]
        r = CheckSession.first_check!(CTX[])
        FIRST_DONE[] = true
        # build the real per-unit src footprint via coverage so selection engages
        try
            CheckSession.build_coverage_footprint!(CTX[]; julia=joinpath(Sys.BINDIR, "julia"),
                                                   project=PROJECT[])
        catch e
            # footprint stays at the in-process approximation; selection degrades
            # to conservative (all units re-run) but verdicts remain correct
            println(stderr, "footprint build failed: ", strip_msg(sprint(showerror, e)))
        end
        return r
    end
    CheckSession.check!(CTX[])
end

function run_suite_capture(conn, repo_dir::String, out_json::String)
    t0 = time()
    ok = true; err = nothing; kind = "full"
    nodes = Any[]
    reviseerr = Ref{Union{Nothing,String}}(nothing)

    redirect_stdout(stderr) do
        try
            CheckSession.Revise.revise(; throw=true)
        catch e
            reviseerr[] = strip_msg(sprint(showerror, e)); return
        end
        try
            r = run_one(repo_dir)
            kind = r.kind
            nodes = CheckSession.report_nodes(r, repo_dir)
        catch e
            ok = false
            msg = sprint(showerror, e)
            err = length(msg) > 4000 ? msg[1:4000] : msg
        end
    end

    reviseerr[] !== nothing && throw(ReviseError(reviseerr[]))

    elapsed = time() - t0
    write_report(out_json, nodes, elapsed; ok=ok, err=err)
    write(conn, string("DONE\t", round(Int, elapsed * 1000), "\t", kind, "\n"))
    flush(conn)
end

function handle_run(conn, repo_dir::String, out_json::String, socket_path::String)
    try
        run_suite_capture(conn, repo_dir, out_json)
    catch e
        msg = e isa ReviseError ? e.msg : strip_msg(sprint(showerror, e))
        write(conn, string("RESTART\t", strip_msg(msg), "\n")); flush(conn)
        try; close(conn); catch; end
        rm(socket_path; force=true)
        exit(3)
    end
end

function serve(socket_path::String, repo_dir::String)
    isfile(socket_path) && rm(socket_path; force=true)
    server = listen(socket_path)
    write(socket_path * ".pid", string(getpid()))
    println("READY"); flush(stdout)
    while true
        conn = accept(server)
        try
            while !eof(conn)
                line = readline(conn; keep=false)
                isempty(line) && eof(conn) && break
                if line == "PING"
                    write(conn, "PONG\n"); flush(conn)
                elseif line == "QUIT"
                    write(conn, "BYE\n"); flush(conn)
                    close(conn); close(server)
                    rm(socket_path; force=true); rm(socket_path * ".pid"; force=true)
                    exit(0)
                elseif startswith(line, "RUN\t")
                    out_json = abspath(String(split(line, '\t')[2]))
                    handle_run(conn, repo_dir, out_json, socket_path)
                elseif isempty(line)
                    continue
                end
            end
        catch e
            isa(e, EOFError) || println(stderr, "conn error: ", strip_msg(sprint(showerror, e)))
        finally
            try; close(conn); catch; end
        end
    end
end

function main()
    length(ARGS) >= 4 || (println(stderr, "usage: kiln_rt_server.jl <repo_dir> <instance_json> <kiln-rt> <socket_path>"); exit(2))
    repo_dir = abspath(ARGS[1])
    socket_path = abspath(ARGS[4])
    project = Base.active_project()
    empty!(ARGS)

    REPO[] = repo_dir
    PROJECT[] = project === nothing ? repo_dir : dirname(project)
    cd(repo_dir)
    telemetry = get(ENV, "KILN_RT_TELEMETRY", joinpath(repo_dir, ".kilnswe", "check_telemetry.jsonl"))
    CTX[] = CheckSession.CheckCtx(repo_dir; telemetry=telemetry)

    serve(socket_path, repo_dir)
end

main()
