# SWE-bench-jl — Publication Plan

A plan to publish SWE-bench-jl at a dataset/benchmark venue (NeurIPS Evaluations &
Datasets, MSR Data & Tool Showcase, or an ML/LLM main track). Decision-first: what the
paper *argues*, then where it goes, then the one experiment that gates everything, then
the work.

Status snapshot (see bottom for live numbers): ~1,563 execution-validated instances /
~158 repos, three tiers defined, archival scaffolding (`.zenodo.json`, `CITATION.cff`,
`LICENSE`, `NOTICES.md`, `dataset_card.md`) already in place.

---

## 1. Thesis — what the paper argues

### Stated thesis

> Coding-agent performance is not language-invariant. We build the first
> execution-validated, large-scale Julia SWE-bench and use a controlled cross-language
> comparison to isolate language/ecosystem effects from task difficulty.

### The three contributions it rests on

- **C1 — Cross-language agent gap (scientific core).** Agent skill does not transfer
  freely across languages. A controlled comparison (same agents; Julia vs. Python
  Verified subsets matched on task characteristics) decomposes the gap into
  *language/ecosystem* effects (training-data scarcity, multiple dispatch, macros,
  precompilation, `Pkg`) vs. *task-difficulty* effects. This is a result, not a dataset.
- **C2 — Docker-free, `Pkg`/`Manifest` validation methodology.** SWE-bench's
  one-Docker-image-per-instance model is heavy; Julia's environment model gives hermetic
  reproducibility without it. A reusable *method* contribution (SE venues value this).
- **C3 — The artifact.** ~1,600 execution-validated instances / ~158 repos, three tiers
  (Full / Lite / Verified), permissively licensed, DOI-archived, pure-Julia harness.

C3 alone is an MSR showcase. C1+C2 on top of C3 is a NeurIPS/ICLR-grade paper.

### Critical review of the thesis (threats, ranked)

1. **The "language effect" is confounded.** Matching on patch-size / #F2P /
   statement-length does not control intrinsic conceptual difficulty. The matched-pair
   number alone is attackable ("your Julia bugs are just harder bugs"). *Mitigation:*
   multiple matching strategies + a qualitative, Julia-specific failure taxonomy that is
   the real vehicle for the causal story.
2. **Contamination runs the other way.** Python SWE-bench fixes are heavily memorized by
   frontier models; Julia is not. A Python>Julia gap may be contamination asymmetry, not
   language difficulty. *Mitigation:* address head-on; spin as "Julia = low-contamination
   benchmark"; use the in-dataset commit-vs-issue `statement_source` leakage probe.
3. **May read as already-known.** SWE-bench Multilingual / SWE-rebench-V2 already show
   off-Python degradation. *Mitigation:* novelty must be the **decomposition** of the
   gap, not its existence.
4. **Contingent on data not yet in hand.** Only baseline so far is a 4-instance pilot
   (12/12 resolved). If Verified rates come back high, there is no gap to study.
   *Mitigation:* frame as a hypothesis; ensure the paper is valuable **either outcome**
   (a *small* gap — "agents transfer to Julia surprisingly well" — is also publishable).
5. **"First" can die to one citation.** SWE-rebench-V2 contains Julia. Qualify exactly —
   *first execution-validated, SWE-bench-contract, pure-Julia-harness, tiered* — and
   verify each qualifier against SWE-rebench-V2's actual methodology before submission.
6. **Single Julia version (1.12.6).** Per-instance Julia-version selection is not yet
   built; the C2 reproducibility claim must be scoped honestly.

### Recommended defensible framing

Make **C2+C3 (artifact + methodology) load-bearing** — true today, venue-ready. Carry
**C1 (cross-language gap) as the headline hypothesis whose value is robust to outcome.**
Let the **failure taxonomy** (qualitative) do the causal storytelling. This keeps every
claim defensible while still swinging for the interesting result.

---

## 2. Venue strategy

Key correction over a naive "MSR is easiest" read: **NeurIPS Evaluations & Datasets
recurs annually** — the 2026 deadline passing does not remove it. NeurIPS 2027 E&D
(~May 2027) is the artifact-perfect target with ~11 months of runway to build strong
baselines.

