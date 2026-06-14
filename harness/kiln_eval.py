"""
kiln_eval.py — end-to-end driver to measure cold / daemon / kiln / kiln-rt execution
performance on SWE-bench-jl using real coding-agent edit->check loops as the workload.

Methodology (one episode = one agent solving one instance):

  1. prepare : build a cold workspace per (agent, instance) — clone@base + test_patch +
               hermetic env + PROBLEM.md + ./check. Resets telemetry for a clean episode.
  2. drive   : run the agent's edit -> ./check loop (codex via `codex exec`, claude models
               via headless `claude -p`). Each ./check snapshots the src diff as
               attempt_N.patch — that ordered patch sequence IS the recorded workload.
  3. replay  : re-apply each episode's attempt_N.patch sequence under every execution mode
               (cold, daemon, kiln, kiln-rt) and record per-attempt check_ms.
  4. score   : independently score each agent's FINAL src patch with the standalone
               SWE-bench-jl evaluator (src-only AND FAIL_TO_PASS-flip AND PASS_TO_PASS-hold).
               This is the authoritative resolve verdict.
  5. report  : aggregate per (agent, instance): iteration count, per-mode episode wall-clock,
               speedup vs cold, and resolved (from score). Selective modes (kiln, kiln-rt)
               emit partial reports, so their per-mode VERDICT is timing-only — never a
               resolve signal; resolution always comes from `score`.

Gotchas encoded here (learned the hard way):
  * Never invoke bare `julia`: on juliaup systems it is a launcher that blocks on a config
    lock. resolve_julia() returns the real binary under ~/.julia/juliaup/*/bin/julia.
  * The standalone bin/swebenchjl has a `#!/usr/bin/env julia` shebang, so it must be run
    AS an argument to the real binary, not executed directly.
  * Authoritative resolution comes from a full run (the scorer), not selective replay modes.

Usage:
    python3 kiln_eval.py run     <instances.jsonl> --instances ID[,ID...] \
                                  --agents haiku,sonnet,codex --dest-root DIR
    python3 kiln_eval.py prepare <instances.jsonl> --instances ... --agents ... --dest-root DIR
    python3 kiln_eval.py drive   --workspace WS --agent <name> [--max-checks 4]
    python3 kiln_eval.py replay  --dest-root DIR [--modes cold,daemon,kiln,kiln-rt]
    python3 kiln_eval.py score   <instances.jsonl> --dest-root DIR [--swebench-jl PATH]
    python3 kiln_eval.py report  --dest-root DIR
"""

import argparse
import collections
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EPISODES = os.path.join(HERE, "episodes.py")
# This harness ships inside the SWE-bench-jl repo (harness/), so the repo root is HERE/..
DEFAULT_SWEBENCH_JL = os.environ.get(
    "SWEBENCH_JL", os.path.normpath(os.path.join(HERE, "..")))
DEFAULT_MODES = "cold,daemon,kiln,kiln-rt"
ALL_MODES = ["cold", "daemon", "kiln", "kiln-rt"]

# Agents whose patches resolve under a FULL run are the only resolve authority; the agent
# driver recipes below are the headless, reproducible equivalents of the loop we ran.
AGENT_MODELS = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}  # -> claude --model
CODEX_AGENTS = {"codex"}

AGENT_PROMPT = (
    "You are fixing a real bug in the Julia package in this directory. "
    "First read PROBLEM.md for the bug report and the rules. "
    "Then fix the bug by editing ONLY files under src/ (or ext/); NEVER edit anything under "
    "test/, and do NOT run `git commit`. After each edit run ./check to verify — it prints a "
    "line starting with PASS when the fix is correct and FAIL otherwise (each run can take a "
    "few minutes; wait for it, do not kill it). Iterate edit -> ./check until ./check prints "
    "PASS, or until you have run ./check {max_checks} times, whichever comes first. "
    "When finished, state whether ./check printed PASS, how many checks you ran, and your fix."
)


# ── julia / process helpers ──────────────────────────────────────────────────

