# report.jl -- main-process report reading + diff/resolve logic.
#
# Ports the pure-Python core of swebench_eval.py: diff_reports / check_resolved,
# plus read_report. These run in the orchestrator process (they consume the JSON
# emitted by the run_tests.jl subprocess); they do NOT run inside the test env.

"""
    read_report(path) -> Dict{String,Any}

Read a run_tests.jl report JSON file into a plain Julia dict. Mirrors Python's
`json.load`. The "" path key inside `tests[]` is preserved.
"""
read_report(path::AbstractString) = _json_to_dict(JSON3.read(read(path, String)))

# Index a report's tests by path -> status.
#
# CRITICAL (B1): this MUST reproduce Python's
#     {t["path"]: t["status"] for t in report.get("tests", [])}
# A Python dict comprehension over a list assigns keys in order, so on a duplicate
# path the LATER occurrence overwrites the earlier one (LAST-WINS), while the key's
# ITERATION POSITION stays at its FIRST insertion. Julia's Dict is not insertion-
# ordered, so we return `(keys, idx)`: `keys` is the insertion-ordered, dedup'd path
# list (first-seen position) and `idx` maps path -> status with last-wins. Together
# they reproduce a Python dict exactly for both iteration and lookup. Duplicate
# paths are pervasive in real reports, so this is load-bearing.
#
# CRITICAL (B2): the empty-string "" path is treated as an ordinary key.
function _index_report(report)
    idx = Dict{String,Any}()
    keys = String[]
    tests = get(report, "tests", Any[])
    for t in tests
        p = String(t["path"])
        haskey(idx, p) || push!(keys, p)
        idx[p] = t["status"]
    end
    return keys, idx
end

"""
    diff_reports(pre, post) -> (f2p, p2p, new_fail)

Return `(F2P, P2P, NEW_FAIL)` keyed by test path, mirroring Python `diff_reports`.

- F2P     : fail-in-pre AND pass-in-post
- P2P     : pass-in-pre AND pass-in-post
- NEW_FAIL: pass-in-pre AND (fail-or-missing in post)

Iteration follows `pre_idx` insertion order (last-wins per `_index_report`), so the
returned lists match the Python lists element-for-element, including ordering.
"""
function diff_reports(pre, post)
    pre_keys, pre_idx = _index_report(pre)
    _, post_idx = _index_report(post)

    f2p = String[]
    p2p = String[]
    new_fail = String[]
    for path in pre_keys
        pre_status = pre_idx[path]
        post_status = get(post_idx, path, nothing)
        if pre_status == "fail"
            if post_status == "pass"
                push!(f2p, path)
            end
        elseif pre_status == "pass"
            if post_status == "pass"
                push!(p2p, path)
            else
                push!(new_fail, path)
            end
        end
    end
    return f2p, p2p, new_fail
end

"""
    check_resolved(report, f2p, p2p) -> Bool

Return `true` iff every F2P path passes and every P2P path passes, mirroring
Python `check_resolved`. The "" path (B2) is looked up like any other key.
"""
function check_resolved(report, f2p, p2p)
    _, idx = _index_report(report)
    for path in f2p
        get(idx, String(path), nothing) == "pass" || return false
    end
    for path in p2p
        get(idx, String(path), nothing) == "pass" || return false
    end
    return true
end
