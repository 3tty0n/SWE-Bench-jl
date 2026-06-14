# SWE-bench-jl スケーリング計画 — 公式 SWE-bench 級の規模へ

現状 **45 件 / 5 リポジトリ** の SWE-bench-jl を、公式 SWE-bench 級（数百〜2,300 件）まで
拡大するための計画。調査（`collect/`・`harness/`・`data/`・`work/logs/` 実装読解）に基づく。

---

## 0. TL;DR（結論先出し）

- 公式 SWE-bench は **12 個の巨大 Python リポジトリ × 高密度**で 2,294 件。
  Julia は巨大リポジトリが少ないので、**多数の中小リポジトリ × 低密度**で積み上げる構造になる。
  → 「対象リポジトリの自動発見」が新たな要。
- 現状の律速は **検証フェーズ**（逐次実行・並列なし・単一 Julia 版・Docker なし・flaky 対策なし）。
  ここを直さない限り件数は伸びない。
- 新規に作る最重要コンポーネントは 2 つ:
  1. **環境再現エンジン（P3）** — per-instance の Julia 版選択 + Manifest 解決フォールバック。
     今は静かに `env_failed` で捨てている候補を救い、かつ「公式級」に必須の**再現可能性**を与える。
  2. **並列検証ランナー（P4）** — 件数を稼ぐための純粋なスループット問題。
- 到達目標は 3 層（Lite 級 300 → Verified 級 500 → Full 級 2,000+）に分け、
  **設計は Full 級を見据えつつ、まず Lite 級 300 を確実に**出す。

---

## 1. 目標の定義 — 「公式級」とは何件か

| ベンチマーク | 件数 | 由来リポジトリ数 | 備考 |
|---|---|---|---|
| SWE-bench (full/test) | 2,294 | 12 | django/sympy/sphinx など巨大 Python OSS |
| SWE-bench Verified | 500 | 12 | 人手で「自己完結・非 flaky・可解」を選別 |
| SWE-bench Lite | 300 | 11 | 軽量サブセット |
| SWE-bench Multimodal | 517 | — | JS 中心 |
| **SWE-bench-jl（現状）** | **45** | **5** | 実行検証済み・Julia 初 |

現状の密度: **45 / 5 = 約 9 件/リポジトリ**（実績レンジ 4〜19）。

### 推奨ターゲット（3 層）

| 層 | 目標件数 | 必要リポジトリ数（概算） | 位置づけ |
|---|---|---|---|
| **Tier A** | 300（Lite 級） | 約 28〜40 | 近期。現方式の延長で確実に到達可能 |
| **Tier B** | 500（Verified 級） | 約 50〜70 + キュレーション | 中期。品質層化と人手/LLM 選別を導入 |
| **Tier C** | 2,000〜2,300（Full 級） | 約 150〜325 | 野心目標。重い依存・並列基盤・Docker が必要 |

**方針**: P1〜P6 の設計は Tier C を前提に作り、リリースは M1(300)→M2(500)→M3(2,000+) の
マイルストーンで刻む。Tier A 到達時点で「SWE-bench Lite 相当の、Julia 初の実行検証ベンチ」
として既に公表価値がある。

---

## 2. 公式（Python）との本質的な違い

| | 公式 SWE-bench | SWE-bench-jl |
|---|---|---|
| リポジトリ戦略 | 少数(12)の巨大 OSS × 高密度 | 多数の中小 × 低密度（→ **リポジトリ発見の自動化が要**） |
| 1 リポジトリあたり PR 数 | 数千〜数万 | 数百〜千程度（大型でも） |
| 環境再現 | per-instance Docker イメージ + Python 版マッピング | 単一 Julia 版・Docker なし（**未整備**） |
| 検証並列度 | 分散・コンテナ並列 | 逐次・並列ゼロ |
| 言語固有の難所 | pip 依存解決 | Julia 版 × Manifest 解決、precompile、JLL/GPU 依存 |

結論: 「公式を Julia に翻訳」は不可能（インスタンスは *Python* リポジトリのバグで言語拘束）。
やるべきは **同等品質の SWE-bench-jl を成熟させる**こと。

---

## 3. 現状の律速・ギャップ（調査で確認した事実）