| venue | expected deadline | format | fit | role |
|---|---|---|---|---|
| **NeurIPS 2027 Evaluations & Datasets** | ~May 2027 (annual cadence; 2027 dates unpublished) | 9 pg | Purpose-built D&B track; agent-eval audience are the consumers of this benchmark | **Primary** (needs C1) |
| **MSR 2027 Data & Tool Showcase** | ~Nov 2026 (predicted) | 4+1 pg | Canonical home for mined datasets; values C2/C3 | **Interim artifact paper / safe fallback** |
| ICLR 2027 | ~Sep–Oct 2026 | 9 pg | SWE-bench's *lineage* venue, but no D&B track → competes with method papers | Stretch, if C1 strong by Sept |
| COLM 2027 | ~Mar 2027 | 9 pg | LLM-focused; benchmarks in-scope | Alternative to NeurIPS |
| EMNLP 2026 (ARR Jul) | ~Jul 15, 2026 | 8 pg | Reachable but baselines cannot be ready in ~3 wks | **Drop** |

### Recommended: two-paper split (not a single bet)

- **MSR 2027 (~Nov 2026) = the *artifact* paper** — dataset + Docker-free harness +
  construction/validation methodology + tiers (C2+C3). Short, low-risk, peer-reviewed and
  citable *this year*.
- **NeurIPS 2027 E&D (~May 2027) = the *study* paper** — large-scale agent evaluation +
  cross-language gap + failure taxonomy (C1), citing the MSR artifact.

**Overlap risk is real.** The NeurIPS paper must *lead with the study* and treat the
dataset as prior-published infrastructure, or reviewers cry salami-slicing.

**Clean alternative (single venue):** NeurIPS 2027 E&D only, plus an arXiv preprint and
HuggingFace release now for early adoption (preprints/data releases are not prior
publication).

**Either way:** MSR's November deadline is the **forcing function** — build everything to
be MSR-submittable by Nov, then decide late whether to ship short (MSR) or hold/expand for
NeurIPS based on how the baselines land.

---

## 3. Go/no-go gate — does the benchmark *discriminate*?

The only baseline today is the 4-instance pilot where all three agents solved 12/12. For
a benchmark paper that is an existential red flag. **Before writing a single page:**

**Run 3–4 agents on the full Verified tier (~600 instances) and read the spread.**

- **Healthy (write the paper):** resolve rates in the SWE-bench-typical **~20–70%** band,
  with visible separation between models and between tiers (Verified < Lite < hard-split).
- **Too easy (fix mining first):** rates cluster >85% → over-cuing; tighten toward
  symptom-only issue statements, harder bugs, drop "implement named function" tasks.
- **Too hard / broken (fix harness):** rates near 0% or env-error-dominated → environment
  or flakiness problem, not difficulty.

This experiment decides whether you are writing a NeurIPS paper (C1 lands) or an MSR
artifact paper (C3 only). It is the highest-information action available — do it first.

---

## 4. Work plan (phases)

Starting position is strong: `.zenodo.json`, `CITATION.cff`, `LICENSE`, `NOTICES.md`
(license-verified, copyleft-refused), `dataset_card.md` already exist — most compliance
scaffolding is done.

| phase | work | gate |
|---|---|---|
| **P0 — Finalize data** (~1 wk) | Grind finishes (~331 left, ~1 day). Enrich commit→PR/issue (`enrich_statements.py`; ~1,456 enrichable). Merge → `data/instances.jsonl`. Run `split_tiers.py`, `gen_notices.py`, `build_hard_split.py`. Re-verify uniqueness / no-empty-F2P. | Final N per tier locked |
| **P1 — Discrimination gate** (~1–2 wk) | The §3 experiment: 3–4 agents × Verified. **Critical path.** | Spread healthy → proceed |
| **P2 — Full baseline grid** (~2–3 wk) | Scale to Lite + hard-split; add open models (Qwen-Coder, DeepSeek); record resolve / cost / iterations / env-fail per tier & per repo | Results tables |
| **P3 — Cross-language study** (~1–2 wk) | Match Julia-Verified to a characteristic-matched Python SWE-bench-Verified subset; same agents both sides; attribute the gap (C1) | Headline figure |
| **P4 — Release & archive** (~1 wk) | HuggingFace upload + **Croissant + Responsible-AI metadata** (NeurIPS *requires* both); Zenodo DOI; single `make reproduce`; artifact-eval packaging | Public, citable |
| **P5 — Writing** (~3 wk) | Draft → internal review → polish, per target format | Submittable draft |

P0/P4 overlap with P1–P3. Schedule is gated by **P1→P2→P3 (the baselines)** — the part
that does not exist yet.

---

## 5. Baseline experimental design

- **Models (≥4 for a credible spread):** 2 frontier (Claude Sonnet/Opus class,
  GPT/o-series), 1 small (Haiku class — cost axis), 1 open-weight
  (Qwen2.5-Coder / DeepSeek-Coder — reproducibility + training-data-scarcity probe).
