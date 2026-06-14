"""
discover_repos.py — SWE-bench-jl repository-universe discovery (scaling plan P1).

Turns the whole JuliaRegistries/General registry (~13.8k packages) into a ranked
`repos.tsv` of *mineable, pure-Julia, non-GPU* GitHub repositories, so the miner
(P2 `mine_all.py`) has a work-list instead of a hand-typed repo name.

Two-stage design so we never blow the GitHub API budget on 13k repos:

  1. OFFLINE  — parse the local registry checkout only (no network):
       Registry.toml  -> package name + path
       <path>/Package.toml  -> git repo URL
       <path>/Deps.toml     -> dependency names (GPU / *_jll landmine filter)
       <path>/Versions.toml -> number of released versions (maturity / churn proxy)
       <path>/Compat.toml    -> declared `julia` compat (for P3 version mapping later)
     Rank ALL surviving packages by version count; dedupe to one row per repo.

  2. ONLINE   — enrich only the top `--enrich N` repos via the `gh` CLI:
       repos/{o}/{r}                       -> stars, license, archived, fork, pushed_at
       repos/{o}/{r}/contents/test/runtests.jl -> has a test suite (mineable at all)
     Results are cached to JSON so re-runs are free and resumable.

Tier A needs only the top ~30 surviving rows, so `--enrich 150` is plenty of slack.

Usage
-----
    # clone the registry once (offline source of truth):
    git clone --depth 1 --filter=blob:none \\
        https://github.com/JuliaRegistries/General work/General

    python3 collect/discover_repos.py --registry work/General \\
        --enrich 150 --out collect/repos.tsv

    # offline only (no gh calls), e.g. to inspect the ranking:
    python3 collect/discover_repos.py --registry work/General --enrich 0 --out repos.tsv

    python3 collect/discover_repos.py --self-test
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import tomllib


# ── tunables ─────────────────────────────────────────────────────────────────

# Dependency names that mean "needs a GPU / fragile native stack" — environment
# build landmines we exclude up front (scaling plan filter ⑤). Ordinary *_jll
# (precompiled BinaryBuilder artifacts, e.g. OpenSSL_jll) are NOT excluded by
# default: real targets like HTTP.jl depend on them and they download fine.
GPU_DEPS = {
    "CUDA", "AMDGPU", "Metal", "oneAPI", "GPUArrays", "GPUCompiler",
    "KernelAbstractions", "ROCArrays", "CuArrays", "CUDAnative", "CUDAdrv",
    "OpenCL", "Vulkan", "CUDAKernels", "ROCKernels", "GPUArraysCore",
}
_GPU_JLL_RE = re.compile(r"(CUDA|CUDNN|CUTENSOR|ROCm|AMDGPU|oneAPI|Vulkan|OpenCL)", re.I)

# SPDX ids permissive enough to redistribute patch/test snippets (NOTICES.md policy).
PERMISSIVE_LICENSES = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear",
    "Apache-2.0", "ISC", "MPL-2.0", "Zlib", "BSL-1.0",
}


# ── pure helpers (covered by --self-test) ────────────────────────────────────

def slug_from_url(url: str) -> str | None:
    """`https://github.com/Org/Name.jl.git` -> `Org/Name.jl` (keep the .jl, drop .git)."""
    if not url:
        return None
    m = re.search(r"github\.com[/:]([^/]+)/(.+?)(?:\.git)?/?$", url.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def is_gpu_dep(name: str) -> bool:
    if name in GPU_DEPS:
        return True
    if name.endswith("_jll") and _GPU_JLL_RE.search(name):
        return True
    return False


def classify_tier(n_versions: int, stars: int | None) -> tuple[str, int]:
    """(tier, est_instances) from maturity/popularity, per the plan's density table."""
    s = stars or 0
    if n_versions >= 80 or s >= 500:
        return "large", 35
    if n_versions >= 25 or s >= 150:
        return "medium", 10
    return "small", 5