| # | ギャップ | 根拠（file:line） | スケール時の影響 |
|---|---|---|---|
| G1 | 検証が**逐次**・並列なし | `harness/swebench_eval.py`（Pool/async なし） | 1,000 件で 11h+、2,300 件で非現実的 |
| G2 | **単一 Julia 版**（1.12.6 固定、`JULIA_BIN`） | `swebench_eval.py:39,401`；`testenv.jl` | 古い commit が resolve せず `env_failed` で黙って脱落 |
| G3 | **Docker / ハーメティック環境なし**（global depot） | `swebench_eval.py:493-494` | 公開リーダーボード・第三者再現が不可 |
| G4 | **flaky 対策なし**（1 回実行のみ） | `run_tests.jl`・`testreport.jl` | 大規模で非決定テストが F2P/P2P を汚染 |
| G5 | マイニング偽陽性 `no_fail_to_pass` が棄却の主因 | `validate_summary.jsonl`（32/35） | 検証の 4 割が無駄打ち（テストが base で既に通る） |
| G6 | リポジトリ**手動指定**・`--max-candidates 50`・統合手作業 | `mine_repo.py:259,265` | 数百リポジトリのオーケストレーション不可 |
| G7 | 問題文の**漏洩**（73% が pr/commit 由来） | `data/instances.jsonl`（issue12/pr32/commit1） | 厳格評価には issue 由来が望ましい |

> 補足: 検証は「真のオラクル」なので G5 は正しさの問題ではなく**コスト**の問題。
> 2,300 件規模では無駄打ちの削減が効いてくる。

---

## 4. 拡張ファネル設計（45 → 2,300）

```
レジストリ全体 (General, 約 1万パッケージ)
  │  P1: 自動選別（pure-Julia / テスト有 / PR数 / star / 許諾ライセンス / GPU・バイナリ依存除外）
  ▼
候補リポジトリ  約 150〜325
  │  P2: 並列マイニング（max-candidates 撤廃 / gh トークン複数 / dedup / 偽陽性プレフィルタ）
  ▼
生候補  約 5,000〜10,000
  │  P3: 環境再現エンジン  +  P4: 並列検証（flaky 除去込み）
  ▼
検証済インスタンス  ≈ 目標件数（歩留り 約 40〜50%）
  │  P5: 品質層化（strict / issue-sourced / Verified 級キュレーション）
  ▼
公開データセット（all / verified / hard / lite ビュー）
```

### 歩留りとリポジトリ必要数の試算（概算・仮定込み）

リポジトリを「マイニング可能な PR 量」で 3 段に分類した想定:

| 段 | 例 | 検証済/repo（想定） | Tier C での内訳 |
|---|---|---|---|
| 大型 | Graphs, HTTP, JuMP, MathOptInterface, DataFrames, Distributions, ChainRules, Symbolics | 20〜60 | 15 repo × 平均35 ≈ **525** |
| 中型 | SpecialFunctions, Polynomials, StatsBase, Tables, CSV, OffsetArrays, FillArrays, Unitful, Roots, QuadGK | 6〜15 | 60 repo × 平均10 ≈ **600** |
| 小型 | 現状の 5 つを含む大多数 | 2〜9 | 250 repo × 平均5 ≈ **1,250** |
| | | | **合計 ≈ 2,375 / 約 325 repo** |

- 300 件（Tier A）: 大型3 + 中型15 + 小型10 ≈ **28 repo**、生候補 ~900、検証歩留り 45% → ~300。
- 500 件（Tier B）: 上記 + 中型・小型を追加 ≈ **50〜70 repo**。
- 検証歩留りは現状 56% だが、新規リポジトリでは下がる想定で **40〜50%** を採用。

---

## 5. フェーズ別実装計画（各フェーズはサブエージェントに委譲可能）

### P1 — リポジトリ宇宙の自動発見 〔新規〕
- **新規** `collect/discover_repos.py`:
  - `JuliaRegistries/General` の `Registry.toml` を parse → 全パッケージの GitHub URL（約 1 万）。
  - フィルタ: ① `test/runtests.jl` 有り ② star / closed-PR 数が閾値以上（保守性 & 採掘可能性の代理）
    ③ ライセンスが許諾系（MIT/BSD/Apache — patch/test_patch 再配布のため。`NOTICES.md` 方針）
    ④ 言語構成が概ね Julia（GitHub languages API で重い C/C++/CUDA を除外 → 環境構築の地雷回避）
    ⑤ deps に JLL/CUDA/AMDGPU 等のバイナリ・GPU 依存を含まない（Project.toml 解析）。
  - 出力: マイニング可能 PR 量でランク付けした `repos.tsv`（段・推定件数つき）。
- **成果物**: 候補リポジトリ表 + 段分類。Tier A は上位 ~30 行を採るだけで足りる。

### P2 — マイニングのスケール 〔既存拡張〕
- **新規** `collect/mine_all.py`（オーケストレータ）: `repos.tsv` を**並列**に `mine_repo.py` へ投入。
  - `--max-candidates` を撤廃/大幅引き上げ（履歴全走査）。
  - `gh` トークンを複数ローテーション + レート制御（現状 0.3s sleep のみ、`mine_repo.py:215,226`）。
  - 横断 dedup（同一 fix がフォーク/再掲で重複）、`candidates/<repo>.jsonl` に集約。