def resolve_julia():
    """Return the real julia binary, never the juliaup launcher."""
    env = os.environ.get("JULIA_BIN")
    if env and os.access(env, os.X_OK) and "juliaup/bin" not in env:
        return env
    cands = sorted(glob.glob(os.path.expanduser("~/.julia/juliaup/julia-*/bin/julia")))
    if cands:
        return cands[-1]
    return "julia"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def episode_dirs(dest_root):
    """Yield (agent, instance_id, workspace) for every prepared episode under dest_root."""
    for ws in sorted(glob.glob(os.path.join(dest_root, "*"))):
        meta = os.path.join(ws, ".kilnswe", "episode.json")
        if os.path.isfile(meta):
            m = json.load(open(meta))
            yield m["agent"], m["instance_id"], ws


# ── 1. prepare ───────────────────────────────────────────────────────────────

def prepare_one(instances, instance_id, agent, dest_root, julia):
    ws = os.path.join(dest_root, f"{instance_id}__{agent}")
    env = dict(os.environ, JULIA_BIN=julia)
    r = run([sys.executable, EPISODES, "prepare", instances, instance_id,
             "--mode", "cold", "--dest", ws], env=env)
    if r.returncode != 0:
        print(f"  prepare FAILED {instance_id}/{agent}: {r.stderr[-300:]}", file=sys.stderr)
        return None
    kilnswe = os.path.join(ws, ".kilnswe")
    # clean start: drop any attempt/telemetry from prepare-time baseline checks
    for f in glob.glob(os.path.join(kilnswe, "attempts", "*.patch")):
        os.remove(f)
    tel = os.path.join(kilnswe, "telemetry.jsonl")
    if os.path.exists(tel):
        os.remove(tel)
    json.dump({"agent": agent, "instance_id": instance_id},
              open(os.path.join(kilnswe, "episode.json"), "w"))
    print(f"  prepared {instance_id}/{agent} -> {ws}")
    return ws


def cmd_prepare(args):
    julia = resolve_julia()
    ids = [s.strip() for s in args.instances_sel.split(",") if s.strip()]
    agents = [s.strip() for s in args.agents.split(",") if s.strip()]
    os.makedirs(args.dest_root, exist_ok=True)
    n = 0
    for iid in ids:
        for ag in agents:
            if prepare_one(args.instances, iid, ag, args.dest_root, julia):
                n += 1
    print(f"prepared {n} episode workspaces in {args.dest_root}")


# ── 2. drive (agent edit->check loop) ────────────────────────────────────────

def drive_one(ws, agent, max_checks, timeout):
    prompt = AGENT_PROMPT.format(max_checks=max_checks)
    if agent in CODEX_AGENTS:
        cmd = ["codex", "exec", "--sandbox", "workspace-write", prompt]
    else:
        model = AGENT_MODELS.get(agent, agent)
        cmd = ["claude", "-p", prompt, "--model", model,
               "--permission-mode", "bypassPermissions"]
    print(f"  driving {agent} in {ws} ...", file=sys.stderr)
    t0 = time.monotonic()
    r = run(cmd, cwd=ws, timeout=timeout)
    dt = time.monotonic() - t0
    log = os.path.join(ws, ".kilnswe", f"agent_{agent}.log")
    with open(log, "w") as fh:
        fh.write(r.stdout + "\n---STDERR---\n" + r.stderr)
    n = len(glob.glob(os.path.join(ws, ".kilnswe", "attempts", "*.patch")))
    print(f"  {agent}: {n} checks, {dt:.0f}s (log: {log})", file=sys.stderr)
    return n


def cmd_drive(args):
    if args.workspace:
        meta = json.load(open(os.path.join(args.workspace, ".kilnswe", "episode.json")))
        drive_one(args.workspace, args.agent or meta["agent"], args.max_checks, args.timeout)
        return
    for agent, iid, ws in episode_dirs(args.dest_root):
        drive_one(ws, agent, args.max_checks, args.timeout)


# ── 3. replay across modes ───────────────────────────────────────────────────

