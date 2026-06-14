using Pkg
using TOML

function build_from_test_project(repo_dir, env_dir, test_project)
    mkpath(env_dir)
    cp(test_project, joinpath(env_dir, "Project.toml"); force=true)
    Pkg.activate(env_dir)
    Pkg.develop(path=repo_dir)
    Pkg.instantiate()
end

function build_from_extras(repo_dir, env_dir)
    proj = TOML.parsefile(joinpath(repo_dir, "Project.toml"))
    extras = get(proj, "extras", Dict{String,Any}())
    targets = get(proj, "targets", Dict{String,Any}())
    test_names = String[]
    if haskey(targets, "test")
        append!(test_names, String.(targets["test"]))
    end

    mkpath(env_dir)
    Pkg.activate(env_dir)
    Pkg.develop(path=repo_dir)
    for name in test_names
        haskey(extras, name) || continue
        Pkg.add(name=name, uuid=extras[name])
    end
    Pkg.instantiate()
end

function main()
    length(ARGS) >= 2 || (println(stderr, "usage: testenv.jl <repo_dir> <env_dir>"); exit(2))
    repo_dir = abspath(ARGS[1])
    env_dir = abspath(ARGS[2])
    test_project = joinpath(repo_dir, "test", "Project.toml")

    try
        if isfile(test_project)
            build_from_test_project(repo_dir, env_dir, test_project)
        else
            build_from_extras(repo_dir, env_dir)
        end
        try
            Pkg.precompile()
        catch pe
            println(stderr, "warning: precompile failed: ", sprint(showerror, pe))
        end
    catch e
        println(stderr, "testenv error: ", sprint(showerror, e))
        exit(1)
    end

    println(env_dir)
    exit(0)
end

main()
