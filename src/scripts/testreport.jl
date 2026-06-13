using Test

mutable struct ReportingTestSet <: Test.AbstractTestSet
    description::String
    results::Vector{Any}
    n_passed::Int
end

ReportingTestSet(desc::AbstractString; kwargs...) = ReportingTestSet(String(desc), Any[], 0)

function Test.record(ts::ReportingTestSet, res::Test.Result)
    if res isa Test.Pass
        ts.n_passed += 1
    else
        push!(ts.results, res)
    end
    res
end

function Test.record(ts::ReportingTestSet, child::Test.AbstractTestSet)
    push!(ts.results, child)
    child
end

Test.record(ts::ReportingTestSet, res) = (push!(ts.results, res); res)

function Test.finish(ts::ReportingTestSet)
    if Test.get_testset_depth() != 0
        Test.record(Test.get_testset(), ts)
    end
    ts
end

# direct passes / results for any testset node
_node_passed(ts::ReportingTestSet) = ts.n_passed
_node_passed(ts::Test.DefaultTestSet) = ts.n_passed
_node_passed(ts) = 0

_node_desc(ts::ReportingTestSet) = ts.description
_node_desc(ts::Test.DefaultTestSet) = ts.description
_node_desc(ts) = ""

_node_results(ts::ReportingTestSet) = ts.results
_node_results(ts::Test.DefaultTestSet) = ts.results
_node_results(ts) = Any[]

function _node_stats(ts)
    pass = _node_passed(ts)
    fail = 0; err = 0; broken = 0
    for r in _node_results(ts)
        r isa Test.AbstractTestSet && continue
        if     r isa Test.Fail;   fail += 1
        elseif r isa Test.Error;  err += 1
        elseif r isa Test.Broken; broken += 1
        elseif r isa Test.Pass;   pass += 1
        end
    end
    (pass, fail, err, broken)
end

# join descriptions with "/", omitting the empty root
function _join_path(prefix, desc)
    isempty(desc) && return prefix
    isempty(prefix) ? desc : string(prefix, "/", desc)
end

function _collect_nodes!(out, ts, prefix)
    desc = _node_desc(ts)
    path = _join_path(prefix, desc)
    pass, fail, err, broken = _node_stats(ts)
    push!(out, (; path, pass, fail, error=err, broken,
                  status = (fail == 0 && err == 0) ? "pass" : "fail"))
    for r in _node_results(ts)
        r isa Test.AbstractTestSet && _collect_nodes!(out, r, path)
    end
    out
end

report_nodes(ts) = _collect_nodes!(Any[], ts, "")

function _jstr(s::AbstractString)
    io = IOBuffer(); print(io, '"')
    for c in s
        if     c == '"';  print(io, "\\\"")
        elseif c == '\\'; print(io, "\\\\")
        elseif c == '\n'; print(io, "\\n")
        elseif c == '\r'; print(io, "\\r")
        elseif c == '\t'; print(io, "\\t")
        elseif c < ' ';   print(io, "\\u", lpad(string(UInt32(c), base = 16), 4, '0'))
        else              print(io, c)
        end
    end
    print(io, '"'); String(take!(io))
end

_jnum(x::AbstractFloat) = isfinite(x) ? string(x) : "0"
_jnum(x::Integer) = string(x)
_jbool(x::Bool) = x ? "true" : "false"

function _node_json(n)
    string("{",
        "\"path\":", _jstr(n.path), ",",
        "\"pass\":", _jnum(n.pass), ",",
        "\"fail\":", _jnum(n.fail), ",",
        "\"error\":", _jnum(n.error), ",",
        "\"broken\":", _jnum(n.broken), ",",
        "\"status\":", _jstr(n.status),
    "}")
end

function write_report(path::String, ts, elapsed_s::Float64;
                      ok::Bool=true, err::Union{Nothing,String}=nothing)
    nodes = ts === nothing ? Any[] : report_nodes(ts)
    tp = 0; tf = 0; te = 0; tb = 0
    for n in nodes
        tp += n.pass; tf += n.fail; te += n.error; tb += n.broken
    end
    io = IOBuffer()
    print(io, "{",
        "\"ok\":", _jbool(ok), ",",
        "\"elapsed_s\":", _jnum(elapsed_s), ",",
        "\"error\":", err === nothing ? "null" : _jstr(err), ",",
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
    path
end
