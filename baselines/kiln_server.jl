using Revise
using Sockets

include(joinpath(@__DIR__, "..", "harness", "testreport.jl"))

function strip_msg(s::AbstractString, n::Int=120)
    s = replace(s, '\t' => ' ', '\n' => ' ', '\r' => ' ')
    length(s) > n ? s[1:n] : s
end

struct ReviseError <: Exception
    msg::String
end

const KLOG = Ref{String}("")
function klog(parts...)
    isempty(KLOG[]) && return
    open(KLOG[], "a") do io
        println(io, "[kiln] ", parts...)
    end
end

const SELECTED = Ref{Union{Nothing,Set{String}}}(nothing)
const TRACKING = Ref{Bool}(false)
const SEEN_PATHS = Set{String}()
const FILE_PATHS = Dict{String,Set{String}}()

function _collect_paths!(set, ts, prefix)
    path = _join_path(prefix, _node_desc(ts))
    isempty(path) || push!(set, path)
    for r in _node_results(ts)
        r isa Test.AbstractTestSet && _collect_paths!(set, r, path)
    end
    set
end

function _stack_testsets()
    get(task_local_storage(), :__BASETESTNEXT__, Test.AbstractTestSet[])
end

function _live_paths()
    stack = _stack_testsets()
    set = Set{String}()
    prefix = ""
    for ts in stack
        _collect_paths!(set, ts, prefix)
        prefix = _join_path(prefix, _node_desc(ts))
    end
    set
end

const INCLUDE_DIR = Ref{String}("")

function _tracked_include(mod::Module, arg)
    p = String(arg)
    # relative include args resolve against the including file's directory,
    # not the process CWD (matches Base.include semantics)
    file = isabspath(p) ? normpath(p) : normpath(joinpath(INCLUDE_DIR[], p))
    sel = SELECTED[]
    (sel !== nothing && !(file in sel)) && return nothing
    old = INCLUDE_DIR[]
    INCLUDE_DIR[] = dirname(file)
    res = try
        Base.include(mod, file)
    finally
        INCLUDE_DIR[] = old
    end
    if TRACKING[]
        cur = _live_paths()
        newp = setdiff(cur, SEEN_PATHS)
        union!(SEEN_PATHS, cur)
        isempty(newp) || (FILE_PATHS[file] = newp)
    end
    res
end

function _rewrite(ex, mod_expr)
    if ex isa Expr
        if ex.head === :call && length(ex.args) >= 2 && ex.args[1] === :include
            return Expr(:call, :_tracked_include, mod_expr, _rewrite(ex.args[end], mod_expr))
        end
        return Expr(ex.head, Any[_rewrite(a, mod_expr) for a in ex.args]...)
    end
    ex
end

mutable struct Suite
    repo_dir::String
    runtests::String
    monolithic::Bool
    body::Any
end

function build_suite(repo_dir::String)
    runtests = joinpath(repo_dir, "test", "runtests.jl")
    top = Meta.parseall(read(runtests, String); filename=runtests)
    has_include = Ref(false)
    scan(ex) = ex isa Expr && begin
        (ex.head === :call && length(ex.args) >= 2 && ex.args[1] === :include) && (has_include[] = true)
        foreach(scan, ex.args)
    end
    scan(top)
    Suite(repo_dir, runtests, !has_include[], _rewrite(top, :Main))
end

function run_full_plain(suite::Suite)
    SELECTED[] = nothing
    TRACKING[] = false
    @testset ReportingTestSet "" begin
        include(suite.runtests)
    end
end

function run_tracked(suite::Suite, selected::Union{Nothing,Set{String}})
    SELECTED[] = selected
    TRACKING[] = true
    INCLUDE_DIR[] = dirname(suite.runtests)
    empty!(SEEN_PATHS)
    root = @testset ReportingTestSet "" begin
        Core.eval(Main, suite.body)
    end
    TRACKING[] = false
    SELECTED[] = nothing
    root
end

all_known_files() = Set(keys(FILE_PATHS))

function unescape_json(s)
    replace(s, "\\\"" => "\"", "\\\\" => "\\", "\\n" => "\n", "\\r" => "\r", "\\t" => "\t")
end

function load_target(instance_json::String)
    set = Set{String}()
    isfile(instance_json) || return set
    m = match(r"\"FAIL_TO_PASS\"\s*:\s*\[(.*?)\]"s, read(instance_json, String))
    m === nothing && return set
    for s in eachmatch(r"\"((?:[^\"\\]|\\.)*)\"", m.captures[1])
        push!(set, unescape_json(s.captures[1]))
    end
    set
end

function failing_paths(report_path::String)
    out = Set{String}()
    isfile(report_path) || return out
    txt = read(report_path, String)
    for m in eachmatch(r"\{\"path\":\"((?:[^\"\\]|\\.)*)\",.*?\"status\":\"(\w+)\"\}", txt)
        m.captures[2] == "fail" && push!(out, unescape_json(m.captures[1]))
    end
    out
end

function path_hits(p::String, target::Set{String})
    p in target && return true
    for t in target
        (startswith(p, t * "/") || startswith(t, p * "/")) && return true
    end
    false
