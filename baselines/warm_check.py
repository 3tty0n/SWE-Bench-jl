#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

JULIA = os.environ.get("JULIA_BIN", "julia")
HERE = os.path.dirname(os.path.abspath(__file__))


def sh(cmd, cwd=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)


def git_apply(clone, diff):
    fd, path = tempfile.mkstemp(suffix=".diff")
    with os.fdopen(fd, "w") as f:
        f.write(diff)
    try:
        r = sh(["git", "apply", path], cwd=clone)
        if r.returncode != 0:
            raise RuntimeError(f"git apply failed: {r.stdout}")
    finally:
        os.unlink(path)


def setup(inst, root):
    work = os.path.join(root, "work")
    src = os.path.join(work, "repos", "JuliaCollections__OrderedCollections")
    clone = os.path.join(work, "warm", "clone_oc")
    env = os.path.join(work, "warm", "env_oc")
    os.makedirs(os.path.join(work, "warm"), exist_ok=True)

    if not os.path.isdir(clone):
        r = sh(["git", "clone", src, clone])
        if r.returncode != 0:
            raise RuntimeError(f"clone failed: {r.stdout}")

    sh(["git", "reset", "--hard"], cwd=clone)
    sh(["git", "clean", "-fdx"], cwd=clone)
    r = sh(["git", "checkout", "--detach", inst["base_commit"]], cwd=clone)
    if r.returncode != 0:
        raise RuntimeError(f"checkout failed: {r.stdout}")

    git_apply(clone, inst["test_patch"])

    if not os.path.isdir(env):
        r = sh([JULIA, "--startup-file=no",
                os.path.join(HERE, "..", "harness", "testenv.jl"), clone, env])
        if r.returncode != 0:
            raise RuntimeError(f"testenv failed: {r.stdout}")
    r = sh([JULIA, "--startup-file=no", "-e",
            f'using Pkg; Pkg.activate("{env}"); Pkg.add("Revise"); Pkg.precompile()'])
    if r.returncode != 0:
        raise RuntimeError(f"Revise add failed: {r.stdout}")

    return clone, env


def cold_run(env, clone):
    out = tempfile.mktemp(suffix=".json")
    t0 = time.monotonic()
    sh([JULIA, "--startup-file=no", f"--project={env}",
        os.path.join(HERE, "..", "harness", "run_tests.jl"), clone, out])
    ms = (time.monotonic() - t0) * 1000.0
    if os.path.exists(out):
        os.unlink(out)
    return ms


def warm_run(proc, out_json):
    proc.stdin.write(f"RUN\t{out_json}\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server died")
        line = line.rstrip("\n")
        if line.startswith("DONE\t"):
            return int(line.split("\t")[1])
        if line.startswith("ERR\t"):
            raise RuntimeError(f"warm run error: {line}")


def f2p_status(out_json, f2p_paths):
    with open(out_json) as f:
        rep = json.load(f)
    by_path = {n["path"]: n for n in rep["tests"]}
    ok = True
    for p in f2p_paths:
        node = by_path.get(p)
        if node is None or node["status"] != "pass":
            ok = False
    return ok, rep["total"]["pass"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instances")
    ap.add_argument("--instance-id")
    ap.add_argument("--swebench-root", default=os.path.normpath(os.path.join(HERE, "..")))
    ap.add_argument("--out")
    args = ap.parse_args()

    inst = None
    with open(args.instances) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if args.instance_id is None or d["instance_id"] == args.instance_id:
                inst = d
                break
    if inst is None:
        print("instance not found", file=sys.stderr)
        sys.exit(1)

    root = args.swebench_root
    clone, env = setup(inst, root)

    cold_run(env, clone)
    cold_ms = cold_run(env, clone)

    out_first = tempfile.mktemp(suffix=".json")
    out_edit = tempfile.mktemp(suffix=".json")

    proc = subprocess.Popen(
        [JULIA, "--startup-file=no", f"--project={env}",
         os.path.join(HERE, "warm_server.jl"), clone],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
        text=True, bufsize=1)

    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server failed to start")
        if line.rstrip("\n") == "READY":
            break

    warm_first_ms = warm_run(proc, out_first)

    git_apply(clone, inst["patch"])

    warm_edit_ms = warm_run(proc, out_edit)

    proc.stdin.write("QUIT\n")
    proc.stdin.flush()
    proc.wait()

    f2p_ok, n_tests = f2p_status(out_edit, inst["FAIL_TO_PASS"])

    result = {
        "instance_id": inst["instance_id"],
        "cold_ms": round(cold_ms, 1),
        "warm_first_ms": warm_first_ms,
        "warm_edit_ms": warm_edit_ms,
        "speedup_vs_cold": round(cold_ms / warm_edit_ms, 2) if warm_edit_ms else None,
        "f2p_pass_warm": f2p_ok,
        "n_tests_warm": n_tests,
    }

    for p in (out_first, out_edit):
        if os.path.exists(p):
            os.unlink(p)

    txt = json.dumps(result)
    print(txt)
    if args.out:
        with open(args.out, "w") as f:
            f.write(txt + "\n")


if __name__ == "__main__":
    main()
