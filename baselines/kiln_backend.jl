# kiln_backend.jl -- a warm kiln-rt backend for the standalone SWEBenchJl package.
#
# This demonstrates the decoupled architecture: the kiln repo CONSUMES the
# committed SWEBenchJl package purely through its `AbstractBackend` interface.
# SWEBenchJl drives the env build + verdict logic; this backend supplies a warm
# execution strategy that reuses compilation across checks via the kiln runtime
# (src/check_session.jl, behind baselines/kiln_rt_server.jl).
#
# Load this AFTER `using SWEBenchJl` so the abstract type + `build_env`/`read_report`
# are in scope:
#     using SWEBenchJl
#     include("baselines/kiln_backend.jl")
#     r = SWEBenchJl.run_one(...; backend=KilnRtBackend(julia; timeout=600), ...)
#
# The socket protocol mirrors harness/episodes.py (_start_server / _sock_send_run /
# ensure_server): the server prints READY, then on `RUN\t<out_json>\n` it runs the
# check, writes the report JSON, and replies `DONE\t<ms>\t<kind>\n` (or `RESTART\t<msg>`
# on a Revise failure). `QUIT\n` -> `BYE`. The FIRST check is a full run, so the report
# is complete and resolve works after a single `run_check` (as run_one does).

using Sockets
import SHA
import JSON3

const _BASELINES_DIR = @__DIR__
const _KILN_RT_SERVER = joinpath(_BASELINES_DIR, "kiln_rt_server.jl")

# Per-worktree server handle: the live subprocess, its socket path, and the
# instance JSON we wrote for it. Keyed by the worktree path inside the backend so a
# single backend instance can drive prepare! -> run_check -> teardown.
mutable struct _ServerHandle
    proc::Base.Process
    sock::String
    instance_json::String
    server_log::String
end

"""
    KilnRtBackend(julia; timeout=900)

A warm `SWEBenchJl.AbstractBackend` that reuses compilation across checks via the
kiln runtime. `prepare!` builds the per-instance env and starts the `kiln_rt_server.jl`
resident server; `run_check` sends one `RUN` over the UNIX socket and parses the
report the server writes. The server stays warm for reuse and is torn down by `stop!`.
"""
mutable struct KilnRtBackend <: SWEBenchJl.AbstractBackend
    julia::String
    timeout::Int
    servers::Dict{String,_ServerHandle}
end
KilnRtBackend(julia::AbstractString; timeout::Integer=900) =
    KilnRtBackend(String(julia), Int(timeout), Dict{String,_ServerHandle}())

# UNIX socket paths are capped at ~108 bytes; deep worktrees exceed it, so sockets
# live in /tmp keyed by a digest of the worktree path (mirrors episodes.py's
# _sock_path_for, which keys on the .kilnswe dir).
function _sock_path_for(key::AbstractString)
    h = bytes2hex(SHA.sha1(abspath(String(key))))[1:12]
    joinpath("/tmp", "kilnrt-$(h).sock")
end

# Connect, run one line of request/response, return the server's reply line.
function _sock_request(sock::AbstractString, msg::AbstractString; timeout::Real=1200.0)
    conn = connect(sock)
    try
        write(conn, msg)
        flush(conn)
        return readline(conn; keep=false)
    finally
        try; close(conn); catch; end
    end
end

# Liveness probe (mirrors episodes.py _sock_ping): PING -> PONG. A UNIX domain socket
# is a path but NOT a regular file, so probe with `ispath`, not `isfile`.
function _sock_ping(sock::AbstractString)
    ispath(sock) || return false
    try
        return _sock_request(sock, "PING\n"; timeout=2.0) == "PONG"
    catch
        return false
    end
end

# Kill the process whose pid is recorded in <sock>.pid (mirrors _kill_pidfile).
function _kill_pidfile(sock::AbstractString)
    pidf = sock * ".pid"
    ispath(pidf) || return
    try
        pid = parse(Int, strip(read(pidf, String)))
        run(ignorestatus(`kill -9 $(pid)`))
    catch
    end
end

# Start the kiln_rt_server.jl subprocess against `wt`, blocking until it prints READY.
# Mirrors episodes.py _start_server for the kiln-rt mode: same argv shape
# (julia --project=<env> kiln_rt_server.jl <repo_dir> <instance_json> kiln-rt <sock>),
# stderr to a log file, stdout read line-by-line for READY. Uses the REAL julia binary.
function _start_server(b::KilnRtBackend, wd::SWEBenchJl.WorkDir,
                       sock::AbstractString, instance_json::AbstractString,
                       server_log::AbstractString)
    isfile(_KILN_RT_SERVER) ||
        error("kiln-rt server script not found: $(_KILN_RT_SERVER)")
    src_check = normpath(joinpath(_BASELINES_DIR, "..", "..", "..", "src", "check_session.jl"))
    isfile(src_check) ||
        error("mode kiln-rt requires the kiln runtime at $(src_check) (src/ absent)")

    ispath(sock) && rm(sock; force=true)
    # The kiln runtime writes its check telemetry to KILN_RT_TELEMETRY (defaulting to
    # <repo>/.kilnswe/check_telemetry.jsonl, an episodes.py convention whose parent dir
    # does NOT exist in a SWEBenchJl worktree). Point it at wd.log, which exists.
    telemetry = joinpath(abspath(wd.log), "check_telemetry.jsonl")
    child_env = copy(ENV)
    child_env["KILN_RT_TELEMETRY"] = telemetry
    cmd = setenv(Cmd([b.julia, "--startup-file=no", "--project=$(wd.env)",
                      _KILN_RT_SERVER, abspath(wd.wt), abspath(instance_json),
                      "kiln-rt", abspath(sock)]),
                 child_env; dir=abspath(wd.wt))

    logio = open(server_log, "a")
    out = Pipe()
    proc = run(pipeline(cmd; stdout=out, stderr=logio); wait=false)
    close(out.in)

    deadline = time() + 300.0
    ready = false
    while time() < deadline
        if eof(out)
            break
        end
        line = readline(out; keep=false)
        if occursin("READY", line)
            ready = true
            break
        end
    end
    if !ready
        try; kill(proc); catch; end
        try; close(logio); catch; end
        error("kiln-rt server did not print READY within 300s (see $(server_log))")
    end
    return _ServerHandle(proc, abspath(sock), abspath(instance_json), server_log)