- **Scaffold:** existing edit→`./check` loop, fixed iteration budget, src-only diffs
  enforced; score every final patch independently with `run-one` (F2P flips, P2P holds,
  no test tampering).
- **Metrics:** resolve rate (per tier, per repo), pass@k, mean iterations, token cost,
  env-failure rate, and **resolve-rate by `statement_source`** (commit vs issue) as a
  built-in leakage/contamination probe.
- **Cross-language control (C1):** select a Python SWE-bench-Verified subset matched to
  Julia-Verified on patch-size / #F2P / statement-length distribution; run the *same*
  agents+scaffold on both; report the matched-pair gap + multiple matching strategies.
  Without matching, "Julia is harder" is confounded by task difficulty.
- **Stat hygiene:** bootstrap CIs on resolve rates; report seeds; release all predictions
  + reports for the artifact badge.

---

## 6. Paper outline (NeurIPS 9-pg; MSR = compressed 4-pg subset)

1. **Introduction** — Julia ecosystem ≠ Python; agents may not transfer; contributions.
2. **Related work** — SWE-bench / Multilingual / SWE-rebench-V2 / JuliaBench /
   Julia-LLM-Leaderboard; position as execution-validated + Julia-native.
3. **Dataset construction** — discover → mine → validate → enrich → tier; pipeline figure;
   yield/rejection stats.
4. **Tiers** — Full / Lite / Verified criteria; distribution plots (patch size, F2P, repo
   diversity, statement source).
5. **Baseline study + cross-language gap** — the C1 core.
6. **Analysis** — difficulty distribution; leakage (commit vs issue); Julia-specific
   failure taxonomy.
7. **Limitations & ethics** — licensing (done); statement-source leakage; coverage; no
   human-verified subset; single Julia version.
8. **Conclusion.**

MSR version keeps Construction + Tiers + artifact; trims the study to a teaser.

---

## 7. Risk register

| risk | severity | mitigation |
|---|---|---|
| Benchmark too easy (no discrimination) | existential | §3 gate before writing; harder mining if needed |
| Cross-language gap confounded by difficulty | high | multiple matching + failure taxonomy as primary evidence |
| Contamination asymmetry (Python memorized) | high | address explicitly; reframe as low-contamination asset |
| "First" claim refuted by SWE-rebench-V2 | medium | verify qualifiers vs. their methodology before submission |
| Dual-submission / salami-slicing (MSR + NeurIPS) | medium | scope MSR = artifact, NeurIPS = study; lead NeurIPS with C1 |
| Single Julia version weakens C2 | low | scope claim honestly; per-instance version selection as future work |
| Result already-known | medium | center novelty on gap *decomposition*, not existence |

---

## 8. Immediate next actions

1. **Finish P0 now** — grind at ~1,563 valid / ~331 left; on completion run
   enrich → merge → `split_tiers.py` → `gen_notices.py` → `build_hard_split.py` → commit.
2. **Stand up P1, the discrimination gate** — run 3–4 agents on Verified and read the
   spread. This single experiment determines NeurIPS-paper (C1) vs. MSR-artifact (C3).
3. **Verify the "first" claim** — check SWE-rebench-V2's Julia subset methodology so the
   novelty qualifiers are exact.

---

## Appendix — current state snapshot

- **Validated pool:** ~1,563 valid (grind ~10,093/10,424 processed, ~331 remaining;
  projected final ~1,640–1,670 at ~16% yield).
- **Tiers (current pool, pre-enrichment):** Full ~1,608 / 158 repos; Lite ~1,308 / 149;
  Verified ~599 / 121. Verified grows after enrichment upgrades commit→PR/issue source.
- **Committed dataset:** 115 instances / 32 repos (`data/instances.jsonl`) — merge of the
  validated pool pending.
- **Scripts:** `discover_repos.py`, `mine_all.py`, `mine_repo.py`, `enrich_statements.py`,
  `split_tiers.py`, `build_hard_split.py`, `gen_notices.py`, `pull_repos.sh`,
  `validate_shards.sh`, `run_tierA.sh`; harness `swebench_eval.py` (parallel/resumable).
- **Archival scaffolding present:** `.zenodo.json`, `CITATION.cff`, `LICENSE`,
  `NOTICES.md`, `docs/dataset_card.md`, `docs/schema.md`,
  `docs/comparison_to_swebench.md`, `docs/baselines.md`.