# ── offline registry parsing ─────────────────────────────────────────────────

def _toml(path: pathlib.Path):
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return None


def parse_registry(reg_dir: pathlib.Path):
    """Yield one dict per non-jll package: name, repo_url, slug, n_versions, deps, julia_compat."""
    reg = _toml(reg_dir / "Registry.toml")
    if reg is None:
        raise SystemExit(f"cannot read {reg_dir/'Registry.toml'} — clone the General registry first")
    packages = reg.get("packages", {})

    for _uuid, ent in packages.items():
        name = ent.get("name", "")
        path = ent.get("path", "")
        if not name or not path:
            continue
        # wrapper artifact packages: no real test suite to mine
        if name.endswith("_jll") or path.startswith("jll/"):
            continue

        pdir = reg_dir / path
        pkg = _toml(pdir / "Package.toml")
        if not pkg:
            continue
        slug = slug_from_url(pkg.get("repo", ""))
        if slug is None:  # non-GitHub host
            continue

        deps = _collect_deps(pdir / "Deps.toml")
        n_versions = _count_versions(pdir / "Versions.toml")
        julia_compat = _julia_compat(pdir / "Compat.toml")

        yield {
            "name": name,
            "repo_url": pkg["repo"],
            "slug": slug,
            "n_versions": n_versions,
            "n_deps": len(deps),
            "deps": deps,
            "julia_compat": julia_compat,
        }


def _collect_deps(path: pathlib.Path) -> set:
    data = _toml(path)
    if not data:
        return set()
    names = set()
    for _ver_range, table in data.items():
        if isinstance(table, dict):
            names.update(table.keys())
    return names


def _count_versions(path: pathlib.Path) -> int:
    data = _toml(path)
    return len(data) if data else 0


def _julia_compat(path: pathlib.Path) -> str:
    data = _toml(path)
    if not data:
        return ""
    found = ""
    for _ver_range, table in data.items():
        if isinstance(table, dict) and "julia" in table:
            found = str(table["julia"])  # last (≈ newest range) wins
    return found


def offline_filter(pkgs, *, exclude_jll: bool):
    """Drop GPU-dependent (and optionally any *_jll-dependent) packages."""
    for p in pkgs:
        gpu = any(is_gpu_dep(d) for d in p["deps"])
        if gpu:
            continue
        if exclude_jll and any(d.endswith("_jll") for d in p["deps"]):
            continue
        yield p


def dedupe_by_repo(pkgs):
    """One row per repo slug (monorepos register many packages). Keep max version count."""
    best = {}
    for p in pkgs:
        key = p["slug"].lower()
        cur = best.get(key)
        if cur is None or p["n_versions"] > cur["n_versions"]:
            # carry the union of package names sharing this repo for visibility
            names = set(cur["names"]) if cur else set()
            names.add(p["name"])
            best[key] = {**p, "names": names}
        else:
            cur["names"].add(p["name"])
    return list(best.values())


# ── online enrichment (gh CLI), cached ───────────────────────────────────────

def gh_json(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def gh_exists(path: str) -> bool:
    r = subprocess.run(["gh", "api", path, "--silent"], capture_output=True, text=True)
    return r.returncode == 0


def enrich_repo(slug: str, sleep: float) -> dict:
    """Fetch stars/license/flags + test-suite presence for one repo. {} on hard failure."""
    meta = gh_json(f"repos/{slug}")
    if not isinstance(meta, dict):
        return {"_error": "repo_meta_failed"}
    time.sleep(sleep)
    has_tests = gh_exists(f"repos/{slug}/contents/test/runtests.jl")
    time.sleep(sleep)
    lic = (meta.get("license") or {})
    return {
        "stars": meta.get("stargazers_count", 0),
        "license": lic.get("spdx_id") or "NONE",
        "archived": bool(meta.get("archived")),
        "fork": bool(meta.get("fork")),
        "pushed_at": meta.get("pushed_at") or "",
        "default_branch": meta.get("default_branch") or "",
        "has_tests": has_tests,
    }


def load_cache(path: pathlib.Path) -> dict:
    if path and path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(path: pathlib.Path, cache: dict):
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=0))


# ── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default="work/General",
                   help="path to a JuliaRegistries/General checkout")
    p.add_argument("--out", default="collect/repos.tsv")
    p.add_argument("--enrich", type=int, default=150,
                   help="enrich the top-N offline-ranked repos via gh (0 = offline only)")
    p.add_argument("--min-versions", type=int, default=3,
                   help="offline prefilter: require at least this many released versions")
    p.add_argument("--min-stars", type=int, default=10,
                   help="online filter: drop enriched repos below this star count")
    p.add_argument("--exclude-jll", action="store_true",
                   help="also drop packages with any *_jll dependency (strict; drops HTTP-like repos)")
    p.add_argument("--exclude-covered", metavar="INSTANCES.jsonl", default=None,
                   help="mark repos already present in this instances file as covered=yes")
    p.add_argument("--require-tests", action="store_true",
                   help="emit only repos confirmed to have test/runtests.jl (needs --enrich)")
    p.add_argument("--require-permissive", action="store_true",
                   help="emit only repos with a permissive SPDX license (needs --enrich)")
    p.add_argument("--cache", default="work/discover_cache.json",
                   help="JSON cache of gh enrichment results (resumable)")
    p.add_argument("--sleep", type=float, default=0.2, help="seconds between gh calls")
    return p.parse_args()


