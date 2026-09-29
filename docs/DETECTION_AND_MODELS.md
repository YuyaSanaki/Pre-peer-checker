# 検知観点カタログ & エンジン／モデル選定

`check_reference/` の参照資料と、研究公正関連文献・人手キュレーションから抽出した検知観点、およびエンジン／モデル選定。

**製品方針**: 本システムは配布し、広く生命科学論文の **研究公正上・PubPeer 上**で問題視されやすい不整合を投稿前に防ぐ。過去事例・実データは **benchmark**。実装の正本は汎用パターン `fixtures/patterns/pubpeer_patterns.json` とし、ケース固有期待は `fixtures/gold/<case>/` に置く。エンジンを単一データセットに過適合させない。

**絶対条件**: **読む＝LLM/VLM、比べる＝機械**（[REQUIREMENTS.md](REQUIREMENTS.md) §1.2.1）。

**ネタ帳の位置づけ**: PubPeer／文献／公的ケースは **有罪教師ではなく Warning（照合方法）の発見源**。決定論 `pattern_id` を拡充する。**PubPeer サイトの自動スクレイプは禁止**。Retraction Watch（Crossref 公式 Git／API）等の **明示的に再配布・利用が許されたオープンデータ**は可。

### カタログ倫理の絶対ルール（崩さない）

PubPeer コメント・手元 PDF・個人経験（例: 自論文への指摘）をネタ帳に使うとき、次の **3 点は例外なく守る**。正本は本節と `fixtures/patterns/pubpeer_patterns.json` の `catalog_policy.absolute_rules`。

1. **表現を取らない** — コメント原文・長い引用・PDF／図の再配布（git・Issue・PR・配布物への投入）をしない。手元 PDF は `cache/` 等 git 外のみ。
2. **アイデア／検査仕様に抽象化する** — カタログに入れるのは機械照合可能な `abstract_rule` / `pattern_id` / Warning タグのみ。有罪断定・感情表現・告発文は入れない。
3. **コメント者を名指し・攻撃しない** — カタログは方法論。投稿者・ハンドル・個人への帰責や非難をルール文に書かない。

自論文への指摘を経験知の入口にするのは可。その場合も上記 1–3 を満たした抽象化だけを正本に載せる。

> 手元の説明動画・説明 PDF は **git 外**（`check_reference/`）。カタログ正本には載せない。個別ケース対応表はローカル `handoff.md`（gitignore）のみ。

---

## 1. 参照ソース

### 1.1 Benchmark・手元参照（抽象シグナルのみ）

手元 PDF・個別論文名は **git 外**。カタログへ落とすのは **抽象ルールと `pattern_id` のみ**（§「カタログ倫理の絶対ルール」）。個別ケース名の対応表は **`handoff.md`（非公開）**。

| 抽象シグナル（公開ドキュメントに載せる粒度） | 寄せる `pattern_id` |
|------|---------------------|
| 別条件パネルなのに点列／棒＋誤差が同一；ファイル名と DF 中身の不一致 | `P-DATA-SWAP-*`, `P-FILENAME-CONTENT-MISMATCH` |
| Legend n と生データ行数の不一致；同一プロット間の n 表記ずれ | `P-N-MISMATCH-*`, `P-N-INCONSISTENT-*` |
| 出典なしの画像パネル／コーパス再利用 | `P-IMAGE-REUSE-UNCITED` |
| 共有コントロール未開示・端点欠落 subset | `P-SHARED-CONTROL-UNDISCLOSED`, `P-VECTOR-SUBSET-UNDISCLOSED` |
| ソース表の同一値過出現；整数倍・定数比・小数部指紋 | `P-SOURCE-DUPLICATE-VALUES`, `P-SOURCE-RATIO-ARTIFACT` |
| 比・正規化由来の値が有効桁を超える桁数（1/3→0.3333 等） | `P-DERIVED-VALUE-PRECISION` |
| 生存率×固定 N が非整数個体数 | `P-SURVIVAL-COUNT-NONINTEGER` |
| Methods 主張 ↔ 図・表メタの矛盾 | `P-METHODS-CLAIM-MISMATCH` |
| 記載 n より多いソース行で除外基準なし；統計再計算不一致 | `P-EXCLUSION-UNDECLARED`, `P-STATS-RECALC-MISMATCH` |
| 試薬／抗体特異性のみ（原稿＋データから機械照合不可） | カタログ対象外（メモのみ） |

手元参照の件数目安: PubPeer 由来 PDF 十数件＋説明資料（いずれも git 外）。

### 1.2 収集源ポリシー（ネタ帳）

PubPeer と同等以上の網羅性を、**規約に抵触しない公式・オープン資源**から得る。

