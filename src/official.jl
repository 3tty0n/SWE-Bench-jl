# official.jl -- export canonical SWE-bench JSONL from the Julia instance schema.
#
# Canonical SWE-bench JSONL keeps the upstream field names used by the Python harness
# and the SWE-bench leaderboard. The mapping from SWE-bench-jl's schema is:
#
#   Kept verbatim:
#     instance_id, repo, base_commit, problem_statement, patch, test_patch,
#     created_at, FAIL_TO_PASS, PASS_TO_PASS
#
#   Renamed:
#     julia_version -> version          (upstream uses "version" for the runtime version)
#
#   Added (constants / derived):
#     environment_setup_commit = base_commit
#       The Julia env (Project.toml + Manifest.toml) is pinned at base_commit, so the
#       environment setup commit equals the base commit.  Upstream SWE-bench uses this
#       field to know which commit to check out before running `pip install -e .`.
#
#     hints_text = ""
#       SWE-bench records retrieved issue comments as hints; we do not redistribute them
#       here, so the field is set to the empty string.
#
# Fields present in the Julia schema but absent from the canonical output:
#   repo_url, fix_commit, subject, statement_source, issue_url
# These are useful for provenance and re-mining but are not part of the SWE-bench contract.

const _KEEP_FIELDS = [
    "instance_id", "repo", "base_commit", "problem_statement",
    "patch", "test_patch", "created_at", "FAIL_TO_PASS", "PASS_TO_PASS",
]

"""
    export_official(instances_path, out_path)

Read SWE-bench-jl instances from `instances_path` (JSONL) and write canonical
SWE-bench JSONL to `out_path`.  Field mapping:
- `julia_version` -> `version`
- `environment_setup_commit` = `base_commit`
- `hints_text` = `""`
- All other canonical fields are copied verbatim.
Returns the number of records written.
"""
function export_official(instances_path::AbstractString, out_path::AbstractString)
    instances = load_instances(instances_path)
    mkpath(dirname(abspath(out_path)))
    n = 0
    open(out_path, "w") do fh
        for inst in instances
            rec = Dict{String,Any}()
            for f in _KEEP_FIELDS
                if haskey(inst, f)
                    rec[f] = inst[f]
                end
            end
            rec["version"] = get(inst, "julia_version", "")
            rec["environment_setup_commit"] = get(inst, "base_commit", "")
            rec["hints_text"] = ""
            write(fh, JSON3.write(rec), "\n")
            n += 1
        end
    end
    return n
end