def cmd_replay(args):
    julia = resolve_julia()
    env = dict(os.environ, JULIA_BIN=julia)
    out_dir = os.path.join(args.dest_root, "replay")
    os.makedirs(out_dir, exist_ok=True)
    for agent, iid, ws in episode_dirs(args.dest_root):
        attempts = os.path.join(ws, ".kilnswe", "attempts")
        npatch = len(glob.glob(os.path.join(attempts, "*.patch")))
        if npatch == 0:
            print(f"  skip {iid}/{agent}: no recorded attempts", file=sys.stderr)
            continue
        out = os.path.join(out_dir, f"{iid}__{agent}.jsonl")
        open(out, "w").close()  # truncate
        print(f"  replay {iid}/{agent} ({npatch} attempts) x [{args.modes}]", file=sys.stderr)
        r = run([sys.executable, EPISODES, "replay", args.instances, iid,
                 "--attempts-dir", attempts, "--modes", args.modes,
                 "--dest-root", os.path.join(out_dir, f"{iid}__{agent}.work"),
                 "--out", out], env=env)
        if r.returncode != 0:
            print(f"    replay FAILED: {r.stderr[-300:]}", file=sys.stderr)


# ── 4. score (authoritative resolve via standalone evaluator) ────────────────

def cmd_score(args):
    julia = resolve_julia()
    swebench_jl = os.path.abspath(args.swebench_jl)
    binjl = os.path.join(swebench_jl, "bin", "swebenchjl")
    instances = os.path.abspath(args.instances)
    scores = os.path.join(args.dest_root, "scores.jsonl")
    with open(scores, "w") as sfh:
        for agent, iid, ws in episode_dirs(args.dest_root):
            # The agent's authoritative final patch is the last recorded attempt (src-only by
            # construction, and the exact state `replay` scored) — not the live working tree,
            # which is clean for replay-only episodes.
            attempts = sorted(
                glob.glob(os.path.join(ws, ".kilnswe", "attempts", "attempt_*.patch")),
                key=lambda p: int(os.path.basename(p)[len("attempt_"):-len(".patch")]))
            if not attempts:
                print(f"  score {iid}/{agent}: no attempts, skipping", file=sys.stderr)
                continue
            final_patch = os.path.abspath(attempts[-1])
            # test-tampering guard: any test/ edits left in the working tree
            tst = run(["git", "-C", ws, "diff", "HEAD", "--", "test"]).stdout
            src_only = (tst.strip() == "")
            # invoke standalone via the REAL binary (its shebang is the juliaup launcher);
            # all paths absolute since run-one runs with cwd in the standalone repo
            r = run([julia, "--startup-file=no", binjl, "--julia", julia,
                     "run-one", instances, iid, "--model-patch", final_patch],
                    cwd=swebench_jl, env=dict(os.environ, JULIA_BIN=julia))
            out = (r.stdout + r.stderr).lower()
            resolved = ("resolved=true" in out) and src_only
            row = {"agent": agent, "instance_id": iid, "resolved": bool(resolved),
                   "src_only": src_only}
            sfh.write(json.dumps(row) + "\n")
            print(f"  score {iid}/{agent}: resolved={resolved} src_only={src_only}",
                  file=sys.stderr)
    print(f"scores -> {scores}")


# ── 5. report ────────────────────────────────────────────────────────────────

def _fmt(ms):
    return f"{ms/1000:7.1f}s" if ms else "      -"