end

const PREV_REPORT = Ref{String}("")
const FIRST_DONE = Ref{Bool}(false)
const TARGET = Ref{Set{String}}(Set{String}())
const MODE = Ref{String}("kiln")

function run_kiln(suite::Suite)
    if !FIRST_DONE[]
        empty!(FILE_PATHS)
        root = run_tracked(suite, nothing)
        FIRST_DONE[] = true
        if isempty(FILE_PATHS)
            suite.monolithic = true
            klog("tracking yielded no file map; degrading to daemon (kind=full)")
        end
        return root, "full", false
    end

    prevfail = isempty(PREV_REPORT[]) ? Set{String}() : failing_paths(PREV_REPORT[])
    target = union(TARGET[], prevfail)

    selected = Set{String}()
    for (f, paths) in FILE_PATHS
        any(p -> path_hits(p, target), paths) && push!(selected, f)
    end
    isempty(selected) && (selected = all_known_files())

    ts0 = time()
    root = run_tracked(suite, selected)
    klog("selective: ", length(selected), "/", length(FILE_PATHS),
         " files, ", round(Int, (time()-ts0)*1000), "ms")
    if any(n -> n.status == "fail", report_nodes(root))
        return root, "selective", true
    end

    tc0 = time()
    root2 = run_tracked(suite, all_known_files())
    klog("confirm: ", round(Int, (time()-tc0)*1000), "ms")
    root2, "confirmed", false
end

function write_report_local(path::String, ts, elapsed_s::Float64;
                            ok::Bool=true, err::Union{Nothing,String}=nothing,
                            partial::Bool=false)
    nodes = ts === nothing ? Any[] : report_nodes(ts)
    tp = 0; tf = 0; te = 0; tb = 0
    for n in nodes
        tp += n.pass; tf += n.fail; te += n.error; tb += n.broken
    end
    io = IOBuffer()
    print(io, "{",
        "\"ok\":", _jbool(ok), ",",
        "\"elapsed_s\":", _jnum(elapsed_s), ",",
        "\"error\":", err === nothing ? "null" : _jstr(err), ",")
    partial && print(io, "\"partial\":true,")
    print(io,
        "\"total\":{",
            "\"pass\":", _jnum(tp), ",",
            "\"fail\":", _jnum(tf), ",",
            "\"error\":", _jnum(te), ",",
            "\"broken\":", _jnum(tb),
        "},",
        "\"tests\":[")
    for (i, n) in enumerate(nodes)
        i > 1 && print(io, ",")
        print(io, _node_json(n))
    end
    print(io, "]}")
    open(path, "w") do f
        write(f, String(take!(io)))
    end
    PREV_REPORT[] = path
    path
end

function run_suite_capture(conn, suite::Suite, out_json::String)
    t0 = time()
    ok = true
    err = nothing
    kind = "full"
    partial = false
    ts = nothing
    reviseerr = Ref{Union{Nothing,String}}(nothing)

    redirect_stdout(stderr) do
        try
            Revise.revise(; throw=true)
        catch e
            reviseerr[] = strip_msg(sprint(showerror, e))
            return
        end
        try
            if MODE[] == "daemon" || suite.monolithic
                ts = run_full_plain(suite)
                kind = "full"
            else
                ts, kind, partial = run_kiln(suite)
            end
        catch e
            ok = false
            msg = sprint(showerror, e)
            err = length(msg) > 4000 ? msg[1:4000] : msg
            ts = nothing
        end
    end

    reviseerr[] !== nothing && throw(ReviseError(reviseerr[]))

    elapsed = time() - t0
    write_report_local(out_json, ts, elapsed; ok=ok, err=err, partial=partial)
    write(conn, string("DONE\t", round(Int, elapsed * 1000), "\t", kind, "\n"))
    flush(conn)
end

function handle_run(conn, suite::Suite, out_json::String, socket_path::String)
    try
        run_suite_capture(conn, suite, out_json)
    catch e
        msg = e isa ReviseError ? e.msg : strip_msg(sprint(showerror, e))
        write(conn, string("RESTART\t", strip_msg(msg), "\n")); flush(conn)
        try; close(conn); catch; end
        rm(socket_path; force=true)
        exit(3)
    end
end

function serve(socket_path::String, repo_dir::String, suite::Suite)
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
                    handle_run(conn, suite, out_json, socket_path)
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
    length(ARGS) >= 4 || (println(stderr, "usage: kiln_server.jl <repo_dir> <instance_json> <daemon|kiln> <socket_path>"); exit(2))
    repo_dir = abspath(ARGS[1])
    instance_json = abspath(ARGS[2])
    mode = ARGS[3]
    socket_path = abspath(ARGS[4])
    empty!(ARGS)

    MODE[] = mode
    TARGET[] = load_target(instance_json)
    KLOG[] = get(ENV, "KILN_LOG", "")
    cd(repo_dir)

    suite = build_suite(repo_dir)
    if mode == "kiln" && suite.monolithic
        klog("monolithic suite (no resolvable includes); degrading to daemon (kind=full)")
    end

    serve(socket_path, repo_dir, suite)
end

main()
