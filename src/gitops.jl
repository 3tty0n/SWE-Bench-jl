# gitops.jl -- repo/worktree management, shell runner, and 3-method patch apply.
#
# Ports the shell + git/worktree + patch portions of swebench_eval.py verbatim in
# behavior. The 3-method patch fallthrough ORDER is load-bearing: it decides which
# patches apply.

# ---------------------------------------------------------------------------
# Shell helper
# ---------------------------------------------------------------------------

# Raised when a subprocess exceeds its timeout; mirrors subprocess.TimeoutExpired.
struct TimeoutError <: Exception
    cmd::Vector{String}
    timeout::Real
end

"""
    run_cmd(cmd; cwd=nothing, timeout=nothing, log_path=nothing, env=nothing)
        -> (returncode, stdout_text, stderr_text)

Run `cmd` (a vector of strings); return `(rc, out, err)` with stdout/stderr
captured to strings and optionally appended to `log_path`. Mirrors the Python
`run()` helper, including the log format and the `subprocess.TimeoutExpired`
behavior: on timeout this throws `TimeoutError` (caller catches it just like
Python catches `TimeoutExpired`).
"""
function run_cmd(cmd::Vector{String}; cwd=nothing, timeout=nothing,
                 log_path=nothing, env=nothing)
    base = Cmd(cmd)
    if env !== nothing
        base = cwd === nothing ? setenv(base, env) : setenv(base, env; dir=String(cwd))
    elseif cwd !== nothing
        base = setenv(base; dir=String(cwd))
    end
    base = ignorestatus(base)

    # Capture to temp FILES, not in-memory IOBuffers. A `wait=false` process whose
    # stdout/stderr target an IOBuffer can stall: the async task that drains the pipe into
    # the buffer is starved by the `process_running` poll loop, so any subprocess with
    # non-trivial output (env build, test run) blocks on a full pipe and appears to hang
    # until the timeout. File redirection lets the OS buffer freely -- no drain task, no stall.
    outf, _oio = mktemp(); close(_oio)
    errf, _eio = mktemp(); close(_eio)
    try
        if timeout === nothing
            proc = run(pipeline(base; stdout=outf, stderr=errf); wait=true)
            rc = proc.exitcode
        else
            proc = run(pipeline(base; stdout=outf, stderr=errf); wait=false)
            deadline = time() + timeout
            while process_running(proc) && time() < deadline
                sleep(0.05)
            end
            if process_running(proc)
                kill(proc)
                # give it a moment, then SIGKILL if needed
                t1 = time() + 2.0
                while process_running(proc) && time() < t1
                    sleep(0.02)
                end
                process_running(proc) && kill(proc, Base.SIGKILL)
                wait(proc)
                throw(TimeoutError(cmd, timeout))
            end
            rc = proc.exitcode
        end

        out = read(outf, String)
        err = read(errf, String)

        if log_path !== nothing
            mkpath(dirname(String(log_path)))
            open(String(log_path), "a") do fh
                write(fh, "=== CMD: ", join(cmd, " "), "\n")
                write(fh, "=== RC: ", string(rc), "\n")
                isempty(out) || write(fh, "--- stdout ---\n", out)
                isempty(err) || write(fh, "--- stderr ---\n", err)
            end
        end
        return rc, out, err
    finally
        rm(outf; force=true)
        rm(errf; force=true)
    end
end

function julia_version(julia::AbstractString)
    try
        rc, out, _ = run_cmd([String(julia), "--version"])
        return rc == 0 ? strip(out) : "unknown"
    catch
        return "unknown"
    end
end

# ---------------------------------------------------------------------------
# Repo / worktree path layout
# ---------------------------------------------------------------------------

repo_dir(work::AbstractString, repo::AbstractString) =
    joinpath(work, "repos", replace(repo, "/" => "__"))

wt_dir(work::AbstractString, instance_id::AbstractString, tag::AbstractString) =
    joinpath(work, "wt", "$(instance_id).$(tag)")

env_dir(work::AbstractString, instance_id::AbstractString) =
    joinpath(work, "envs", instance_id)