def cmd_report(args):
    out_dir = os.path.join(args.dest_root, "replay")
    scores = {}
    sp = os.path.join(args.dest_root, "scores.jsonl")
    if os.path.exists(sp):
        for l in open(sp):
            r = json.loads(l)
            scores[(r["agent"], r["instance_id"].split("__")[-1])] = r

    # ep[(agent,inst)][mode] = {ms, n}
    ep = collections.defaultdict(lambda: collections.defaultdict(lambda: {"ms": 0.0, "n": 0}))
    for path in sorted(glob.glob(os.path.join(out_dir, "*.jsonl"))):
        agent = os.path.basename(path)[:-len(".jsonl")].split("__")[-1]
        for l in open(path):
            r = json.loads(l)
            inst = r["instance_id"].split("__")[-1]
            c = ep[(agent, inst)][r["mode"]]
            c["ms"] += r["check_ms"]
            c["n"] += 1

    abbr = {"daemon": "dmn", "kiln": "kiln", "kiln-rt": "krt"}
    present = [m for m in ALL_MODES if any(m in v for v in ep.values())]
    warm = [m for m in present if m != "cold"]
    hdr = (f"{'agent':8s} {'instance':18s} {'res':4s} {'it':3s} "
           + " ".join(f"{m:>9s}" for m in present)
           + "  " + " ".join(f"{abbr.get(m, m)+'/x':>7s}" for m in warm))
    print(hdr)
    print("-" * len(hdr))
    tot = collections.defaultdict(float)
    for (agent, inst) in sorted(ep):
        modes = ep[(agent, inst)]
        it = modes.get("cold", next(iter(modes.values())))["n"]
        cold = modes.get("cold", {}).get("ms", 0.0)
        cells = " ".join(_fmt(modes.get(m, {}).get("ms", 0.0)) for m in present)
        sx = " ".join((f"{cold/modes[m]['ms']:6.1f}x" if modes.get(m, {}).get("ms") else "      -")
                      for m in warm)
        for m in present:
            tot[m] += modes.get(m, {}).get("ms", 0.0)
        sc = scores.get((agent, inst))
        res = ("yes" if sc["resolved"] else "NO") if sc else "?"
        print(f"{agent:8s} {inst:18s} {res:4s} {it:3d} {cells}  {sx}")
    print("-" * len(hdr))
    cold = tot.get("cold", 0.0)
    cells = " ".join(_fmt(tot.get(m, 0.0)) for m in present)
    sx = " ".join((f"{cold/tot[m]:6.1f}x" if tot.get(m) else "      -") for m in warm)
    print(f"{'TOTAL':8s} {'':18s} {'':4s} {'':3s} {cells}  {sx}")
    print()
    print("res = resolved by independent scorer (full-run authority; src-only enforced).")
    print("per-mode columns = episode wall-clock (sum of check_ms); x = speedup vs cold.")
    print("kiln/kiln-rt run selectively -> their verdict is partial; resolution is `res` only.")


# ── full pipeline ────────────────────────────────────────────────────────────

def cmd_run(args):
    cmd_prepare(args)
    # drive every prepared episode
    for agent, iid, ws in episode_dirs(args.dest_root):
        drive_one(ws, agent, args.max_checks, args.timeout)
    cmd_replay(args)
    cmd_score(args)
    cmd_report(args)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, instances=True):
        if instances:
            sp.add_argument("instances", help="instances JSONL (e.g. data/instances_hard.jsonl)")
        sp.add_argument("--dest-root", required=True, help="root for episode workspaces")

    pr = sub.add_parser("prepare"); add_common(pr)
    pr.add_argument("--instances-sel", "--ids", required=True, dest="instances_sel",
                    help="comma-separated instance_ids")
    pr.add_argument("--agents", default="haiku,sonnet,codex")
    pr.set_defaults(func=cmd_prepare)

    dr = sub.add_parser("drive")
    dr.add_argument("--dest-root", required=True)
    dr.add_argument("--workspace", help="drive a single workspace (else all under dest-root)")
    dr.add_argument("--agent", help="override agent name for --workspace")
    dr.add_argument("--max-checks", type=int, default=4)
    dr.add_argument("--timeout", type=int, default=3600)
    dr.set_defaults(func=cmd_drive)

    rp = sub.add_parser("replay"); add_common(rp)
    rp.add_argument("--modes", default=DEFAULT_MODES)
    rp.set_defaults(func=cmd_replay)

    sc = sub.add_parser("score"); add_common(sc)
    sc.add_argument("--swebench-jl", default=DEFAULT_SWEBENCH_JL)
    sc.set_defaults(func=cmd_score)

    rep = sub.add_parser("report")
    rep.add_argument("--dest-root", required=True)
    rep.set_defaults(func=cmd_report)

    rn = sub.add_parser("run"); add_common(rn)
    rn.add_argument("--instances-sel", "--ids", required=True, dest="instances_sel")
    rn.add_argument("--agents", default="haiku,sonnet,codex")
    rn.add_argument("--modes", default=DEFAULT_MODES)
    rn.add_argument("--swebench-jl", default=DEFAULT_SWEBENCH_JL)
    rn.add_argument("--max-checks", type=int, default=4)
    rn.add_argument("--timeout", type=int, default=3600)
    rn.set_defaults(func=cmd_run)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