def main():
    if "--self-test" in sys.argv:
        return _self_test()
    args = parse_args()
    reg_dir = pathlib.Path(args.registry)

    covered = set()
    if args.exclude_covered and os.path.exists(args.exclude_covered):
        for line in open(args.exclude_covered):
            line = line.strip()
            if line:
                covered.add(json.loads(line)["repo"].lower())

    print(f"[discover] parsing registry at {reg_dir} ...", file=sys.stderr)
    pkgs = list(offline_filter(parse_registry(reg_dir), exclude_jll=args.exclude_jll))
    print(f"[discover] {len(pkgs)} non-GPU{' non-jll' if args.exclude_jll else ''} packages", file=sys.stderr)

    repos = dedupe_by_repo(pkgs)
    repos = [r for r in repos if r["n_versions"] >= args.min_versions]
    repos.sort(key=lambda r: r["n_versions"], reverse=True)
    print(f"[discover] {len(repos)} repos with >= {args.min_versions} versions", file=sys.stderr)

    # online enrichment of the top slice, cached + resumable
    cache_path = pathlib.Path(args.cache) if args.cache else None
    cache = load_cache(cache_path)
    to_enrich = repos[: args.enrich] if args.enrich > 0 else []
    n_calls = 0
    for i, r in enumerate(to_enrich):
        slug = r["slug"]
        if slug not in cache:
            cache[slug] = enrich_repo(slug, args.sleep)
            n_calls += 1
            if n_calls % 20 == 0:
                save_cache(cache_path, cache)
                print(f"[discover] enriched {n_calls} (at {slug})", file=sys.stderr)
    save_cache(cache_path, cache)
    if to_enrich:
        print(f"[discover] enrichment done ({n_calls} new gh fetches, {len(cache)} cached)", file=sys.stderr)

    # assemble rows
    rows = []
    for r in repos:
        e = cache.get(r["slug"], {})
        stars = e.get("stars")
        has_tests = e.get("has_tests")
        license_ = e.get("license", "?")
        archived = e.get("archived", False)
        fork = e.get("fork", False)
        tier, est = classify_tier(r["n_versions"], stars)
        rows.append({
            "slug": r["slug"],
            "stars": stars if stars is not None else "",
            "n_versions": r["n_versions"],
            "n_deps": r["n_deps"],
            "license": license_,
            "has_tests": "" if has_tests is None else ("yes" if has_tests else "no"),
            "archived": "yes" if archived else "no",
            "fork": "yes" if fork else "no",
            "julia_compat": r["julia_compat"],
            "pushed_at": e.get("pushed_at", ""),
            "tier": tier,
            "est_instances": est,
            "covered": "yes" if r["slug"].lower() in covered else "no",
        })

    # quality gates (only meaningful for enriched rows)
    def keep(row, enriched):
        if row["covered"] == "yes":
            return False
        if not enriched:
            return True
        if row["archived"] == "yes" or row["fork"] == "yes":
            return False
        if args.require_tests and row["has_tests"] != "yes":
            return False
        if args.require_permissive and row["license"] not in PERMISSIVE_LICENSES:
            return False
        if isinstance(row["stars"], int) and row["stars"] < args.min_stars:
            return False
        return True

    enriched_slugs = {r["slug"] for r in to_enrich}
    kept = [r for r in rows if keep(r, r["slug"] in enriched_slugs)]

    # rank: enriched repos first (by stars then versions), then offline tail by versions
    def rank_key(row):
        st = row["stars"] if isinstance(row["stars"], int) else -1
        return (st, row["n_versions"])
    kept.sort(key=rank_key, reverse=True)

    cols = ["slug", "stars", "n_versions", "n_deps", "license", "has_tests",
            "archived", "fork", "julia_compat", "pushed_at", "tier",
            "est_instances", "covered"]
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cum = 0
    with open(out_path, "w") as fh:
        fh.write("\t".join(["rank", *cols, "cum_est"]) + "\n")
        for i, row in enumerate(kept, 1):
            cum += row["est_instances"]
            vals = [str(i)] + [str(row[c]) for c in cols] + [str(cum)]
            fh.write("\t".join(vals) + "\n")

    n_to_300 = next((i for i, _ in enumerate(_cumsum(r["est_instances"] for r in kept), 1)
                     if _ >= 300), None)
    print(f"[discover] wrote {len(kept)} repos to {out_path}", file=sys.stderr)
    if n_to_300:
        print(f"[discover] top {n_to_300} repos cover an estimated 300 instances (Tier A)", file=sys.stderr)
    else:
        print(f"[discover] estimated reach across all rows: {cum} instances", file=sys.stderr)


def _cumsum(it):
    total = 0
    for x in it:
        total += x
        yield total


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test():
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"FAIL {name}: got {got!r} want {want!r}")
        else:
            print(f"  ok  {name}")

    check("slug_https", slug_from_url("https://github.com/Org/Name.jl.git"), "Org/Name.jl")
    check("slug_no_git", slug_from_url("https://github.com/Org/Name.jl"), "Org/Name.jl")
    check("slug_ssh", slug_from_url("git@github.com:Org/Name.jl.git"), "Org/Name.jl")
    check("slug_nongithub", slug_from_url("https://gitlab.com/x/y.jl.git"), None)
    check("slug_empty", slug_from_url(""), None)

    check("gpu_cuda", is_gpu_dep("CUDA"), True)
    check("gpu_jll", is_gpu_dep("CUDA_Runtime_jll"), True)
    check("gpu_plain_jll", is_gpu_dep("OpenSSL_jll"), False)
    check("gpu_normal", is_gpu_dep("DataStructures"), False)

    check("tier_large_versions", classify_tier(100, 0), ("large", 35))
    check("tier_large_stars", classify_tier(5, 800), ("large", 35))
    check("tier_medium", classify_tier(30, 0), ("medium", 10))
    check("tier_small", classify_tier(4, 5), ("small", 5))

    check("cumsum", list(_cumsum([5, 10, 35])), [5, 15, 50])

    print()
    if fails:
        for f in fails:
            print(f, file=sys.stderr)
        sys.exit(1)
    print("all self-tests passed")


if __name__ == "__main__":
    main()
