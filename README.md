# Pre-peer-checker

**日本語** | [English](README.en.md)

> [!WARNING]
> **アルファ版（開発中）です。** 仕様や出力形式は予告なく変わります。検知精度は検証の途中で、Warning が出ないことは「問題がない」ことを意味しません。結果は投稿前セルフチェックの補助として使い、最終判断は必ず人が行ってください。

## フィードバックのお願い

いまは実際の原稿・データで試していただき、**見逃しと誤検知**を集めて精度を上げている段階です。気付いた点（見逃し、うまく動かない原稿・データの種類、追加してほしいチェックなど）を、Slack・メール・[GitHub Issues](https://github.com/YuyaSanaki/Pre-peer-checker/issues) などで教えていただけると大変助かります。

次の情報があると助かります。

- 使用環境（Mac / Linux、GPU の有無）と、LLM / VLM 補助のオン・オフ
- 誤検知: Warning のタグ・`pattern_id`、実際は何が正しかったか
- 見逃し: 本来見つけてほしかった食い違いの種類（例: Legend の n と生データの行数の不一致）
- エラーの場合: 画面やターミナルに出たエラーメッセージ

**未発表の原稿・生データ・画像・個人情報はそのまま送らないでください。** 共有する場合は、数値や名前を伏せた最小の再現例（合成データ）にしてください。再現用の合成データの例は [`fixtures/synthetic/`](fixtures/synthetic/) にあります。

## チェックできること

論文を投稿する前に、**原稿・Figure・実験データ・解析コードのあいだの食い違い**を自動でチェックするツールです。n の転記ミス、データの取り違え、図の貼り間違いといったうっかりミスを、投稿前に見つける手助けをします。

- **すべて手元のパソコンの中で動きます。** 原稿やデータを外部に送信しません。外部 API の課金もありません。
- **ブラウザで操作します。** フォルダを選んで「照合開始」を押すだけです。
- **結果は「確認したほうがよい箇所」の一覧**（Warning）として出ます。

食い違いの判定そのものは AI に任せていません。AI（LLM / VLM）は、原稿や Figure から n・群名・統計値などの情報や文脈を読み取るために使い、読み取った内容と実験データ・解析コードを機械的に照合します。画像の類似度の計算には画像認識モデルを使いますが、Warning にするかどうかは決まったしきい値で判定します。照合する観点は、Retraction Watch や PubPeer で頻繁に指摘されているケースをカタログとしてまとめたものです。


| 観点           | 例                                                                              |
| ------------ | ------------------------------------------------------------------------------ |
| サンプルサイズ（n）   | Figure Legend の n と、生データの行数が合わない／記載の n より多い行があるのに除外基準が書かれていない／コロニー数などのカウントの n が合わない |
| データの取り違え     | 別の条件・別の Figure に同じデータが使われている／ファイル名と中身が合わない／本文・Figure と解析設定で群名（遺伝型・条件）が合わない |
| 統計           | 生データから再計算した平均・SD・p 値が本文と合わない／3 群以上なのに多重比較をしていない／データの対応あり・なしと検定の種類が合わない |
| 誤差棒          | Legend では SEM と書いているのに、生データから計算すると SD になっている（またはその逆）                         |
| 数値の転記        | 本文に書いた平均値や p 値が、表や Figure の値と合わない                                            |
| 画像の重複・再利用    | 論文内の別パネル、または自分たちの過去の論文と同じ画像が使われている（出典の書き忘れ）／回転・トリミング・拡大縮小した画像やブロットのレーンの使い回し |
| 倍率・スケール      | 同じ画像なのに、倍率やスケールバーの記載がパネルごとに異なる／図中に描かれたスケールバーの長さ（例: 100 µm）が Legend の記載（例: 50 µm）と合わない |
| コントロール群の共有   | 複数のパネルで同じコントロールを使っているのに記載がない／共有コントロールから一部の点を抜いたものを、独立した実験として示している       |
| ソースデータの値     | 独立したサンプルのはずなのに値が完全に一致する／別条件の値がちょうど整数倍になっている／生存率 × 個体数が整数にならない              |
| Methods との整合 | Methods に書いた比率・条件と、Figure の値が合わない                                             |
| 抗体の記載        | 抗体の宿主動物種が品番と合わない（HPA 品番＝ウサギなのに mouse と記載など）／二次抗体（anti-rabbit 等）に対応する一次抗体の宿主がない／同じ品番・RRID で宿主が違う |
| 表記・参照        | 本文の「Fig. 1C」と実際のパネルが合わない／参考文献リストの抜け・重複／引用文が引用先論文の内容と矛盾する                   |
| 数値の表示        | 比や正規化から計算した値が、必要以上の桁数のまま（1/3 → 0.3333 など）                                  |

上の表は主な観点の例です。観点の全一覧（`pattern_id` ごとの判定条件）は [検知観点カタログ](docs/DETECTION_AND_MODELS.md) にまとめています。




## 想定用途と利用上のお願い

本ソフトウェアは、**著者自身（共著者・所属研究室を含む）が、自分たちの原稿を投稿前に見直すためのセルフチェック補助ツール**です。

- **目的外での利用はお断りします。** 他者の論文や公開済みの論文を検証・調査する目的での利用は想定していません。
- **本ソフトウェアの出力は、研究不正の有無を判断する根拠にはなりません。** Warning は「人が確認したほうがよい箇所の候補」を機械的に列挙したもので、誤検知と見逃しの両方を含みます。画像の類似やデータの不一致には、同一対照の再掲、記載の揺れ、ツール側の読み取り誤りなど正当な理由があることも多く、Warning の有無だけで研究の正しさや研究者の意図を判断することはできません。
- **出力を PubPeer 等への投稿、研究不正の告発・通報、その他特定の研究者を批判する材料として使わないでください。** 機械的な判定結果が文脈を離れて流通すると、根拠の不十分な疑いによって研究者の名誉や研究への信頼が不当に損なわれるおそれがあります。
- 研究公正上の懸念をお持ちの場合は、本ソフトウェアの出力に頼らず、一次データや原資料を人が確認したうえで、所属機関や学術誌が定める正規の手続きに沿って対応してください。

あわせて「[免責事項](#免責事項)」もご確認ください。

## 動作環境


| OS / ハードウェア                             | 対応状況        | 備考                                                               |
| --------------------------------------- | ----------- | ---------------------------------------------------------------- |
| **macOS（Apple Silicon: M1 以降）**         | ✅ 推奨・全機能    | macOS 14 以降を推奨                                                   |
| **Linux（x86_64 / aarch64）+ NVIDIA GPU** | ✅ 全機能       | NVIDIA ドライバ 560 以上。動作確認: DGX Spark（aarch64 / GB10）、x86_64 + H100 |
| Linux + AMD GPU（ROCm）/ Intel GPU（XPU）   | ⚠️ 実験的      | x86_64 のみ。GPU が使えない場合は CPU で動作                                   |
| Linux（GPU なし）                           | ⚠️ 限定的      | 照合本体は動作。LLM / VLM 補助は実用的な速度になりません                                |
| macOS（Intel）                            | ⚠️ 最小構成・未検証 | LLM / VLM と画像照合は使えません                                            |
| Windows                                 | ❌ 非対応       | WSL2 上なら Linux と同じ手順で動く可能性がありますが未検証です                            |


そのほかに必要なもの:

- メモリ: Mac は 32GB 以上を推奨。Legend LLM の既定モデル **Qwen2.5-32B** は 7B より読み取り精度が高い一方（n をパネルへ割り当てる精度: 開発評価で 32B 約 0.83 対 7B 約 0.6）、読み込むと **GPU 側のメモリを約 18.5GB** 使います（Mac はメモリを CPU と GPU で共有するため、本体メモリから約 18.5GB。照合全体のピークは約 27GB）。LLM を呼ばない照合では本ツールが使うのは 6〜7GB 程度です。メモリ 32GB 未満の Mac（Linux は GPU メモリ 64GB 未満）では、インストール時・WebUI 起動時に既定が自動で 7B（`qwen2.5-7b-mlx`、約 4GB）になります。WebUI のモデル選択か環境変数 `PRE_PEER_CHECKER_LLM_PROFILE` で上書きできます
- Linux の GPU メモリ: 20GB 以上を推奨。それ未満の GPU では AI モデルの一部を PC のメモリに置いて動かすため、照合は完了しますが遅くなります
- 空きディスク: Mac で約 30GB、Linux + GPU で約 40GB（AI モデルを含む）
- インストール時のインターネット接続
- ブラウザ（Chrome / Safari / Firefox など）

Python はなくても構いません（インストーラが自動で用意します。パソコン既存の Python は変更しません）。

## インストール（初回のみ）



### 1. ダウンロードする

[リポジトリのページ](https://github.com/YuyaSanaki/Pre-peer-checker)で **Code → Download ZIP** を選び、ZIP を展開します。

![GitHub の「Code」→「Download ZIP」](docs/fig/Untitled.001.png)

展開したフォルダ（`Pre-peer-checker-main`）を、ホームフォルダなど分かりやすい場所に移します。

![展開したフォルダをホームフォルダに移す](docs/fig/Untitled.002.png)

Git を使える場合は `git clone https://github.com/YuyaSanaki/Pre-peer-checker.git` でも構いません（フォルダ名は `Pre-peer-checker` になります）。

### 2. インストーラを実行する

ターミナルを開き（Mac では Spotlight で「ターミナル」または「Terminal」と検索）、次の 2 行を実行します。

```bash
cd Pre-peer-checker-main
bash install.sh
```

![ターミナルで install.sh を実行する](docs/fig/Untitled.003.png)

1. 最初に **利用区分** を聞かれます。画像照合に使う部品のライセンス条件が異なるためです（詳しくは「[ライセンス](#ライセンス第三者コンポーネント)」）。
   - 大学・非営利組織での非商用研究 → `1`
   - 企業・製薬企業・CRO など、それ以外の場合や判断がつかない場合 → `2`
2. あとは自動で進みます。必要なソフトと AI モデル（Mac で合計約 25GB。うち Legend LLM の 32B モデルが 18.4GB）をダウンロードするため、しばらく時間がかかります。
3. 最後に機能チェックの結果が表示され、「**セットアップ完了**」と出れば終わりです。

途中で失敗した場合は、ネットワークを確認してもう一度 `bash install.sh` を実行してください。足りない分だけ補います。

<details>
<summary>インストールのオプション（環境変数）</summary>

例: `PRE_PEER_CHECKER_SKIP_MODELS=1 bash install.sh`

| 環境変数 | 効果 |
| --- | --- |
| `PRE_PEER_CHECKER_SKIP_MODELS=1` | モデル重みの事前取得を省略（初回照合時に取得） |
| `PRE_PEER_CHECKER_PYTHON=/path/to/python3` | 使用する Python（3.11 以上）を明示 |
| `PRE_PEER_CHECKER_ACCEL=cuda\|rocm\|xpu\|cpu` | Linux の GPU 種別を強制（既定: 自動判定） |
| `PRE_PEER_CHECKER_TORCH_INDEX=URL` | PyTorch の取得元 index を明示 |
| `PRE_PEER_CHECKER_EXTRAS=dev,gui` | 追加の extras を導入 |
| `PRE_PEER_CHECKER_USAGE=academic\|commercial` | 利用区分の質問を省略 |
| `HF_HOME=/path/to/cache` | AI モデルの保存先を変更（既定: `~/.cache/huggingface`。ホームの容量が少ない共用サーバなど） |

</details>


### 3. 起動する

- **Mac**: フォルダ内の `Pre-peer-checker.command` をダブルクリック
- **Linux**: `scripts/start_webui.sh` を実行（または `Pre-peer-checker.desktop` をダブルクリック）

ブラウザで照合画面（[http://127.0.0.1:8765](http://127.0.0.1:8765) ）が開きます。2 回目以降はこの手順だけで起動できます。

![Pre-peer-checker.command をダブルクリックする](docs/fig/Untitled.004.png)

## 使い方



### 1. 照合するフォルダを用意する

親フォルダを 1 つ作り、その中に `manuscript` と `data` という名前のフォルダを置きます。

```text
MyCase/
  manuscript/          # 原稿（Word）と Figure の PDF
    paper.docx
    Fig1.pdf
    Fig2.pdf
  data/                # 実験データ。**実験一式は必ずサブフォルダにまとめる**
    Fig1/              # Figure 1 の表・スクリプト・関連画像
      WT.xlsx
      graph.xlsx
      analyze.R
    Fig2/
      ...
    FigS1/
      ...
```

- **（絶対条件）実験一つ分のまとまりはフォルダです。** `data/` 直下に Excel や CSV を全部並べないでください。同じ実験の生データ・作図表・スクリプトは **1 つのサブフォルダ** に入れます。似た結果の図が多い論文でも、フォルダ単位で突合します。
- **フォルダ名は `Fig1` / `Fig2` / `FigS1` を推奨します**（原稿の図番号に合わせる）。照合はこのフォルダを優先します。別の Fig フォルダにファイルを置いてしまっても、中身の数値や群名がはっきり一致すればそちらにも紐付けます。
- **原稿は Word（`.docx`）がおすすめです。** Word が無いときは PDF 原稿から本文を読み取り、Figure Legend の n・本文の統計・参考文献も照合します。ただし段組みや改行の復元に頼るため、Word より取りこぼしが出やすくなります。スキャン画像だけのページは OCR（Tesseract）で読みます。Tesseract は `install.sh` が入れます（Mac は Homebrew、Linux は apt / dnf を使い、Linux では管理者パスワードを求められます）。入れられなかった場合はそのページを読めず、カバレッジに表示します。Figure の PDF は原稿と一緒に置いてください。
- **出版 Figure（PDF、または `Fig1.png` のような JPEG/PNG）** では、パネル文字（A/B/C…）を読み取り、Legend LLM のヒント `[figure_panel_labels]` に渡します。PDF はまずベクター文字を探し、取れない文字があるときだけ OCR します。JPEG/PNG は最初から OCR します（PDF 経由で 300 dpi に引き伸ばしません）。**Mac** では `install.sh` が入れる **Apple Vision**（`pyobjc-framework-Vision`）だけで読み（図全体・タイル・写真ごとの切り出しを合わせて読む。Florence-2 は使いません）、**Linux + GPU** では Florence-2 で読みます（`install.sh` の transformers 構成）。読み取り結果は要確認として扱います。無効にするには `PRE_PEER_CHECKER_RASTER_PANEL_OCR=0`、Mac で Vision だけ省略するには `PRE_PEER_CHECKER_SKIP_FIGURE_VISION=1` を設定します。
- `.zip` のまま入れても自動で展開します。



### 2. 照合する

![照合画面の操作手順](docs/fig/Untitled.005.png)

1. **ケース親フォルダ**: 「フォルダを選ぶ」で、用意した親フォルダ（例: `MyCase`）を選びます。
2. **過去論文コーパス（任意）**: 自分たちの過去の論文の PDF をドロップすると、画像の使い回し（出典の書き忘れ）もチェックします。
3. **参考文献 PDF（任意）**: 引用している論文の PDF をドロップすると、参考文献リストの書誌情報や、引用文が引用先の内容と矛盾していないかもチェックします。
4. **照合開始** を押します。その上にある LLM 補助などの設定は既定のままで構いません。

ドロップした PDF はこのパソコンの中（`cache/`）にだけ保存され、次回以降も選べます。「カタログ更新」タブは開発者向けの機能なので、使わなくて構いません。

照合にはデータ量や PC の性能に応じて 30 分〜2 時間ほどかかります（過去論文・参考文献の PDF が多いとさらに長くなります）。画面に進み具合と残り時間の目安が表示され、ブラウザのタブを閉じても処理は続きます。

![照合の進捗表示](docs/fig/webui_progress.png)

### 3. 結果を見る

照合が終わると、画面の下に Warning の一覧が表示されます。根拠のファイルや n の対照表を含む詳しいレポートは「**HTML レポートを開く**」から開けます。

![「HTML レポートを開く」リンク](docs/fig/webui_report_link.png)

結果を読むときのポイント:

- 各 Warning には `[サンプルサイズ記載誤記]` `[データ取り違え]` `[画像重複・再利用（要出典確認）]` のようなタグが付きます。
- Warning は「確認したほうがよい箇所の候補」です。共有コントロールの再掲など、正当な理由があるものも含まれます。根拠のファイルを見て、人が判断してください。
- Warning が出なくても、問題がないとは限りません。



## 困ったとき

- **インストールが途中で失敗した**: ネットワークを確認して `bash install.sh` をもう一度実行してください。足りない分だけ補います。
- **「フォルダが見つからない」旨のエラーが出る**: 選んだ親フォルダの中に `manuscript` と `data` があるか確認してください。
- **n 対照表がほとんど空／未紐付け**: `data/` 直下に表を並べていませんか。実験一式を `Fig1/` などのサブフォルダに分けてください。
- **ブラウザが開かない**: 起動した状態のまま、ブラウザで [http://127.0.0.1:8765](http://127.0.0.1:8765) を開いてください。
- **利用区分を変えたい**: `bash install.sh` を再実行すると選び直せます。
- **原稿やデータは外部に送られますか？**: 送られません。照合はすべてこのパソコンの中で行います。インターネットは主にインストール時のソフトとモデルのダウンロードに使います。



## 免責事項

- 本ソフトウェアは現状のまま（AS IS）提供され、出力の正確性・完全性・特定目的への適合性を含め、明示・黙示を問わず一切の保証をしません（[LICENSE](LICENSE) の条項も併せて適用されます）。
- Warning が出ないことは、原稿・データ・Figure に問題がないことを保証するものではありません。
- 本ソフトウェアの利用または利用できなかったことにより生じたいかなる損害（投稿・査読・採否の結果、誤検知や見逃しに起因する損害、データの消失、第三者との紛争を含み、これらに限りません）についても、作者および貢献者は、法令上許容される最大限の範囲で責任を負いません。
- 出力をどのように解釈し、どのような判断・行動をとるかは利用者自身の責任です。上記「想定用途と利用上のお願い」に反する利用によって第三者との間に紛争が生じた場合は、利用者が自らの責任と負担で解決するものとします。
- 本 README の記載は法的助言ではありません。

---



## 開発者向け情報

ここから下は、開発・カスタマイズする方向けの情報です。

**設計の原則**: **読む＝LLM/VLM**（Legend・Fig チャンク → チェック項目 JSON）、**比べる＝機械**（表・DAG・画像類似度で Warning を確定）。LLM の判断だけで Warning を確定することはありません。詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)「設計ルール」。

### ドキュメント

- [要件定義書](docs/REQUIREMENTS.md)
- [技術設計・設計ルール](docs/ARCHITECTURE.md)
- [検知観点 & モデル選定・精度指針](docs/DETECTION_AND_MODELS.md)（`pattern_id` 体系、§1.4: カタログ拡充 — RW/ORI/COPE・PDF D&D・Issue/PR）
- [Fixtures（汎用パターン / ゴールド）](fixtures/README.md)
- [Docker 実行・学習](docs/DOCKER.md)



### モジュール構成


| モジュール       | 内容                                                                                    |
| ----------- | ------------------------------------------------------------------------------------- |
| `parsers/`  | Python AST / R tree-sitter / YAML / Word / PDF / Fig チャンク / 参考文献・本文 cite / 引用先 PDF 取込 |
| `data/`     | Excel・CSV 群推定と統計再計算（比べる）                                                              |
| `engine/`   | 決定論照合（n・群ベクトル・統計・参照・参考文献メタ・引用主張）                                                      |
| `imaging/`  | CZI/LIF ローダ、画像重複スキャン（軽量フォールバック + DINOv2）                                              |
| `llm/`      | ローカル LLM/VLM（読む本線）+ モデルレジストリ                                                          |
| `catalog/`  | 照合カタログ拡充（RW pulse・PubPeer PDF 取込・Issue/PR 共有）                                         |
| `pipeline/` | オーケストレーション                                                                            |
| `report/`   | HTML Warning レポート（n 対照表・カバレッジ）                                                        |
| `web/`      | ローカル WebUI（照合＋カタログ更新タブ）                                                               |




### 開発環境（ホスト venv）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
# 画像・顕微鏡フォーマットが必要なら:
# pip install -e ".[dev,imaging]"
# R tree-sitter 本線（language-pack）:
# pip install -e ".[dev,r-ast]"
```



### CLI

```bash
pre-peer-checker path/to/case -o outputs/report.html --json outputs/warnings.json
# 過去論文の画像フォルダ（画像再利用チェック）
pre-peer-checker path/to/case --corpus path/to/past_figures -o outputs/report.html
# 引用先 PDF（ファイルまたはフォルダ）
pre-peer-checker path/to/case --cited-papers path/to/reference_pdfs -o outputs/report.html
```

LLM / VLM 補助:

```bash
# pip install -e ".[mlx]"          # Mac
# pip install -e ".[llm-cuda]"     # Linux GPU
# pip install -e ".[llm-json]"     # Outlines（JSON schema 強制。未導入時は free+coerce）
# pip install -e ".[vlm-mlx]"      # Mac: パネル地図補助（mlx-vlm）
# pip install -e ".[vlm-cuda]"     # Linux GPU: Qwen2.5-VL
# Legend LLM は既定で auto（規則が読み残した n だけ、LLM がパネルへ割り当てる）
pre-peer-checker ... --legend-llm        # on: 全 Figure を LLM で読む（1 Figure 数分）
pre-peer-checker ... --legend-llm off    # 規則のみ
pre-peer-checker ... --vlm-assist --vlm-prefer auto --vlm-profile qwen2.5-vl-7b-mlx
# 明示例: --legend-llm-prefer cuda --llm-profile qwen2.5-32b-hf（軽量: qwen2.5-7b-mlx / qwen2.5-7b-hf）
# 受入スクリプト（Mac）:
#   python scripts/dev_legend_json_mode_verify.py --prefer mlx --require-outlines
#   python scripts/dev_vlm_panel_map_verify.py --prefer mlx --synthetic --require-vlm
# 受入スクリプト（Linux GPU）:
#   python scripts/dev_legend_json_mode_verify.py --prefer cuda
#   python scripts/dev_vlm_panel_map_verify.py --prefer cuda --profile qwen2.5-vl-7b --synthetic --require-vlm
```

- `--legend-llm auto`（既定・WebUI も同じ）: 各 Figure の Legend に書かれた n（`n = …`、`N independent experiments` 等）を規則が全部拾えていれば LLM を呼ばず、モデルも読み込みません。拾えていない n があれば、Legend 中の n に印を付けて LLM に渡し、規則が読み残した値だけをどのパネル・群のものか割り当てさせます（n の値は本文から取るので LLM が値を作ることはありません）。
- Text LLM の既定は Qwen2.5-32B。7B より n の割り当て精度が高い（開発評価で約 0.83 対 0.6）代わりに、Mac（MLX 4-bit）では **GPU 側のメモリを約 18.5GB** 使います（実測。照合全体のピークは約 27GB）。32GB 以上の Apple Silicon を推奨。メモリが足りない機種（Mac は搭載 32GB 未満、CUDA は GPU メモリ 64GB 未満。しきい値は `model_registry.yaml` の `recommended_vram_gb`）では既定が自動で 7B（`qwen2.5-7b-mlx` / `qwen2.5-7b-hf`）になります（割り当て精度は下がります）。`--llm-profile` か `PRE_PEER_CHECKER_LLM_PROFILE` で上書き可。`--legend-llm on` で全 Figure を 32B で読むと 1 Figure 約 2〜3 分かかります（M 系 Mac・64GB での実測）。
- `--legend-llm-prefer auto`: Mac → MLX、PyTorch から GPU（NVIDIA CUDA / AMD ROCm / Intel XPU）を利用可 → transformers（GPU）。MLX 既定プロファイルでも GPU ホストでは HF にフォールバックします。GPU の判定は `pre_peer_checker/accel.py` に集約しています（LLM / VLM / DINOv2 / LightGlue 共通）。
- `PRE_PEER_CHECKER_DEVICE=cuda|xpu|mps|cpu` で演算デバイスを強制できます。
- `PRE_PEER_CHECKER_GPU_MAX_MEMORY_GB=8` のように指定すると、LLM / VLM が使う GPU メモリを上限までに抑え、残りを PC のメモリに置きます。大きな GPU 上で GPU メモリの少ない環境を再現する検証にも使えます。
- DGX Spark では `TORCH_DISABLE_NATIVE_JIT=1` をバックエンドが自動設定します（Triton 再ビルド不要）。

WebUI / GUI を直接起動する場合:

```bash
pip install -e ".[web]" && pre-peer-checker-web   # → http://127.0.0.1:8765
pip install -e ".[gui]" && pre-peer-checker-gui   # PyQt GUI（任意・開発用）
```

ローカルに保存されるもの（すべて git 外・共有なし）:

- **過去論文コーパス**: `cache/past_papers/`（WebUI 照合タブ / `imaging.past_paper_ingest`。図を抽出し、照合時に選んだ論文だけを画像再利用チェックに使用。CLI の `--corpus` と同じエンジン経路）
- **参考文献 PDF**: `cache/cited_papers/`（WebUI 照合タブ「2b」 / `parsers.cited_paper_ingest`。過去論文コーパスとは別の置き場。同じ内容の PDF は再利用）
- **PubPeer カタログ（ネタ帳）**: WebUI カタログ更新タブ / `catalog.pubpeer_ingest` / `catalog.share`（抽象ルールのみ共有可）



### カタログ更新タブ

WebUI の **カタログ更新** タブでは次ができます（PubPeer サイトの自動取得はしません）。

1. ブラウザで保存した PubPeer PDF を D&D → `cache/` のみに取込・抽象ルール抽出
2. RW 等の公式ソース pull → ローカル LLM 仕分け → 人手キュレーション → Apply（照合用アクティブカタログ）
3. 抽象ルール案を **GitHub Issue 下書き URL**（Web Intent・トークン不要）で開くか、**PR 用 CLI** をコピー（自動 push しない。PDF・コメント全文は含めない）

```bash
python -m pre_peer_checker.catalog --help
# 例: pulse / pull-rw / mine / propose / export-yaml
```



### 参考文献メタ＋引用整合チェックの仕組み

引用先論文の中身は **ユーザーが渡した PDF だけ** を使い、PubMed や Crossref などへのネットワーク問い合わせはしません。

1. **原稿だけで行うチェック（常時）** — 原稿（Word、無ければ PDF）の `References` 節と、本文中の引用（`[1]`、`[1,2]`、`[3–5]`、`(Smith et al., 2020)`。PDF の上付き番号も含む）を突き合わせます。
2. **引用先 PDF を渡したときのチェック（任意）** — 参考文献の各エントリを PDF に紐付け（DOI 完全一致 → タイトル＋年 → ファイル名の順）、メタデータと本文を照合します。


| pattern_id                  | 内容                                      | 必要な入力        | 出力                           |
| --------------------------- | --------------------------------------- | ------------ | ---------------------------- |
| `P-REF-MISSING-ENTRY`       | 本文で引用しているキーが References に無い             | 原稿           | Warning                      |
| `P-REF-DUPLICATE-KEY`       | References に同じ番号・キーが重複している              | 原稿           | Warning                      |
| `P-REF-META-INCONSISTENT`   | 1 つのエントリ内で年が食い違う、DOI はあるのに著者もタイトルも読めない  | 原稿           | Warning                      |
| `P-REF-ORPHAN-ENTRY`        | References にあるが本文で一度も引用されていない           | 原稿           | カバレッジに情報表示（既定で Warning にしない） |
| `P-REF-PDF-META-MISMATCH`   | 参考文献の DOI・年・タイトルが、紐付いた PDF のものと食い違う     | 原稿 + 引用先 PDF | Warning                      |
| `P-REF-CLAIM-CONTRADICTION` | 引用文の主張（数値・増加／減少）が、引用先 PDF の該当箇所と明確に矛盾する | 原稿 + 引用先 PDF | Warning                      |


- 本文の引用を含む文を「主張」として取り出し、その引用キーに紐付いた PDF の中だけから関連箇所を検索します（BM25）。別の論文の文章を根拠に取り違えないよう、PDF を横断検索はしません。
- 見つかった関連箇所は HTML レポートの **「引用根拠レビュー（情報）」カード** に主張の文と並べて表示します。人が読んで判断するための材料で、Warning ではありません。
- Warning になるのは、「本文は 50% 増加、PDF は 10% 減少」のように数値や増減の向きが機械的に矛盾すると判定できた場合だけです。

現時点の制限:

- 参考文献の読み取りは原稿の `References` 見出し以降が対象です。Word 原稿がある場合は Word を優先し、PDF 原稿は Word が無いときだけ読みます。
- 番号付き・author–year 形式の一般的な書式に対応した規則ベースの読み取りです。雑誌固有の特殊な書式では取りこぼすことがあります。
- 引用先 PDF のタイトル・年・DOI は先頭ページからの推定です。スキャン PDF などテキストが取れないものは紐付けできません。
- 紐付けできなかった参考文献は Warning にせず、カバレッジに「未紐付け」として件数を出します。



### Docker（開発・学習）

```bash
docker compose build
docker compose run --rm test
docker compose run --rm run verify /data/input -o /data/output/report.html
# GPU 学習スタブ:
docker compose --profile train build train
docker compose --profile train run --rm train
```

手順の詳細は [docs/DOCKER.md](docs/DOCKER.md)。

### レイアウト検証

```bash
./scripts/verify_packaging_layout.sh
```

`install.sh`・WebUI エントリ・パターン JSON の存在と import を確認します（ネットワーク不要。`.venv` は事前に必要）。

## ライセンス・第三者コンポーネント

本ソフトウェア（`pre-peer-checker`）自体のソースコードは **MIT**（[LICENSE](LICENSE)）です。依存パッケージとモデル重みはリポジトリに同梱せず、`install.sh` / `pip` / `torch.hub` を通じて利用者の環境へ各配布元から取得します。以下は無料配布・論文化を想定した 2026-09 時点の整理です（商用化時は別途再確認）。各パッケージ／モデルの正式条件は上流の LICENSE を正とし、本節は法的助言ではありません。

### PyMuPDF（AGPL-3.0）に関する条項

PDF のテキスト・図の抽出に使う **PyMuPDF（`fitz`）はコア依存**で、すべてのインストールに含まれます。PyMuPDF は **GNU AGPL-3.0 と Artifex 商用ライセンスのデュアルライセンス**です。

- 本リポジトリの MIT コードは AGPL-3.0 と両立します。**ソースを公開し、依存は利用者が各自取得する現行の配布方針**では、追加の対応は不要です。
- PyMuPDF を含めた**結合物を配布する場合**（PyInstaller の凍結バイナリ、`.app`、PyMuPDF をインストール済みの Docker イメージ、依存をまとめたアーカイブ等）は、結合物全体が AGPL-3.0 の条件を受けます。具体的には、対応するソースコードの提供、AGPL-3.0 全文の同梱、同一ライセンスでの再配布が必要です。
- PyMuPDF と結合した本ソフトを**改変し、WebUI 等でネットワーク越しに第三者へ提供する場合**は、AGPL-3.0 §13 により利用者へのソース提供義務が生じます（既定の `127.0.0.1` での個人ローカル利用は該当しません）。
- **クローズドソースまたは商用で配布・提供する場合**は、Artifex から商用ライセンスを取得するか、PDF 処理を permissive なライブラリ（例: pypdfium2 = Apache-2.0 / BSD-3-Clause、pypdf = BSD-3-Clause）へ置き換えてください。
- fork・改変して再配布する場合も上記の条件を引き継ぎます。リポジトリが MIT であることは、PyMuPDF を含む成果物をクローズドで配布できることを意味しません。



### 画像照合モデル（DINOv2 / LightGlue / SuperPoint）

- **DINOv2**: コード・標準重みとも Apache-2.0 で、MIT と両立します。実行時に `torch.hub.load("facebookresearch/dinov2", ...)` で取得し、同梱はしません。重みを再配布する場合は上流の LICENSE を添付してください。
- **LightGlue**: コードと学習済み重みは Apache-2.0 で、MIT と両立します。
- **SuperPoint / ALIKED（利用区分で切替）**: SuperPoint の推論コード（`lightglue/superpoint.py`）と重みは **Magic Leap の「大学・非営利組織による非商用研究」限定ライセンス**です。配布者が非商用でも、利用者が営利組織（企業の研究所・製薬企業・CRO など）であれば対象外になります。そこで `./install.sh` の最初に利用区分を選択し、LightGlue の特徴点抽出器を切り替えます。

  | 利用区分                       | 特徴点抽出器     | ライセンス                                  | 判定しきい値（一致点数） |
  | -------------------------- | ---------- | -------------------------------------- | ------------ |
  | 1) 大学・非営利組織による非商用研究        | SuperPoint | Magic Leap（非商用研究）                      | 35           |
  | 2) 上記以外（企業・商用研究・判断がつかない場合） | ALIKED     | BSD-3-Clause（LightGlue 重みは Apache-2.0） | 50           |

  - 選択はリポジトリ直下の `usage_profile.json`（git 管理外）に保存され、`./install.sh` の再実行で変更できます。環境変数 `PRE_PEER_CHECKER_USAGE=academic|commercial` で上書き・非対話実行も可能です。未選択・判別不能のときは 2）ALIKED 側に倒します。
  - 区分は利用者の自己申告であり、条件の遵守は利用者の責任です。SuperPoint のファイル自体は LightGlue パッケージに同梱されるため 2) でもディスク上には存在しますが、読み込み・重みの取得は行いません。
  - しきい値は合成ペアで抽出器ごとに較正しています（`fixtures/gold/lightglue_calib/calib_summary.json`）。ALIKED は 90° 回転した切り抜きを一致点数だけでは無関係ペアと分離できないため、回転を伴う再利用の確定力は SuperPoint より弱くなります。
- **LightGlue のバージョン固定**: `install.sh` は LightGlue をコミット `eb42fee`（`LIGHTGLUE_COMMIT`）に固定して導入します。更新する場合は上流の LICENSE を確認し、`scripts/dev_lightglue_threshold_calib.py --features {superpoint,aliked}` で再較正してください。
- LightGlue は任意機能です。未導入の場合、精密照合は OpenCV ORB + RANSAC（Apache-2.0）→ 正規化相互相関（追加依存なし）へフォールバックします。



### ローカルモデル（任意・補助）


| 役割                    | モデル                                                                        | ライセンス                                                                 | 備考                               |
| --------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------- |
| Legend→JSON 等（既定）     | Qwen2.5-32B-Instruct（Mac は MLX 4-bit: `mlx-community/Qwen2.5-32B-Instruct-4bit`） | Apache 2.0                                                            | Alibaba Cloud / Qwen             |
| Legend→JSON 等（軽量）     | Qwen2.5-7B-Instruct（MLX 4-bit: `mlx-community/Qwen2.5-7B-Instruct-4bit`） | Apache 2.0                                                            | Alibaba Cloud / Qwen             |
| Fig 接地（配布本命）          | Qwen2.5-VL-7B-Instruct（mlx-vlm）                                            | Apache 2.0                                                            | パネル境界・軸ラベル・グラフ種別                 |
| Fig 接地（開発・教師）         | Qwen2.5-VL-32B-Instruct                                                    | Apache 2.0                                                            | DGX ゴールド・7B 蒸留／LoRA 用            |
| 画像類似スクリーニング           | DINOv2（例: `dinov2_vits14`）                                                 | Apache 2.0                                                            | Meta。標準重み。派生チェックポイントは別ライセンスの場合あり |
| 画像ペア精密照合（任意）          | LightGlue + SuperPoint または ALIKED                                          | LightGlue: Apache 2.0 / SuperPoint: Magic Leap（非商用研究） / ALIKED: BSD-3 | install.sh の利用区分で切替（上記）          |


モデルプロファイル（`llm/model_registry.yaml`）には Apache-2.0 のモデルだけを載せています。Qwen2.5-3B（Qwen Research License）・Qwen2.5-72B（Qwen License）・InternVL3 は選択肢から外しました。

推論ランタイム: **MLX / mlx-lm**（MIT、Apple Silicon）、**PyTorch + transformers**（BSD / Apache、CUDA 経路）。

### コア依存（常時）


| パッケージ                             | 用途             | 代表的ライセンス                                          |
| --------------------------------- | -------------- | ------------------------------------------------- |
| numpy, pandas, scipy, statsmodels | 数値・統計再計算       | BSD 系                                             |
| openpyxl                          | Excel          | MIT                                               |
| ruamel.yaml                       | YAML           | MIT                                               |
| python-docx                       | Word           | MIT                                               |
| **PyMuPDF**                       | PDF テキスト・埋め込み図 | **AGPL-3.0**（または Artifex 商用）。上記「PyMuPDF に関する条項」参照 |
| Pillow                            | 画像 I/O         | MIT-CMU（HPND 系）                                   |
| Jinja2                            | HTML レポート      | BSD-3-Clause                                      |
| nbformat                          | Jupyter        | BSD-3-Clause                                      |




### オプショナル依存（`pyproject.toml` extras）


| Extra      | 主なパッケージ                                 | 用途             | 代表的ライセンス                   |
| ---------- | --------------------------------------- | -------------- | -------------------------- |
| `web`      | FastAPI, uvicorn                        | ローカル WebUI     | MIT / BSD                  |
| `gui`      | PyQt6                                   | 任意 GUI         | **GPL-3.0** / Riverbank 商用 |
| `imaging`  | torch, torchvision, OpenCV, aicsimageio | 重複スキャン・顕微鏡     | BSD / Apache 等（各上流）        |
| `imaging`  | **readlif**                             | Leica LIF 読込   | **GPL-3.0**                |
| `imaging`  | **pylibCZIrw**                          | Zeiss CZI 読込   | **LGPL-3.0**               |
| `r-ast`    | tree-sitter, tree-sitter-language-pack  | R AST          | MIT                        |
| `mlx`      | mlx, mlx-lm                             | Mac LLM        | MIT                        |
| `llm-cuda` | torch, transformers, accelerate         | Linux/CUDA LLM | BSD / Apache               |
| `dev`      | pytest, ruff, httpx                     | 開発             | MIT                        |


LightGlue は PyPI に無いため、`install.sh` が上流のコミット固定 zip から導入します（Apple Silicon / Linux）。

### 配布・論文での扱い（現状方針）


| 配布形態                               | 主な制約                                                                                               |
| ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| ソース公開（clone → `./install.sh`、現行本線） | 追加義務なし。依存とモデルは利用者が各自取得                                                                             |
| 凍結バイナリ・Docker イメージ等、依存込みの結合物を配布    | PyMuPDF により全体が AGPL-3.0。readlif・PyQt6 を含めば GPL-3.0、pylibCZIrw は LGPL-3.0（差し替え可能性の確保）。各 LICENSE を同梱 |
| クローズドソース・商用                        | PyMuPDF の商用ライセンス取得または置換、SuperPoint 不使用（ALIKED 固定）、readlif・PyQt6 の除外または置換                           |


- **無料配布**: ソース公開（clone → `./install.sh`）を本線とし、利用者が依存とモデルを各自取得する形を推奨。
- **論文化**: Methods / Acknowledgments にモデル名・主要ライブラリとライセンス（および必要なら論文引用）を記載。
- **商用・クローズド配布**を将来検討する場合は、とくに PyMuPDF（AGPL）、SuperPoint（非商用）、readlif / PyQt6（GPL）を再点検してください。

