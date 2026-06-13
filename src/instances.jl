# instances.jl -- JSONL instance load/dump.
#
# Mirrors the Python harness' line-oriented JSON handling. Each line is one
# instance object. The empty-string "" element inside FAIL_TO_PASS / PASS_TO_PASS
# arrays (and the "" path key in reports) is data and MUST be preserved verbatim
# on round-trip.

"""
    load_instances(path) -> Vector{Dict{String,Any}}

Read a JSONL file into a vector of instance dicts (one per non-blank line),
preserving insertion order. Mirrors the Python `for line in fh: json.loads(line)`
loop, including stripping blank lines.
"""
function load_instances(path::AbstractString)
    out = Vector{Dict{String,Any}}()
    for line in eachline(path)
        s = strip(line)
        isempty(s) && continue
        push!(out, _json_to_dict(JSON3.read(s)))
    end
    return out
end

"""
    load_instances_by_id(path) -> Dict{String,Dict{String,Any}}

Read a JSONL file keyed by `instance_id`, last-wins on duplicate ids. Mirrors
the Python `_load_jsonl_by_id`.
"""
load_instances_by_id(path::AbstractString) = last(load_instances_by_id_ordered(path))

"""
    load_instances_by_id_ordered(path) -> (ids::Vector{String}, Dict{String,Dict{String,Any}})

Like [`load_instances_by_id`](@ref) but also returns the ids in FILE INSERTION ORDER
(first-seen position, last-wins value). Python's dict is insertion-ordered, so
`cmd_eval`'s `list(instances.keys())` iterates ids in file order; reproducing that
order keeps the eval report's `instances` array element-for-element identical.
"""
function load_instances_by_id_ordered(path::AbstractString)
    result = Dict{String,Dict{String,Any}}()
    ids = String[]
    for line in eachline(path)
        s = strip(line)
        isempty(s) && continue
        obj = _json_to_dict(JSON3.read(s))
        iid = String(obj["instance_id"])
        haskey(result, iid) || push!(ids, iid)
        result[iid] = obj
    end
    return ids, result
end

"""
    dump_instances(path, instances)

Write a vector of instance dicts as JSONL (one compact JSON object per line),
mirroring the Python `out_fh.write(json.dumps(inst) + "\\n")`.
"""
function dump_instances(path::AbstractString, instances)
    open(path, "w") do fh
        for inst in instances
            write(fh, JSON3.write(inst))
            write(fh, "\n")
        end
    end
    return path
end

# Recursively convert a parsed JSON3 value into plain Julia containers
# (Dict{String,Any} / Vector{Any} / scalars), so downstream code is index- and
# mutate-friendly exactly like the Python dicts. The "" string element and
# `nothing` (JSON null) survive unchanged.
_json_to_dict(x::JSON3.Object) = Dict{String,Any}(String(k) => _json_to_dict(v) for (k, v) in pairs(x))
_json_to_dict(x::JSON3.Array) = Any[_json_to_dict(v) for v in x]
_json_to_dict(x::AbstractString) = String(x)
_json_to_dict(x) = x