end

# Ensure a live server exists for this worktree; (re)start if absent/dead.
function _ensure_server(b::KilnRtBackend, wd::SWEBenchJl.WorkDir)
    h = get(b.servers, wd.wt, nothing)
    if h !== nothing && process_running(h.proc) && _sock_ping(h.sock)
        return h
    end
    # stale entry: clean up before restarting
    if h !== nothing
        _kill_pidfile(h.sock)
        try; ispath(h.sock) && rm(h.sock; force=true); catch; end
    end
    sock = _sock_path_for(wd.wt)
    server_log = joinpath(wd.log, "kiln_rt_server.log")
    instance_json = joinpath(wd.log, "instance.json")
    h = _start_server(b, wd, sock, instance_json, server_log)
    b.servers[wd.wt] = h
    return h
end

"""
    prepare!(b::KilnRtBackend, instance, wd) -> Bool

Build the per-instance env via `SWEBenchJl.build_env`, add Revise to it (the kiln-rt
server loads `using .CheckSession` which needs Revise), persist the instance JSON, and
start the resident kiln_rt_server.jl. Returns `true` on success.
"""
function SWEBenchJl.prepare!(b::KilnRtBackend, instance, wd::SWEBenchJl.WorkDir)
    mkpath(wd.env)
    mkpath(wd.log)

    SWEBenchJl.build_env(b.julia, wd.wt, wd.env, b.timeout, wd.log) || return false

    # The kiln-rt server includes src/check_session.jl, which does `using Revise`.
    # build_env (testenv.jl) does not add Revise, so add it to the env now, mirroring
    # episodes.py's non-cold prepare step.
    revise_log = joinpath(wd.log, "revise_add.log")
    rc, _, _ = SWEBenchJl.run_cmd(
        [b.julia, "--startup-file=no", "-e",
         "using Pkg; Pkg.activate(\"$(abspath(wd.env))\"); " *
         "in(\"Revise\", keys(Pkg.project().dependencies)) || Pkg.add(\"Revise\"); Pkg.precompile()"];
        timeout=b.timeout, log_path=revise_log)
    rc == 0 || return false

    # Persist the instance record where the server expects a positional <instance_json>.
    instance_json = joinpath(wd.log, "instance.json")
    open(instance_json, "w") do fh
        JSON3.write(fh, instance)
    end

    try
        _ensure_server(b, wd)
    catch e
        @warn "kiln-rt server start failed" exception=(e, catch_backtrace())
        return false
    end
    return true
end

"""
    run_check(b::KilnRtBackend, wd) -> (report_or_nothing, check_ms)

Send one `RUN\\t<out_json>` to the resident server, wait for `DONE`, and parse the
report JSON the server wrote via `SWEBenchJl.read_report`. On `RESTART` (a Revise
failure server-side) the server is restarted once and the RUN retried, mirroring
episodes.py. Returns the parsed report (or `nothing`) and wall-clock milliseconds.
"""
function SWEBenchJl.run_check(b::KilnRtBackend, wd::SWEBenchJl.WorkDir)
    out_json = abspath(joinpath(wd.log, "report_$(wd.label).json"))

    h = _ensure_server(b, wd)

    t0 = time()
    reply = _sock_request(h.sock, "RUN\t$(out_json)\n"; timeout=b.timeout * 1.0)

    if startswith(reply, "RESTART\t")
        _kill_pidfile(h.sock)
        ispath(h.sock) && rm(h.sock; force=true)
        delete!(b.servers, wd.wt)
        h = _ensure_server(b, wd)
        reply = _sock_request(h.sock, "RUN\t$(out_json)\n"; timeout=b.timeout * 1.0)
    end

    check_ms = (time() - t0) * 1000.0

    startswith(reply, "DONE\t") ||
        @warn "kiln-rt RUN did not return DONE" reply

    isfile(out_json) || return nothing, check_ms
    try
        return SWEBenchJl.read_report(out_json), check_ms
    catch
        return nothing, check_ms
    end
end

"""
    stop!(b::KilnRtBackend)

Send QUIT to every resident server and reap the subprocess (SIGKILL the pid-file
process as a fallback). Safe to call multiple times.
"""
function stop!(b::KilnRtBackend)
    for (wt, h) in collect(b.servers)
        # QUIT reads the pidfile/socket paths the server cleans up on exit, so snapshot
        # the pid first; a Julia server exiting from inside a connection handler while
        # another task blocks on accept() can linger, so SIGKILL the proc handle too.
        pid = nothing
        try
            pid = parse(Int, strip(read(h.sock * ".pid", String)))
        catch
        end
        try
            _sock_request(h.sock, "QUIT\n"; timeout=5.0)
        catch
        end
        try; kill(h.proc, Base.SIGKILL); catch; end
        if pid !== nothing
            try; run(ignorestatus(`kill -9 $(pid)`)); catch; end
        end
        _kill_pidfile(h.sock)
        try; ispath(h.sock) && rm(h.sock; force=true); catch; end
        try; ispath(h.sock * ".pid") && rm(h.sock * ".pid"; force=true); catch; end
        delete!(b.servers, wt)
    end
    return nothing
end