- **G5 対策（偽陽性プレフィルタ）**: test_patch の新規 `@test` 行が、fix patch の触る
  シンボル名と少なくとも 1 つ重なるかを静的判定。重ならない候補は検証前に間引く
  （検証はオラクルなので最終判定は変えず、無駄打ちだけ削減）。
- **成果物**: 数千件規模の生候補 + リポジトリ別歩留りログ。

### P3 — 環境再現エンジン 〔新規・最重要・最大リスク〕
今の `testenv.jl` は「単一 Julia + base の Manifest を instantiate」だけ。これを多段化:
- **per-instance Julia 版選択**: `juliaup` で複数版を常備し、
  ① Project.toml の `[compat] julia` ② `created_at`（fix commit 日付）から当時の Julia 版へ写像。
  → G2（古い commit が現行 Julia で resolve しない）を救済。
- **Manifest 解決フォールバック階段**（成功した経路を記録）:
  1. commit 同梱の Manifest があればそれを使う（厳密ピン）
  2. なければ compat 宣言下で `Pkg.resolve`
  3. それも失敗なら `[compat]` を緩めて resolve
  4. 最後に最新 Julia で試行
  → `env_failed` の多くを検証済へ転換し、歩留りを底上げ。
- **再現アーティファクト**: 解決済み Manifest と Julia 版をインスタンスに保存（第三者が再現可能に）。
- **（任意・Tier C 必須）Docker/Apptainer**: 「repo × 時代」単位でイメージを焼き、公開リーダーボードで
  ハーメティックに再現可能にする（per-instance では多すぎるので粒度を粗く）。→ G3 解消。
- **成果物**: 拡張 `testenv.jl` + 版マッピング表 + per-instance 環境メタ。

### P4 — 並列検証ランナー 〔既存拡張・律速解消〕
- `_validate_one` をプロセスプールで**インスタンス並列**化（multiprocessing / `xargs -P` / CI マトリクス / 分散）。
  - depot は共有（読み主体）、env はワーカーごとに分離して Manifest 競合を回避。
  - 各インスタンス後に worktree/env を GC（report JSON と解決済み Manifest のみ残す）。
  - `validate_summary.jsonl` への追記で**冪等・再開可能**（完了済みはスキップ）。
- **G4 対策（flaky 除去）**: 各テストレポートを K=3 回実行し、全回一致しないノードを flaky として
  F2P/P2P から除外。F2P が空になったらそのインスタンスを落とす。
- **スループット目標（概算）**: 1 件 ≈ pre+post 120s、flaky K=3 で ≈ 360s。
  16 並列なら実時間 ≈ 22.5s/件 → 1,000 件 ≈ 6.3h、2,300 件 ≈ 14h（1 台 16 コア）。
  CI マトリクスで横にシャードすれば数時間。ディスクは 500GB+ 見込み、GC で圧縮。
- **成果物**: 並列・再開可能な検証ランナー + スループット計測。

### P5 — 品質層化と Verified 級キュレーション 〔新規〕
- **strict サブセット**: 既存の hard 定義（issue 由来 ∧ 非 feature ∧ 既存コード改変）を踏襲し自動抽出。
- **LLM 審査**（OpenAI SWE-bench Verified プロトコルのミニ版）:
  問題文の自己完結性 / テストの妥当性（解を過小・過大指定していないか）/ 可解性 を LLM で採点。
- **人手最終確認**: 上位候補のみ人が確認して `verified` ビューを確定。
- **メタデータ付与**: 難易度、漏洩ラベル（issue/pr/commit）、flaky スコア。
- **成果物**: `all / verified / hard / lite` の 4 ビュー（canonical は `instances.jsonl`、他は派生）。

### P6 — ガバナンス / リリース 〔継続〕
- **スキーマ拡張**: per-instance の `julia_version` / 環境イメージ hash / `flaky_score` / `leak_label`。
- **汚染カットオフ**: 評価対象モデルの知識カットオフ以降の issue/PR を別 split に隔離。
- **バージョニング**: v0.2(300) → v0.3(500, +環境エンジン) → v1.0(2,000+)。データセットカード更新。
- **配布**: `bin/swebenchjl` の export-official、リーダーボード雛形、Zenodo メタ更新。

---

## 6. スケジュール感（粗い工数見積り）

| フェーズ | 目安 | 難度 |
|---|---|---|
| P1 リポジトリ発見 | 1〜2 週 | 低 |
| P2 並列マイニング | 1〜2 週 | 低〜中 |
| P3 環境再現エンジン | 2〜3 週 | **高（最難）** |
| P4 並列検証 | 1〜2 週 | 中 |
| P5 品質層化 + 人手 | 2〜4 週 | 中（人手込み） |
| P6 ガバナンス | 継続 | 低 |