function log_dir(work::AbstractString, instance_id::AbstractString)
    d = joinpath(work, "logs", instance_id)
    mkpath(d)
    return d
end

# ---------------------------------------------------------------------------
# Clone / worktree management
# ---------------------------------------------------------------------------

"""
    ensure_clone(repo_url, clone, commits, log)

Clone a `--filter=blob:none` repo if absent; if any commit in `commits` is missing,
fetch all heads/tags, falling back to `--unshallow`. Mirrors Python `ensure_clone`.
Throws on clone failure.
"""
function ensure_clone(repo_url::AbstractString, clone::AbstractString,
                      commits, log::AbstractString)
    if !ispath(clone)
        mkpath(dirname(clone))
        rc, _, err = run_cmd(["git", "clone", "--filter=blob:none",
                              String(repo_url), String(clone)];
                             log_path=joinpath(log, "clone.log"))
        if rc != 0
            error("clone failed: " * first(err, 300))
        end
    end

    missing = String[]
    for sha in commits
        rc, _, _ = run_cmd(["git", "-C", String(clone), "cat-file", "-e",
                            "$(sha)^{commit}"])
        rc != 0 && push!(missing, String(sha))
    end

    if !isempty(missing)
        rc, _, err = run_cmd(["git", "-C", String(clone), "fetch", "--quiet",
                              "--tags", "origin", "+refs/heads/*:refs/heads/*"];
                             log_path=joinpath(log, "fetch.log"))
        if rc != 0
            run_cmd(["git", "-C", String(clone), "fetch", "--unshallow"];
                    log_path=joinpath(log, "fetch_unshallow.log"))
        end
    end
    return nothing
end

function remove_worktree(clone::AbstractString, wt::AbstractString, log::AbstractString)
    if ispath(wt)
        run_cmd(["git", "-C", String(clone), "worktree", "remove", "--force",
                 String(wt)]; log_path=joinpath(log, "wt_remove.log"))
        if ispath(wt)
            rm(wt; force=true, recursive=true)
        end
    end
    return nothing
end

function create_worktree(clone::AbstractString, wt::AbstractString,
                         sha::AbstractString, log::AbstractString)
    mkpath(dirname(wt))
    remove_worktree(clone, wt, log)
    run_cmd(["git", "-C", String(clone), "worktree", "prune"];
            log_path=joinpath(log, "wt_prune.log"))
    rc, _, err = run_cmd(["git", "-C", String(clone), "worktree", "add",
                          "--detach", String(wt), String(sha)];
                         log_path=joinpath(log, "wt_add.log"))
    return rc == 0
end

# ---------------------------------------------------------------------------
# Patch application -- 3 methods, ORDER IS LOAD-BEARING
# ---------------------------------------------------------------------------

"""
    apply_patch(patch_text, wt, log, label) -> Bool

Try three methods in order, returning `true` on the first success:

1. `git apply --whitespace=nowarn`
2. `git apply --3way`
3. `patch -p1 -f --fuzz=3`

Mirrors Python `apply_patch` exactly, including writing `patch_text` to a temp
`.patch` file and removing it afterward. The fallthrough order decides which
patches apply.
"""
function apply_patch(patch_text::AbstractString, wt::AbstractString,
                     log::AbstractString, label::AbstractString)
    patch_file, io = mktemp()
    write(io, patch_text)
    close(io)

    lp = joinpath(log, "$(label).log")
    try
        rc, _, _ = run_cmd(["git", "-C", String(wt), "apply",
                            "--whitespace=nowarn", patch_file]; log_path=lp)
        rc == 0 && return true

        rc, _, _ = run_cmd(["git", "-C", String(wt), "apply", "--3way",
                            patch_file]; log_path=lp)
        rc == 0 && return true

        rc, _, _ = run_cmd(["patch", "-d", String(wt), "-p1", "-f", "--fuzz=3",
                            "-i", patch_file]; log_path=lp)
        return rc == 0
    finally
        rm(patch_file; force=true)
    end
end
