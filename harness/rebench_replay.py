"""
rebench_replay.py -- ReBench gauge wrapper around episodes.py replay.

Replays one recorded agent trajectory (the patch + check sequence captured under
work/eval/live/<instance>__<model>__<mode>/) against one backend, verifies the
replayed verdicts match the recorded ones, and emits a single RebenchLog line:

    <benchmark-name>: iterations=1 runtime: <total_us>us

where total = sum over the replayed checks of the engine-internal per-check ms,
times 1000 (us). That is the trajectory TOTAL check time: NOT wall-clock, NOT the
workspace preparation or server boot.

Usage
-----
    python3 rebench_replay.py <benchmark-name> --backend <cold|daemon|kiln>

benchmark-name is one of:
    C179-sonnet  C179-haiku  C172-sonnet  C172-haiku  OC136-sonnet  OC136-haiku

Each invocation rebuilds a fresh, private replay workspace and kills any server
left over from a previous invocation, so repeated invocations are i.i.d. (no warmth
or socket leaks across them). The Julia binary follows the swebench JULIA_BIN
convention; --julia (or $JULIA_BIN / $KILN_JULIA) overrides the default below.
"""

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SWE_ROOT = os.path.normpath(os.path.join(HERE, ".."))

DEFAULT_JULIA = "/home/yusuke/.julia/juliaup/julia-1.12.6+0.x64.linux.gnu/bin/julia"

# benchmark name -> recorded instance id + model label
BENCHMARKS = {
    "C179-sonnet":  ("JuliaMath__Combinatorics-179", "sonnet"),
    "C179-haiku":   ("JuliaMath__Combinatorics-179", "haiku"),
    "C172-sonnet":  ("JuliaMath__Combinatorics-172", "sonnet"),
    "C172-haiku":   ("JuliaMath__Combinatorics-172", "haiku"),
    "OC136-sonnet": ("JuliaCollections__OrderedCollections-136", "sonnet"),
    "OC136-haiku":  ("JuliaCollections__OrderedCollections-136", "haiku"),
}

BACKENDS = ("cold", "daemon", "kiln")


def _live_dir(instance, model, mode):
    return os.path.join(SWE_ROOT, "work", "eval", "live",
                        f"{instance}__{model}__{mode}")


def _attempt_num(fname):
    try:
        return int(fname[len("attempt_"):-len(".patch")])
    except ValueError:
        return 0


def _load_attempts(attempts_dir):
    files = [f for f in os.listdir(attempts_dir)
             if f.startswith("attempt_") and f.endswith(".patch")]
    return sorted(files, key=_attempt_num)


def _recorded_verdicts(kilnswe_dir):
    telem = os.path.join(kilnswe_dir, "telemetry.jsonl")
    rows = []
    with open(telem) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r.get("attempt", 0))
    return [r.get("verdict") for r in rows]


def main():
    p = argparse.ArgumentParser(prog="rebench_replay.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("benchmark", choices=sorted(BENCHMARKS.keys()),
                   help="recorded trajectory benchmark name")
    p.add_argument("--backend", required=True, choices=BACKENDS,
                   help="replay backend / mode")
    p.add_argument("--julia", default=None,
                   help="Julia binary (default $JULIA_BIN / $KILN_JULIA / built-in)")
    args = p.parse_args()

    julia = (args.julia or os.environ.get("JULIA_BIN")
             or os.environ.get("KILN_JULIA") or DEFAULT_JULIA)
    # episodes.py reads JULIA_BIN at import time and records it in config.json
    # at prepare; set it before importing so the replay uses the right binary.
    os.environ["JULIA_BIN"] = julia

    sys.path.insert(0, HERE)
    import episodes

    instance, model = BENCHMARKS[args.benchmark]
    mode = args.backend

    live = _live_dir(instance, model, mode)
    kilnswe = os.path.join(live, ".kilnswe")
    attempts_dir = os.path.join(kilnswe, "attempts")
    if not os.path.isdir(attempts_dir):
        print(f"recorded attempts not found: {attempts_dir}", file=sys.stderr)
        sys.exit(2)

    recorded = _recorded_verdicts(kilnswe)
    patch_files = _load_attempts(attempts_dir)
    if not patch_files:
        print(f"no attempt patches under {attempts_dir}", file=sys.stderr)
        sys.exit(2)

    instances_jsonl = os.path.join(SWE_ROOT, "data", "instances.jsonl")

    ws = tempfile.mkdtemp(prefix=f"replay_{args.benchmark}_{mode}_",
                          dir=os.path.join(SWE_ROOT, "work", "eval", "replay"))
    ws = os.path.join(ws, f"{instance}__{mode}")

    try:
        # kill any leaked server/socket for this fresh workspace before starting
        episodes._stop_server_for_dir(ws)

        prep_args = argparse.Namespace(
            instances=instances_jsonl, instance_id=instance, mode=mode, dest=ws,
        )
        # cmd_prepare prints the workspace path; keep it off the RebenchLog stream.
        with contextlib.redirect_stdout(sys.stderr):
            episodes.cmd_prepare(prep_args)

        ws_kilnswe = os.path.join(ws, ".kilnswe")
        with open(os.path.join(ws_kilnswe, "config.json")) as fh:
            cfg = json.load(fh)
        with open(os.path.join(ws_kilnswe, "instance.json")) as fh:
            inst = json.load(fh)

        f2p = inst.get("FAIL_TO_PASS", [])
        p2p = inst.get("PASS_TO_PASS", [])
        needed = f2p + p2p

        total_ms = 0.0
        replayed = []

        for pf in patch_files:
            patch_path = os.path.join(attempts_dir, pf)

            episodes._run(["git", "-C", ws, "checkout", "--", "src"])
            episodes._run(["git", "-C", ws, "checkout", "--", "ext"])

            with open(patch_path, "rb") as fh:
                patch_content = fh.read()
            if patch_content.strip():
                fd, tmp = tempfile.mkstemp(suffix=".patch")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(patch_content)
                    episodes._run(["git", "-C", ws, "apply", tmp])
                finally:
                    os.unlink(tmp)

            kind, check_ms, restart, report = episodes.run_check(ws, cfg, inst)
            total_ms += check_ms

            if report is None or report.get("partial"):
                idx = {t["path"]: t for t in (report or {}).get("tests", [])}
                failing = [q for q in needed if q in idx and idx[q]["status"] != "pass"]
                verdict = "fail"
            else:
                idx = {t["path"]: t for t in report.get("tests", [])}
                failing = [q for q in needed if idx.get(q, {}).get("status") != "pass"]
                verdict = "pass" if not failing else "fail"
            replayed.append(verdict)

        episodes._stop_server_for_dir(ws)
    finally:
        episodes._stop_server_for_dir(ws)
        shutil.rmtree(os.path.dirname(ws), ignore_errors=True)

    if replayed != recorded:
        print(f"PARITY MISMATCH {args.benchmark}/{mode}: "
              f"replayed={replayed} recorded={recorded}", file=sys.stderr)
        sys.exit(1)

    total_us = int(round(total_ms * 1000.0))
    print(f"{args.benchmark}: iterations=1 runtime: {total_us}us")


if __name__ == "__main__":
    main()