クリティカルパスは **P3 → P4**（P1/P2 と並行可）。Tier A(300) だけなら P1+P2+P4(最小並列) で到達でき、
P3 は「歩留り改善・再現可能性」のため Tier B 以降で本格投入、という刻み方も可能。

---

## 7. リスクと代替案

| リスク | 対応 / フォールバック |
|---|---|
| Julia エコシステムに、厳格・実行検証可能なバグ修正が 2,300 件**存在しない**可能性 | Tier A/B を確実化。300 件規模でも「Julia 初の実行検証ベンチ（Multilingual SWE-bench 級）」として新規性は十分 |
| バイナリ/GPU 依存で検証不能なリポジトリ | Docker 層で吸収、または対象から除外（P1 のフィルタ） |
| 古い commit が現行 Julia で resolve 不能 | P3 の版マッピング + 緩和 resolve で救済。だめなら対象期間を絞る |
| flaky / データ汚染 | P4 の K 回実行除去 + P6 のカットオフ split |
| マイニング偽陽性で検証コスト膨張 | P2 の静的プレフィルタ + P4 の並列化で吸収 |

---

## 8. マイルストーン

- **M1 — v0.2 / Tier A (300, Lite 級)**: P1 + P2 + P4(最小並列)。「SWE-bench Lite 相当」として公表可能。
- **M2 — v0.3 / Tier B (500, Verified 級)**: P3 環境エンジン + P4 flaky 除去 + P5 キュレーション着手。
- **M3 — v1.0 / Tier C (2,000+, Full 級)**: Docker 層 + 大型リポジトリ取り込み + リーダーボード。

---

## 9. 実装状況 — Layer A（M1）着手分

M1（Tier A = P1 + P2 + P4）の各コンポーネントを実装済み。運用手順は
[`tierA_runbook.md`](tierA_runbook.md)、一括実行は [`../collect/run_tierA.sh`](../collect/run_tierA.sh)。

| フェーズ | 成果物 | 状態 | 備考 |
|---|---|---|---|
| P1 | `collect/discover_repos.py` | ✅ | General レジストリ約 1.4 万件をオフライン選別→上位を `gh` で補強→`repos.tsv`（段・推定件数つき）。自己テスト + 実レジストリで確認。 |
| P2 | `collect/mine_all.py` | ✅ | `mine_repo.py` を並列駆動・上限撤廃・横断 dedup・G5 静的プレフィルタ（test/fix シンボル重なり）・再開可能。 |
| P4 | `harness/swebench_eval.py validate` 拡張 | ✅ | `--jobs`（プロセスプール + repo 単位 flock）・`--resume`（summary でスキップ + `validated.jsonl` から `out` 再構成）・`--flaky-runs K`（G4: K 回実行で不安定ノード除外、K=1 は従来と一致）・`--gc-env`（env 破棄・解決済み Manifest 保存）。 |

検証: 既存インスタンスの再検証で F2P/P2P が canonical と完全一致、`--resume`/`--gc-env`/並列
（同一 repo に 2 ワーカー）を確認。新規 repo（Primes.jl）でマイニング→検証の通し動作も確認。

**環境上の注意**: 並列検証では `JULIA_BIN` を juliaup ランチャでなく実バイナリに向ける
（ランチャはグローバルロックを取り並列ワーカーを直列化する）。`--jobs` はコア数に合わせる。

### 未着手（Tier B/C へ繰り越し）
- P3 環境再現エンジン（per-instance Julia 版選択 + Manifest 解決フォールバック、Docker）。
  本環境は Julia 1.12.6 単一・1 コアのため Layer A では版固定・単一解決パス。
- P5 品質層化の LLM/人手審査、P6 ガバナンス拡張。

---

## 付録: 現状データの内訳（基準値）

| リポジトリ | 候補 | 検証済 | 歩留り |
|---|---|---|---|
| JuliaCollections/DataStructures.jl | 25 | 19 | 76% |
| JuliaCollections/OrderedCollections.jl | 13 | 9 | 69% |
| JuliaMath/Combinatorics.jl | 12 | 8 | 67% |
| JuliaCollections/IterTools.jl | 7 | 5 | 71% |
| JuliaIO/JSON.jl | 23 | 4 | 17% |
| **合計** | **80** | **45** | **56%** |

棄却内訳（`work/logs/validate_summary.jsonl`）: `no_fail_to_pass` 32 / `timeout_pre` 2 / `broken_at_base` 1。
問題文由来: issue 12 / pr 32 / commit 1。hard サブセット 9 件。