| 優先 | ソース | 取り方 | git に入れるもの |
|------|--------|--------|------------------|
| **P0** | **Retraction Watch Database**（[Crossref GitLab](https://gitlab.com/crossref/retraction-watch-data) / API） | 公式 CSV／Git の正規取得。撤回理由タグを類型化 | 理由タグ→`pattern_id` 対応表・集計メモ。論文図・全文は入れない |
| **P0** | **ORI Case Summaries**（米国研究公正局の公開事例） | 公開 HTML／PDF を人手または許諾範囲の構造化。判定文から「何が不一致か」だけ抽象化 | `abstract_rule` のみ |
| **P0** | **COPE Case Database**（出版倫理・編集部ケース） | 検索可能な公開ケースを人手／公開 API があれば正規利用 | 類型コード＋抽象ルール |
| **P0** | 研究公正・画像／統計の **学術文献**（Bik 2016 等） | 正規の文献入手。要約は人手 | DOI + 1行類型 |
| **P0** | `check_reference/`・benchmark | 手元のみ | 既カバーとの対応 |
| **P1** | PubPeer **人手閲覧**（代表スレッド） | ブラウザで問題の種類だけメモ | 抽象ルールのみ。コメント全文・図は入れない |
| **P2** | Retraction Watch **記事サイト**・学会声明（ページ単位の人手） | 人手。サイト全体のスクレイプはしない | ギャップ発見の補助 |
| **不可** | **PubPeer の自動クロール／一括スクレイプ** | — | ToS 違反。製品・開発パイプラインに入れない |

採用基準: **投稿前に機械照合できるシグナル**があること。「意図」「捏造」「有罪」断定は入れない。ORI／COPE の叙述は **照合手続きのヒント**に落とし、学習ラベルにしない。

### 1.3 文献・公式シード（初版・類型メモ）

人手で読む／対応付けする種。全文・ケース HTML はリポジトリに置かない。

| 参照 | 類型メモ | 寄せる `pattern_id` / 観点 |
|------|----------|---------------------------|
| [10.1128/mBio.00809-16](https://doi.org/10.1128/mBio.00809-16) (Bik et al.) | 画像重複 Cat I/II/III（単純／変形・反転・トリミング／部分パッチ） | `P-IMAGE-REUSE-UNCITED`, `P-IMAGE-PARTIAL-REUSE`, `P-BLOT-LANE-REUSE` |
| [10.1083/jcb.200406019](https://doi.org/10.1083/jcb.200406019) | 画像操作の誘惑（クロップ・コントラスト・レーン） | `P-IMAGE-PARTIAL-REUSE`, `P-BLOT-LANE-REUSE` |
| [10.1083/jcb.200611141](https://doi.org/10.1083/jcb.200611141) | 誤差棒 SEM vs SD | `P-ERRORBAR-SEM-SD-MISMATCH` |
| [10.1038/489007a](https://doi.org/10.1038/489007a) | 有意性・サンプルサイズ解釈 | `P-N-MISMATCH-*`, `P-STATS-RECALC-MISMATCH` |
| [10.1371/journal.pbio.3000410](https://doi.org/10.1371/journal.pbio.3000410) | ARRIVE 2.0 — n・群・統計記載 | `P-N-MISMATCH-*`, `P-STAT-MULTIPLICITY-GAP` |
| [10.3758/s13428-015-0664-2](https://doi.org/10.3758/s13428-015-0664-2) | 本文↔統計値転記（statcheck 系） | `P-NUMERIC-CROSSREF-MISMATCH` |
| Retraction Watch / Crossref reasons | Duplication of Image, Falsification/Fabrication, Error in Analyses/Methods 等 | §2.0 体系の A–D にマップ |
| ORI Case Summaries | 画像改ざん・データ不一致の調査記述 | 決定論シグナル候補の抽出のみ |
| COPE Case Database | 編集部ケース（共有 Ctrl、n、検定誤用等） | 類型コード→`pattern_id` |

### 1.4 カタログ拡充パイプライン（運用・実装済）

静的チェックリストで終わらせず、**公式・オープンデータ**と **人手保存の PubPeer PDF** で類型を継続更新する。

```
[収集]
  ├── ① Retraction Watch (Crossref Git) ──► 日次 git pull ──► Reason タグ集計・生命科学フィルタ
  ├── ② ORI Case Summaries（人手キュレーション種）──► 抽象ルールのみ
  ├── ③ COPE Case Database（人手キュレーション種）──► 類型コード→pattern_id
  ├── ④ Bik et al. 等の文献シード ──────────► 画像 Cat I–III 定義
  └── ⑤ PubPeer PDF（ブラウザで人手保存 → WebUI D&D）──► cache/ のみ・抽象ルール抽出
                         │
                         ▼
              propose / キュレーションキュー（自動 merge なし）
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  cache/catalog_active/  fixtures/…json   GitHub Issue / PR
  （照合時に優先）        （人手反映時）      （抽象ルールのみ共有）
                         │
                         ▼  export
         rules/verification_catalog.yaml         ← 人間可読ビュー（ERR_* エイリアス）
```

| やってよい | やらない |
|------------|----------|
| RW Crossref Git の `git pull`／公式 CSV | PubPeer HTML のロボット取得 |
| 人手保存 PDF の D&D 取込（`cache/` のみ） | PDF・コメント全文・図の git / Issue / PR 投入 |
| 理由タグの頻度集計→ギャップ優先度の提案 | 撤回論文図の git 投入・有罪スコア学習 |
| ルール正本は **`pubpeer_patterns.json`**。YAML は export／提案用 | YAML と JSON を別々に手編集して二重管理 |
| 各 `pattern_id` に合成 fixture で回帰 | 「不正っぽさ」主観ラベル |
| ORI／COPE は公開摘要から **抽象ルールのみ**人手で種を追加 | ケース HTML 全文の自動一括スクレイプ |
| Issue＝抽象案の **Web Intent**（`issues/new`）、PR＝**CLI コピー**（PAT 不要） | カタログの自動 merge・WebUI へのトークン入力 |
| （再掲）カタログ倫理の絶対ルール 1–3 | 原文・長い引用・PDF 再配布／有罪断定／コメント者の名指し・攻撃 |

**CLI**: `python -m pre_peer_checker.catalog --help`（`pulse` / `pull-rw` / `mine` / `propose` など）  
**WebUI**: 「カタログ更新」タブ — PubPeer PDF D&D → Qwen 仕分け（固有情報除去プロンプト）→ 人手キュレーション → Apply → **Issue Web Intent** / **PR 用 CLI コピー**（標準 JSON `schema 1.1`）  

**アクティブ同期**: 照合・ステータス表示・pull／仕分け／Apply／PubPeer PDF 取込のたびに fixtures 正本を `cache/catalog_active/` へマージする（実装済昇格・新規 ID）。人手の明示 disable は保持。実装済 ID を `accept_planned` で planned+無効に戻す操作はしない（PDF Apply による抑制を防ぐ）。**日次パルス**: `.github/workflows/catalog-pulse.yml`（提案アーティファクトのみ。カタログ自動 merge なし）

共有 JSON の標準形（Issue/PR 添付）:

```json
{
  "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
  "category": "Warning [データ取り違え]",
  "title": "異なる実験条件間でのプロット数値・ベクター点列の一致",
  "abstract_rule": "別条件パネルに同一データが描画されていないかを検査する。",
  "target_inputs": ["r_script", "excel", "fig_pdf"],
  "detection_logic": { "tier1": "…", "tier2": "…" },
  "sources": ["PubPeer Community Contribution (Abstracted)"]
}
```

マッピング表: `fixtures/catalog/rw_reason_map.json`、ORI/COPE 種: `fixtures/catalog/ori_seed_rules.json` / `cope_seed_rules.json`。

---

## 2. 検知観点カタログ（優先度付き）

### 2.0 実装状態の見方

| 状態 | 意味 |
|------|------|
| **実装済** | 決定論エンジンあり（精度は紐付け・照合の強化で継続改善） |
| **planned** | ネタ帳で定義。エンジンは後続 |

`pattern_id` 正本: `fixtures/patterns/pubpeer_patterns.json`（`status` / `taxonomy` フィールド）。別ファイルの二重 YAML は作らない。

### 2.0.1 Verification Taxonomy（A–F ↔ `pattern_id`）

査読・PubPeer・撤回理由で頻出の指摘を、**決定論で照合可能な**大分類に整理した対応表。判定は LLM 有罪分類ではなく、表・DAG・画像類似・再計算などの特徴量。

| 大分類 | 中分類 | 決定論シグナル（要約） | `pattern_id` | 状態 |
|--------|--------|------------------------|--------------|------|
| **A. 画像** | A1 単純同一重複 | 埋め込み／ハッシュ高類似（例: コサイン ≥ 閾値） | `P-IMAGE-REUSE-UNCITED` | 実装済 |
| | A2 クロップ・回転・スケール | 部分マッチ／LightGlue（Bik Cat II） | `P-IMAGE-PARTIAL-REUSE` | 実装済（回転+連続スケール NCC） |
| | A3 ゲル／ブロット帯・パッチ | レーン帯局所一致（Bik Cat III 寄り） | `P-BLOT-LANE-REUSE` | 実装済（初期レーン NCC） |
| | A4 例示・過去論文流用＋出典なし | コーパス一致 × citation 無し | `P-IMAGE-REUSE-UNCITED` | 実装済 |
| **B. データ取り違え** | B1 スクリプト／DF 参照ミス | DAG・群ベクトル同一 | `P-DATA-SWAP-CROSS-CONDITION`, `P-FILENAME-CONTENT-MISMATCH` | 実装済 |
| | B2 共有 Ctrl 無断 | 点列一致／subset × 非明示 | `P-SHARED-CONTROL-UNDISCLOSED`, `P-VECTOR-SUBSET-UNDISCLOSED` | 実装済 |
| **C. サンプルサイズ** | C1 Legend n vs 生データ | 行数突合（可視ドットは不可） | `P-N-MISMATCH-LEGEND-VS-DATA` | 実装済 |
| | C2 本文・Table・Fig 間の n | クロスソース n | `P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS`, `P-COUNT-N-MISMATCH`, `P-NUMERIC-CROSSREF-MISMATCH` | 実装済 |
| | C3 除外基準なき n 間引き | 生 n ＞ 記載 n × 除外無し；除外あり時は ID set equality | `P-EXCLUSION-UNDECLARED` | 実装済（set equality） |
| **D. 統計** | D1 多重比較欠落 | 群数 × 検定名 | `P-STAT-MULTIPLICITY-GAP`, `P-STAT-METHOD-INCONSISTENT` | 実装済 / 実装済（Hint） |
| | D2 対応／非対応の誤り | データ構造 × 検定 | `P-STAT-METHOD-INCONSISTENT` | 実装済（細分化は後続可） |
| | D3 p／統計量の再計算不一致 | scipy 再計算 vs 記載 | `P-STATS-RECALC-MISMATCH`, `P-NUMERIC-CROSSREF-MISMATCH` | 実装済 |
| | （補助）SEM/SD | 誤差棒種別 vs 再計算 | `P-ERRORBAR-SEM-SD-MISMATCH` | 実装済（初期） |
| **E. 表記・参照** | E1 パネル参照食い違い | 本文 Fig ID vs PDF ラベル | `P-REF-LABEL-MISMATCH` | 実装済 |
| | E2 遺伝型・条件の表記揺れ | 本文／Fig／YAML／表 | `P-CONFIG-ANNOTATION-MISMATCH`, `P-REF-LABEL-MISMATCH` | 実装済 |
| | E3 Methods／本文主張と図・数値の矛盾 | 主張スパーン × 表／図メタ | `P-METHODS-CLAIM-MISMATCH` | 実装済（初期 ratio） |
| | E4 参考文献メタ整合 | 本文 cite ↔ References；ユーザー提供 PDF メタ | `P-REF-MISSING-ENTRY`, `P-REF-DUPLICATE-KEY`, `P-REF-META-INCONSISTENT`, `P-REF-PDF-META-MISMATCH`（`P-REF-ORPHAN-ENTRY` は情報寄り・既定 OFF） | 実装済（初期） |
| | E5 引用主張↔論文内容 | 引用文 × ユーザー提供 PDF 根拠チャンク | `P-REF-CLAIM-CONTRADICTION`（根拠レビューは情報カード） | 実装済（初期） |
| **F. ソースデータ指紋** | F1 独立標本の完全同一値 | 表内・群間の厳密一致頻度 | `P-SOURCE-DUPLICATE-VALUES` | 実装済（初期） |
| | F2 整数倍・定数比・小数部指紋 | 比が正確に k∈ℤ または小数部が群横断で一致 | `P-SOURCE-RATIO-ARTIFACT` | 実装済（初期） |
| | F3 生存／カウントの非整数化 | Methods の固定 n × 生存率 → 非整数 | `P-SURVIVAL-COUNT-NONINTEGER` | 実装済（初期） |

Bik et al. Category I≈A1、II≈A2、III≈A3 に対応づける。F 系は **source data / 埋め込み表**があるときだけ決定論で発火（図の見た目だけでは不十分）。

### 2.1 実装済 — P0（本事例の直接原因）

#### D1. `Warning [データ取り違え]` — スクリプト参照ミス — **実装済**

- **事例**: Fig.1C（条件 A）用データセットを Fig.1H（条件 B）の `ggplot(data=...)` が参照。統計は正しい DF で計算済みでも作図だけ別 DF。
- **副次シグナル**: 出力 PDF 名と参照 DF／実験系ラベルが食い違う；異なる条件ラベルなのに jitter 座標が点一致。
- **照合元**: `.R` / `.Rproj` 履歴・環境変数、`.xlsx`、Figure PDF、Word Legend（実験系名）。
- **エンジン**: **決定論的** — R/Python AST→DAG（読込→変数→ggplot/seaborn→ggsave）。DF 内容ハッシュ／ソート済み数値ベクトルの完全一致をパネル間で検査。
- **pattern_id**: `P-DATA-SWAP-CROSS-CONDITION`, `P-FILENAME-CONTENT-MISMATCH`



#### D2. `Warning [サンプルサイズ記載誤記]` — **実装済**

- **事例**: Legend `n=9` だが実データ `n=8`；同一プロットなのに Legend だけ `n=9` vs `n=14`。
- **注意**: `geom_point` の重なりで見える点数 ≠ n（偽陽性になりやすい）。**見える点数は補助情報のみ**。正は生データ行数／スクリプト有効 n。
- **エンジン**: 表行数・群別 n の再集計（比べる）＋ LLM/VLM が Fig チャンクから抽出した n JSON（読む）。規則パーサはフォールバック。
- **pattern_id**: `P-N-MISMATCH-LEGEND-VS-DATA`, `P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS`



#### D3. `Warning [画像重複・再利用（要出典確認）]` — **実装済**（部分一致・ブロット帯は planned）

- **事例**: 先行論文図と同一パネルが、出典記載なしで再掲されている。意図的なスクリーニング分類例示でも **出典未記載なら Warning**。
- **エンジン**: DINOv2 埋め込みコサイン類似度 ＋ LightGlue。Word Legend に citation／「reproduced from」有無を LLM で確認。
- **コーパス入力**: CLI `--corpus`（画像フォルダ）／GUI フォルダ指定／**WebUI 照合タブ**で過去論文 PDF を取込（図抽出 → `cache/past_papers/`、ローカル再利用のみ・共有なし）。
- **pattern_id**: `P-IMAGE-REUSE-UNCITED`



### 2.2 実装済 — P1（コントロール設計の透明性・統計突合）



#### D4. `Warning [コントロール群共有]` — **実装済**（subset 明示は planned で強化）

- **事例**: WT／fat−/− 等が複数 Fig で完全一致；「独立実験」と読める記述なのに同一点列；共有時に最高／最低点だけ欠落した部分集合。
- **エンジン**: パネル間の数値多重集合一致・包含（subset）検出。Legend／本文に “shared control” 明示が無ければ Warning。
- **pattern_id**: `P-SHARED-CONTROL-UNDISCLOSED`



#### D5. `Warning [実験データとの不一致]` — **実装済**

- 生データ再計算の mean/SD/SEM/p と Word・Legend 記載の突合。
- **エンジン**: scipy/statsmodels 再計算（比べる）＋ LLM による数値／条件抽出（読む）。
- **pattern_id**: `P-STATS-RECALC-MISMATCH`



### 2.3 実装済 — P2（間接的だが要件どおり）


| ID  | Warning           | 本参照での現れ方                                   | pattern_id |
| --- | ----------------- | ------------------------------------------ | ---------- |
| D6  | `[統計手法の不整合]`      | 多群切替・単位変更時の再解析文脈。YAML/スクリプト検定名と Legend の突合 | `P-STAT-METHOD-INCONSISTENT` |
| D7  | `[設定・アノテーション不整合]` | 群ラベル（条件 A vs 条件 B、genotype）と YAML/DF 列の不一致 | `P-CONFIG-ANNOTATION-MISMATCH` |
| D8  | `[表記揺れ・参照不整合]`    | 本文 Fig.1C/H 記述とパネルラベル・条件名                  | `P-REF-LABEL-MISMATCH` |


### 2.4 ネタ帳ギャップ（決定論エンジン — 初期実装済 / 精緻化は後続）

各項目: **読む**＝抽出キー、**比べる**＝突合手続き、**not_sufficient**＝これだけでは Warning にしない。

#### G1. 画像部分一致・変形再利用 — P0 — `P-IMAGE-PARTIAL-REUSE` — **実装済（初期）**

- **典型**: 回転・クロップ・コントラスト変更後の同一バンド／セル領域。
- **読む**: 出典フラグ、パネル地図。
- **比べる**: コーパス照合の NCC 包含（`imaging/partial_match.py` + `corpus_scan`）。回転 4 通り × スケール 0.55–1.0 の各テンプレートを、FFT 相互相関＋積分画像で**全オフセット（stride 1）厳密評価**する（float64 総当たりとの差 < 1e-12）。高スコアのみ LightGlue で再検証。
- **粗選別を入れない理由**: pHash 等の全体ハッシュで候補を先に絞ると、切り抜き・回転・スケール変更後の再利用（Bik Cat II）がハッシュ上は別画像になり取りこぼす。高速化は計算の厳密な置き換えのみで行う。
- **not_sufficient**: 同一実験系の意図的再掲で出典あり。
- **合成 fixture**: `fixtures/synthetic/image_partial` + gold `image_partial`（G1）。
- **sources**: literature (Bik; Rossner), curated_pubpeer

#### G2. ゲル／ブロット帯レーン流用 — P0 — `P-BLOT-LANE-REUSE` — **実装済（初期）**

- **典型**: loading control やレーンが別実験パネルと同一。
- **読む**: ブロット／ゲルである旨、レーン注釈（あれば）。
- **比べる**: 縦帯レーン NCC（`imaging/blot_lane.py`）。全体画像同一は IMAGE-REUSE 側。
- **not_sufficient**: 同一ゲルの正当な再表示＋明示。
- **合成 fixture**: `fixtures/synthetic/blot_lane` + gold `blot_lane`（G2）。
- **sources**: literature (Rossner; image integrity)

#### G3. 点列 subset／端点操作 — P0 — `P-VECTOR-SUBSET-UNDISCLOSED` — **実装済（初期）**

- **典型**: 共有コントロールの上位互換（端点欠落・外れ値削除）を独立と読める記述。
- **読む**: “shared control”／独立実験の主張（`detect_independence_claims`）。
- **比べる**: 多重集合包含・端点欠落（`shared_control.warnings_from_shared_controls` が独立主張時に本 ID）。
- **not_sufficient**: 本文が共有を明示。
- **合成 fixture**: `fixtures/synthetic/vector_subset` + gold `vector_subset`（G3）。
- **sources**: curated_pubpeer, check_reference

#### G4. SEM/SD・誤差棒不一致 — P1 — `P-ERRORBAR-SEM-SD-MISMATCH` — **実装済（初期）**

- **典型**: Legend が SEM なのに再計算が SD（またはその逆）、表記と数値が矛盾。
- **読む**: `error_bar_type`（sem/sd/ci/不明）＋ mean±err スパーン。
- **比べる**: 記載 ± と生データ SD/SEM（`engine/errorbar_sem_sd.py`）。
- **not_sufficient**: type 未抽出のみ。
- **合成 fixture**: `fixtures/synthetic/errorbar_sem_sd` + gold `errorbar_sem_sd`（G4）。
- **sources**: literature (Cumming 2007)

#### G5. 多重比較ギャップ — P1 — `P-STAT-MULTIPLICITY-GAP` — **実装済（初期）**

- **典型**: 3 群以上で無補正の対比較 t など、群構造と検定が矛盾。
- **読む**: 検定名、比較ペア。
- **比べる**: 群数≥3 × Student/unpaired t かつ補正語なし（`engine/multiplicity.py`）。
- **not_sufficient**: 群数不明。
- **合成 fixture**: `fixtures/synthetic/multiplicity` + gold `multiplicity`（G5）。
- **sources**: literature (ARRIVE; reporting standards)

#### G6. 本文↔表／Fig 数値転記 — P0 — `P-NUMERIC-CROSSREF-MISMATCH` — **実装済（初期）**

- **典型**: Results の mean/p が表または Fig と不一致。
- **読む**: 本文・Legend・表から数値スパーン。
- **比べる**: 記載 mean ↔ 表グループ平均（`engine/numeric_crossref.py`）。丸め差は抑止。
- **not_sufficient**: 丸め差のみ。
- **合成 fixture**: `fixtures/synthetic/numeric_crossref` + gold `numeric_crossref`（G6）。
- **sources**: literature (statcheck 系)

#### G7. スケール／倍率矛盾 — P2 — `P-SCALE-MAG-INCONSISTENT` — **実装済（初期）**

- **典型**: 同一画像なのに倍率・スケールバー記載が矛盾。
- **読む**: 倍率／スケール文言。
- **比べる**: 画像同一性＋抽出メタの矛盾（`engine/scale_mag.py`）。
- **not_sufficient**: 画像非同一。
- **合成 fixture**: `fixtures/synthetic/scale_mag` + gold `scale_mag`（G7）。
- **sources**: literature (microscopy integrity)

#### G8. カウント系 n — P2 — `P-COUNT-N-MISMATCH` — **実装済（初期）**

- **典型**: ゲート後／コロニーカウント n と Legend n。
- **読む**: カウント対象の n（colonies/events 等）。
- **比べる**: カウント表行数 vs 抽出 n（`engine/count_n.py`；一般 n 系と分離）。
- **not_sufficient**: カウント表が無い。
- **合成 fixture**: `fixtures/synthetic/count_n` + gold `count_n`（G8）。
- **sources**: reporting guidelines

#### G9. ソースデータ同一値の過出現 — P0 — `P-SOURCE-DUPLICATE-VALUES` — **実装済（初期）**

- **典型**: 独立サンプル／別遺伝子パネルなのにソース表の値が厳密一致；丸め桁が不自然に揃う。
- **読む**: パネル／群の「独立実験」主張、ソース表の列対応。
- **比べる**: 同一表内の別群ラベルで完全一致多重集合（`engine/source_values.py`）。
- **not_sufficient**: 離散カウントで一致が自然に起きうる場合；機器分解能の説明だけで **有罪断定しない**（Warning は「要確認」）。
- **合成 fixture**: `fixtures/synthetic/source_dup` + gold `source_dup`（G9）。
- **sources**: check_reference, curated_pubpeer

#### G10. 整数倍・定数比・小数部指紋 — P0 — `P-SOURCE-RATIO-ARTIFACT` — **実装済（初期）**

- **典型**: 別条件なのに値の比が正確に 2/3/10/100；独立実験間で小数部だけが一致；棒＋誤差棒がスケール違いで同一形状。
- **読む**: 条件ラベル、y スケール注釈。
- **比べる**: 同一表内ペアで全要素が整数 k≥2 倍（`engine/source_values.py`）。小数部ハッシュは後続。
- **not_sufficient**: ΔΔCt 等で **単発**の 2 倍が出るだけ（Methods の計算式と整合し、頻度が低い場合）。
- **合成 fixture**: `fixtures/synthetic/source_ratio` + gold `source_ratio`（G10）。
- **sources**: check_reference, curated_pubpeer

#### G14. 比・正規化値の過剰桁 — P2 — `P-DERIVED-VALUE-PRECISION` — **実装済（初期）**

- **典型**: 1/3 を 0.3333 のまま載せる、qPCR ソフトの正規化値を 0.873452 のまま載せる → 「有効桁数ではない」と指摘される。科学的には無害なことが多いので、指摘を未然に防ぐための Warning。
- **比べる**: 群の非整数値の過半が「分母の小さい p/q（2・5 以外の素因数を含む）を 4 桁以上で打ち切り／丸めた値」、または 8 割以上が小数 5 桁以上・有効 6 桁以上（`engine/derived_precision.py`）。偶然一致率は小数桁数から分母上限を決めて抑える。Excel の加算誤差（…00001）は短い小数に戻してから判定。
- **読む**: 本文に比・正規化・ΔΔCt の記載があるか（文面と降格の切替のみ）。記載なし → Methods への明記か丸めを促す Warning、記載あり → 丸めのみ促す降格・情報。
- **not_sufficient**: 比・正規化値であること自体。
- **合成 fixture**: `fixtures/synthetic/derived_precision` + gold `derived_precision`（G14）。
- **sources**: curated_pubpeer

#### G11. 生存曲線の非整数羽数 — P1 — `P-SURVIVAL-COUNT-NONINTEGER` — **実装済（初期）**

- **典型**: Methods が「バイアルあたり固定 N 匹」なのに、生存率×N が非整数。
- **読む**: 初期個体数／バイアルサイズ、生存率表。
- **比べる**: `survival * N0` が整数に戻らない時点（`engine/survival_count.py`）。
- **not_sufficient**: Methods が「約 N」と明示；丸め誤差のみ（例: 0.999→1）。
- **合成 fixture**: `fixtures/synthetic/survival_noninteger` + gold `survival_noninteger`（G11）。
- **sources**: check_reference

#### G12. Methods／本文主張と図の矛盾 — P0 — `P-METHODS-CLAIM-MISMATCH` — **実装済（初期）**

- **典型**: Methods の結合比・サンプリング範囲・モデル名と、図・Extended Data・公開レビュー応答が食い違う。
- **読む**: Methods 主張スパーン（stoichiometry、CV bound、n／再現）。
- **比べる**: Methods↔Figure の比率スパーン不一致（`engine/methods_claim.py`）。モデル名等は後続。
- **not_sufficient**: 主張スパーン未抽出。
- **合成 fixture**: `fixtures/synthetic/methods_claim` + gold `methods_claim`（G12）。
- **sources**: check_reference, curated_pubpeer

#### G13. 除外基準なき個体／行の間引き — P0 — `P-EXCLUSION-UNDECLARED` — **実装済**

- **典型**: Methods／Legend が `n=6` なのにソース表にそれ以上の個体があり、除外基準の記載が無い。除外後の生存／体重曲線が公開図と不一致。
- **読む**: 記載 n、除外基準テキストの有無、除外 ID 列挙。
- **比べる**: (1) ソース有効行数 ＞ 記載 n かつ除外基準スパーンが空；(2) 除外記載あり時は Methods 列挙 ID ↔ テーブル omitted **set equality**（undeclared + phantom）；任意で再計算曲線。
- **not_sufficient**: 除外基準が Methods に明示され、除外 ID がテーブルと完全一致。
- **合成 fixture**: `exclusion_undeclared`（基準なし）／`exclusion_id_trace`（ID 未列挙）／`exclusion_id_phantom`（phantom ID）。
- **sources**: check_reference, ori_case_summaries, cope_case_database

### 2.5 P0 抽出キー（スキーマ先行）

| キー | 用途 | 優先 |
|------|------|------|
| `citation_flags` / 出典有無 | 画像再利用抑制 | 既存・必須 |
| `panel_map`（VLM） | パネル接地 | 既存（`--vlm-assist`・ベクター分割が空のときのみ）。ラスタ図中 OCR の将来強化（Qwen3-VL）は [handoff/figure-ocr-raster.md](handoff/figure-ocr-raster.md) |
| `error_bar_type` | G4 SEM/SD | **スキーマ＋rules＋突合エンジン初期**（`errorbar_sem_sd`） |
| `stat_test_name` / 比較構造 | G5 多重比較 | P1 |
| `numeric_claims[]`（mean/p/n） | G6 転記 | P1 |
| blot/ゲル種別ヒント | G2 | P1（読めなければ画像側のみ） |
| `independence_claims` / 実験独立性 | G9–G10 ソース指紋の FP 抑制 | **スキーマ＋rules 検知済** |
| `vial_or_cohort_size` / 生存初期 n | G11 生存非整数 | P1 |
| `exclusion_criteria` / 除外有無 | G13 無宣言除外 | **スキーマ＋rules 検知済** |
| `methods_claims[]`（モデル名・結合比・範囲） | G12 Methods↔図 | P2 |

---



## 3. 失敗モード → 検知能力マッピング


| 人が見落とした理由（説明資料）  | ソフトで潰す手段                       |
| ---------------- | ------------------------------ |
| 結果の方向性が正しいデータと同じ | 方向性ではなく **データ同一性／DAG 参照** を見る  |
| 単位変換は正しく反映されていた  | 単位チェックだけでは不十分 → **変数バインディング**  |
| ファイル名が条件 B だった | ファイル名を信頼しない → **中身ハッシュとコード引数** |
| 点の重なりで n が見えない   | 可視ドット数を正解にしない                  |


---



## 4. エンジン／モデル選定（確定方針）

**絶対条件**: **読む＝LLM/VLM、比べる＝機械**。

| 層 | 役割 | 担当 |
|----|------|------|
| **読む** | Legend / Results / Methods / Fig 画像からパネル・n・群・出典などの **チェック項目 JSON** を出す | ローカル LLM/VLM（本線） |
| **比べる** | JSON／数値を表・DAG・画像類似度と突合し **Warning を確定** | 決定論のみ |
| **フォールバック** | LLM 未導入・失敗時の安全網 | 規則パーサ（無限にルールを増やさない） |

不正判定モデルの学習は行わない。LoRA は抽出スキーマが多様な公開 Legend で繰り返し破綻したときのみ検討（製品必須にしない）。

### 4.1 決定論的コア（比べる・必須・学習不要）


| 役割          | 選定                                     | 理由                                    |
| ----------- | -------------------------------------- | ------------------------------------- |
| R 静的解析      | **tree-sitter-r**（Git 導入）              | ggplot `data=`・パイプ・複数作図の DAG。本事例の直接原因 |
| Python 静的解析 | **stdlib ast**                         | seaborn/matplotlib 同様                 |
| 表・統計        | **pandas + scipy.stats + statsmodels** | n・記述統計・検定の再計算                         |
| 文書          | **python-docx + PyMuPDF**              | Legend / 本文 / PDF 埋め込み図（チャンク素材）        |
| 顕微鏡         | **pylibCZIrw / readlif**               | `.lif` が本データセットの主フォーマット               |
| 画像類似        | **DINOv2 ViT-B/14** + **LightGlue**    | 回転・クロップ耐性。スクリーニング例示画像のクロス論文一致         |


DINOv2 は **預訓練のまま推論**（追加学習不要）。閾値は fixture（意図的複製ペア）で校正。

### 4.2 ローカル LLM / VLM（読む・本線）


| 役割                         | 選定                                     | 実行場所                                  | メモリ目安  |
| -------------------------- | -------------------------------------- | ------------------------------------- | ------ |
| Legend→JSON（**配布・生徒**） | **Qwen2.5-7B-Instruct 4-bit**（`qwen2.5-7b-mlx`） | Mac: **MLX** / DGX: `qwen2.5-7b-hf` | ~5–16GB |
| Legend→JSON（**開発・教師**） | **Qwen2.5-32B-Instruct**（`qwen2.5-32b-hf`） | DGX Spark / CUDA | ~64GB+ |
| Fig 接地（**配布用本命**） | **Qwen2.5-VL-7B Instruct** | Mac: **mlx-vlm** / DGX: CUDA | 16GB Mac 想定 |
| Fig 接地（**開発・教師用本命**） | **Qwen2.5-VL-32B Instruct** | DGX Docker（CUDA） | ~64GB+ |

プロファイルは Apache-2.0 のモデルに限る。Qwen2.5-3B（Qwen Research License・非商用）は低メモリ用の縮退先から外した（16GB 未満でも 7B 4-bit を使う）。


#### VLM 役割と選定理由

| # | 役割 | 選定 | 強み |
|---|------|------|------|
| ① | **配布用本命** | **Qwen2.5-VL-7B Instruct** | **mlx-vlm** による Apple Silicon 最適化が進んでおり、16GB Mac でも OS・他アプリのメモリを圧迫せず高速動作する |
| ② | **開発・教師用本命** | **Qwen2.5-VL-32B Instruct** | 7B で起きやすい「複雑な条件表記（例: `geneX -/-; UAS-geneY-RNAi`）の読み飛ばし」や「密集したパネル番号の誤認識」が大幅に減る |

- **① の役割**: Figure PDF 内のパネル境界（A, B, C…）、軸ラベル、グラフ種別（Bar / Box / Dot）の視覚的コンテキスト把握。
- **② の役割**: DGX 上でゴールドデータセットを作成し、7B の抽出プロンプト調整や LoRA／蒸留の正解ラベル生成に使用。
- パネル分割の **決定論優先**（PyMuPDF ベクター bbox）は変わらない。VLM は不足分の補助と視覚コンテキスト抽出。

**採用しないもの**

- クラウド API（OpenAI 等）— 要件違反
- 「不正検知」専用に分類器を学習 — 説明可能性・偽陽性が悪化。決定論シグナルで足りる
- 論文ごとに正規表現ルールを増やし続けること — 読む本線に反する
- **Pixtral / MiniCPM-V** — 候補カタログから除外（Qwen2.5-VL 本命に一本化）
- 巨大 VLM（72B 級）の **配布必須化** — 開発機 bakeoff は可。16GB Mac 製品既定にはしない



### 4.3 学習でやること／やらないこと


| やる                                                   | やらない                        |
| ---------------------------------------------------- | --------------------------- |
| Legend／Fig 接地の **スキーマ＋プロンプト＋Fig チャンク**（読む本線）       | **製品必須の LoRA**（現状不要）        |
| DINOv2 埋め込みの **ローカルキャッシュ**（過去論文ライブラリ）                | DINOv2 のフルファインチューン（コスト対効果低） |
| 擬似エラーセット（D1–D4）での **閾値・適合率校正**（`eval/metrics_suite`） | PubPeer コメント文からの「有罪スコア」学習・サイト自動クロール |
| （例外）難しい Legend で 7B が落ちるとき **32B 候補＋人手修正ラベル**でプロンプト／LoRA／蒸留（抽出専用。生 32B 出力を無審査で教師にしない） | クラウド上の学習ジョブ / 特定データセット専用学習  |
| 研究公正文献＋人手キュレーションで **pattern_id ネタ帳**を拡充 | 文献 PDF／PubPeer HTML の git 投入 |


Docker `train` イメージはキャッシュ構築・閾値スイープ用。LoRA はデフォルト経路にしない。

**LoRA を急がない理由**: 製品価値は「読んだ JSON を機械が突合した説明可能な Warning」。まずスキーマ・チャンク・VLM 接地・モデル bakeoff で精度を上げる。過適合 LoRA は任意原稿への汎化を壊す。

### 4.4 パイプライン順序（推奨・精度強化版）

```
input/ 収集
  → Figチャンク（Legend + Results + Methods）
  → PDFベクター: パネルラベル座標 → 矩形分割（決定論・優先）± VLM 補助
  → LLM: スキーマ強制 JSON（読む。有罪判定はしない）
  → Tier1: DAG + データ指紋ハッシュ（比べる・完全一致）
  → Tier2: 統計量・n の許容誤差突合（比べる・近似）
  → Tier3: 表記揺れの候補正規化のみ LLM（読む）。Warning 確定は機械
  → DINOv2/LightGlue（画像）± 出典フラグ
  → FP 抑制（明示共有 Ctrl 等）→ HTML レポート
```

`check_reference/` は **評価用ゴールド観点**および回帰設計に使い、毎回の推論入力に必須ではない。

---



## 5. 精度向上の指針（Legend / Fig↔データ / n）

目標は「規則を増やす」ではなく、**Entity Linking（対応関係）**・**読む JSON の安定性**・**複合 Fig のパネル接地**を上げること。

**実装の優先順位**: [ARCHITECTURE.md](ARCHITECTURE.md)「設計ルール」。  
**認識**: ネタ帳で Taxonomy を増やしても、紐付けが外れると **False Positive が増えるだけ**。精度の本線は決定論照合の強化。

**達成済み（初期）**: 軟紐付け・n 対照表ファイル名・未紐付け明示。残りは下記ボトルネック潰し。

### 5.0 精度低下の3大ボトルネック

| # | ボトルネック | 典型症状 | 潰し方 |
|---|--------------|----------|-----------------|
| ① | **Entity Linking の曖昧さ** | Excel列 ↔ R変数 ↔ Legend ↔ PDF パネル記号の取り違え → FP / 漏れ | データ指紋＋3層照合。ファイル名非信頼 |
| ② | **LLM の非決定性・ハルシネーション** | n／検定の抽出揺れ・パース失敗 | JSON Schema / Pydantic 強制（Outlines・MLX JSON mode）。自由文抽出をやめる |
| ③ | **複合 Figure のパネル分割精度** | 1 PDF に数十パネルで VLM 座標ズレ → 画像・n の誤パネル | **PyMuPDF ベクター**で A/B/C… テキスト bbox → 幾何分割。VLM は補助 |

### 5.1 3層ハイブリッド照合（比べる＝機械が本線）

| Tier | 内容 | LLM | ねらい |
|------|------|-----|--------|
| **1** | DAG（`ggsave`←DF）＋表／点列の **ハッシュ・指紋完全一致** | 使わない | 一致パスは説明可能・高精度 |
| **2** | 生データ再計算（mean/SD/n/p）↔ Legend／本文数値、許容誤差（例: ε≈1e-3） | 使わない（数値抽出は規則優先） | 転記ミス・n 誤記 |
| **3** | 表記揺れ（`geneX-/-` ↔ `geneX mutant`）の **候補正規化・候補キー提案** | 使う（読むのみ） | Warning の有罪／無罪は機械が確定 |

Tier 3 で「この Fig は不正か」を聞かない（§5.4）。

### 5.2 Legend・n 抽出（読む）

| 優先 | 施策 | ねらい |
|------|------|--------|
| P0 | **Fig 単位チャンク**を LLM 入力にする | 隣パネル n の誤紐付け抑制 |
| P0 | **スキーマ強制**（`LEGEND_JSON_SCHEMA` + `coerce_legend_dict` + `llm/json_mode` Outlines） | パース失敗・欠落キー・不正パネル行を減らす。Outlines 未導入時は free+coerce |
| P0 | **文脈プロンプト**（括弧＝パネルか群か） | Fig2A / Fig5E 型 |
| P0 | **PDFベクター・パネル分割**（`pdf_panel_geometry`: 太字1文字 bbox → 矩形 → crop） | 複合 Fig。VLM 単独分割に頼らない |
| P1 | VLM パネル地図はベクター失敗時の **補助** | bakeoff で抽出本線を決める |
| P1 | モデル bakeoff → 必要なら蒸留 | 配布サイズ |
| P2 | 抽出専用 LoRA | スキーマ強制でも破綻が続く場合のみ |

### 5.3 Fig ↔ 実験データ Entity Linking（比べる）

| 優先 | 施策 | ねらい |
|------|------|--------|
| P0 | **データ指紋**: ソート済み数値ベクトル／群別 n・mean・分散 | ファイル名依存を廃止 |
| P0 | **三角照合**（抽出キー ↔ 表行数 ↔ 点列ハッシュ）＋採用根拠をレポート | FP の説明と抑制 |
| P0 | **3層スコア**（Tier1→2→3）で採用 | ①のボトルネック |
| P1 | スクリプト DAG 本線リンク（`read`→DF→`ggplot`/`ggsave`） | H1 根因 |
| P1 | 候補キー接地（規則／LLM 提案 → 機械スコア採用） | 表記揺れ — **規則経路 初期実装済**（`key_normalize` / `tier3`） |
| P1 | 顕微鏡メタ接地 | 画像パネル |
| P2 | **実験単位はフォルダ**（`FigN` 推奨）。弱い手がかりはフォルダ内、指紋・n+群は誤配置でも横断 | 似た実験が並ぶ論文での誤紐付けを抑えつつ、置き場所ミスを拾う |

### 5.4 サンプルサイズ・FP 抑制

- **正の n** = 生データ群別の有効行数（またはスクリプト入力 n）。ggplot の可視ドット数は補助のみ・判定に使わない。
- Warning 理由文でも同じ定義を明示する（`n_authority=raw_data_nrows`）。
- 抽出／紐付け欠落は **未紐付け**（未検出と混同しない）。
- **False Positive 抑制**: shared control / reproduced from を明示 → 抑制。弱い記載 → `【降格】` + `severity=info`（レポートで区別表示）。

### 5.5 評価ハーネス（定量）

- `gold_eval` / `metrics_suite` で **pattern_id 単位**の Precision / Recall を計測（「100%」の製品宣伝はしない）。
- CI ゲート例: 合成＋必須ゴールドで **required recall = 1.0**；拡張スイート（〜20 ケース目標）で Precision／Recall を記録し回帰を検知。
- 失敗時は **パーサー／紐付け／照合**のどこで落ちたかをレポート（カバレッジと一体）。

### 5.6 やらないこと

- 論文ごとの正規表現積み増し
- LLM に「この Fig は不正か」を最終判定させる
- 有罪分類器の学習
- PubPeer 自動スクレイプ
- Taxonomy だけ増やして Entity Linking を後回しにすること（FP 増の主因）

---



## 6. 回帰テストに落とし込む最小セット

primary private benchmark 相当の fixture（匿名化コピー可）:
1. 正しい条件 B の xlsx + 誤って条件 A を参照する `.R` → **D1 / B1 必須検知**
2. Legend `n=9` + 実データ 8 行 → **D2 / C1**
3. 同一 TIFF/PNG を Fig.S1 と「先行論文」フォルダに配置、Legend に出典なし → **D3 / A4**
4. 同一 WT ベクトルを 3 パネルに、1 パネルだけ max 欠落 → **D4 / B2**
5. （推奨）`n=18(a)/10(b)`・`n=5(1x),8(4x)`・近傍パネル括弧のゴールド → **読む JSON**（合成: `fixtures/gold/legend_json/legend_json_synthetic_matrix.json` + `tests/test_legend_json_gold.py`）
6. `pattern_synthetic_matrix.json` + `tests/test_pattern_id_matrix.py` — **pattern_id 1:1 合成 CI**（実装済は必須再現；planned は fixture_policy のみ）
7. 複合マルチパネル PDF のベクター分割ゴールデン（パネル bbox）— `fixtures/gold/panel_bbox/` + `tests/test_panel_bbox_gold.py`

---



## 7. 改訂履歴

- 2026-09-25: **bbox ゴールデン + P-EXCLUSION-UNDECLARED 初期** — 2×2 合成 pct CI。data_n>legend_n かつ除外基準なしで Warning（合成 exclusion_undeclared）。
- 2026-09-25: **P0 抽出キー** — `error_bar_type` / `independence_claims` / `exclusion_criteria` を Legend JSON スキーマ・rules・coerce に追加。
- 2026-09-25: **typo merge 抑制** — `_drop_llm_n_stolen_from_rules`（rules 所有 empty-group n の別パネル付け替えを drop）。CUDA 7B hybrid で forbid_fp=0。
- 2026-09-25: **オフライン受入を要件から削除**（完全ローカル・API $0 は維持）。メモリ監視は将来。
- 2026-09-25: **読む JSON 合成ゴールド CI** — `legend_json_synthetic_matrix` + `test_legend_json_gold` / `dev_legend_json_gold_eval.py`。
- 2026-09-25: **Mac 受入** — `dev_vlm_panel_map_verify --prefer mlx --synthetic --require-vlm` → A/B・`n_vlm=2`。
- 2026-09-29: **ラスタ Figure パネル OCR（製品）** — ベクター `_panel_labels` が空／スパン内に欠けのある出版 Fig PDF で Florence layout + crop OCR（Mac は Vision と Florence の和、Linux は Florence）→ ベクターと和を取り `figure_panel_labels` / `[figure_panel_labels]`（`needs_review`）へ。連続性フィルタ `dominant_panel_run` で軸・凡例の孤立文字を除く。quick ベンチ: 単独 82.1%（余分 7/3）→ **和＋フィルタ 87.2%（余分 0）**。`PRE_PEER_CHECKER_RASTER_PANEL_OCR=0` で off。Qwen3-VL は将来差し替え候補。[handoff/figure-ocr-raster.md](handoff/figure-ocr-raster.md)。
- 2026-09-25: **VLM パネル地図補助** — ベクター分割が空のときのみ mlx-vlm / transformers-VL（`--vlm-assist`）。`scripts/dev_vlm_panel_map_verify.py`。
- 2026-09-25: **6A JSON mode** — `llm/json_mode.py`（Outlines MLX/transformers → free+coerce）。Mac 検証: `scripts/dev_legend_json_mode_verify.py --prefer mlx --require-outlines`。
- 2026-09-25: **pattern_id 1:1 合成 CI**（`pattern_synthetic_matrix.json` + `test_pattern_id_matrix`）。G9/G10 初期エンジン（`source_values`）+ `stats_recalc` 合成。
- 2026-09-24: §4.2 VLM を **Qwen2.5-VL-7B（配布）／32B（開発・教師）** に確定。Pixtral / MiniCPM-V を除外。
- 2026-09-24: **カタログ倫理の絶対ルール**（表現を取らない／抽象化／名指し・攻撃しない）を冒頭と `catalog_policy.absolute_rules` に固定。
- 2026-09-24: §1.4 ブラッシュアップ — 抽出プロンプト固定（固有情報除去）、共有 JSON schema 1.1、Issue=Web Intent / PR=CLI コピー（PAT 不要）。
- 2026-09-24: §1.4 に **PubPeer PDF D&D（cache のみ）** と **Issue/PR 共有（抽象ルールのみ）** を追記。収集⑤と「やらない」表を同期。
- 2026-09-24: **公式オープンソース拡充パイプライン実装**（RW pull / Reason マイニング / ORI·COPE 種 / YAML export / 日次パルス）。`P-EXCLUSION-UNDECLARED` 追加。手元参照は git 外。
- 2026-09-24: **`check_reference` 拡充（PubPeer 14）** を §1.1 に反映。Taxonomy **E3 / F1–F3** とギャップ **G9–G12**（ソース同一値・比指紋・生存非整数・Methods 矛盾）を追加。
- 2026-09-24: §5 に **3大ボトルネック・3層照合・PDFベクター分割・スキーマ強制・Eval ハーネス**を追加。Taxonomy 増だけでは FP が増える旨を明記。
- 2026-09-24: **公式オープンソース＋ Verification Taxonomy A–E** を §1–§2.0.1 に追加。
- 2026-09-24: **Phase 5 ネタ帳** — 収集源ポリシー、文献シード、ギャップ G1–G8、P0 抽出キー。§5 を Phase 6 精度に対応付け。
- 2026-09-24: **読む＝LLM、比べる＝機械**を絶対条件に改訂。§5 精度向上指針を追加。
- 2026-09-26: **Phase 7 初期** — Taxonomy E4/E5。参考文献メタ＋ユーザー提供 PDF の引用根拠レビュー（情報カード）／決定論矛盾 Warning。
- 2026-09-22: `check_reference` 初回読解に基づき作成。モデル選定を要件・handoff と整合。
