# Agentic Art Production

Production #60 adds the [public plan attestation](docs/public-plan-attestation.md)
CLI. It verifies complete canonical bytes and explicit hash-bound publication
review without publishing, granting physical approval, or changing the plan.

`agentic-art-research`が生成した制作仮説・要件・Prototype Planを受け取り、制作仕様、工程、資源、予算、試作、本制作、設営、受入、結果還流までを追跡可能にする制作基盤です。

設計硬化、handoff受理、計画、試作管理、replay可能なruntime、task lease/retry/effect/approval gate、外部・物理証跡と制作観察のappend-only ingest、出力版・品質・設営の追跡台帳、versioned production-resultの生成・export、代表E2E/security/chaos評価まで実装済みです。制作観察は明示的に記録された最新ACTIVE revisionだけをlosslessに結果へ返し、観察0件は空配列として扱います。実作品や外部効果はprotocol repositoryへ保存・実行せず、Git外output rootのproject記録だけを更新します。実装エージェントは次の順で読みます。

制作計画の生成時には、受理済みhandoffからGit外output rootへ決定論的なビジュアルリファレンスボードと`CONCEPTUAL`モックアップを追加します。`03_plan/production-plan.md`には両者の相対リンク、asset hash、権利・安全状態を掲載します。これらは引用専用・合成fixtureであり、外部素材の採用、物理制作、外部検証、公開、購入、契約、Drive共有を実施した記録ではありません。

## Agentic Art全体との関係と利用方法

8リポジトリ全体の人間向け案内は、親repoの[repository map](https://github.com/masa-san-jp/agentic-art-orchestration/blob/main/docs/repository-map.md)を正本とします。このREADMEにも、役割と利用入口を次の通り表示します。

```text
self-model-notes ─┐
art-history-notes ├─ normalized research signal ─┐
marketing-trends ┘                               │
                                                 ▼
viewer-response-notes ─ feedback ─→ agentic-art-orchestration
                                                 │
                                                 ▼
                                       agentic-art-research
                                                 │ production-handoff
                                                 ▼
                                       agentic-art-production
                                                 │ canonical plan
                                                 ▼
                                       agentic-art-project
                                           公開カタログ
```

| リポジトリ | 役割 |
|---|---|
| [agentic-art-orchestration](https://github.com/masa-san-jp/agentic-art-orchestration) | 全体のcontrol plane。workspace、pin、retrieval、実行、再開、検証 |
| [self-model-notes](https://github.com/masa-san-jp/self-model-notes) | 本人の明示的・同意済みの自己モデル |
| [art-history-notes](https://github.com/masa-san-jp/art-history-notes) | 美術史上の作品、技法、関係、根拠 |
| [marketing-trends-notes](https://github.com/masa-san-jp/marketing-trends-notes) | 社会・市場の変化と鮮度付きの根拠 |
| [agentic-art-research](https://github.com/masa-san-jp/agentic-art-research) | 入力知識を使った調査、仮説、要件、判断 |
| [agentic-art-production](https://github.com/masa-san-jp/agentic-art-production) | handoffを受けた制作プラン、試作、制作結果 |
| [viewer-response-notes](https://github.com/masa-san-jp/viewer-response-notes) | 鑑賞者反応の集計と保守的な評価 |
| [agentic-art-project](https://github.com/masa-san-jp/agentic-art-project) | 検証済みの公開プラン、作品、制作記録のカタログ |

### 利用者の入口

- 制作を始める: [OrchestrationのREADME](https://github.com/masa-san-jp/agentic-art-orchestration#利用者向けの最短ルート)と[agent-runtime-guide](https://github.com/masa-san-jp/agentic-art-orchestration/blob/main/docs/agent-runtime-guide.md)から始める。個別repoを順番に手操作しない。
- 知識を更新する: 更新対象repoのREADME、Issue、schema、validatorを正本として使い、親へ本文をコピーしない。
- Research/Productionを確認する: [agentic-art-research](https://github.com/masa-san-jp/agentic-art-research)と[agentic-art-production](https://github.com/masa-san-jp/agentic-art-production)の各入口を読む。
- 公開プランや作品を見る: [agentic-art-projectのplans/とworks/](https://github.com/masa-san-jp/agentic-art-project)を開く。
- 鑑賞者反応を戻す: [viewer-response-notes](https://github.com/masa-san-jp/viewer-response-notes)で集計し、次回Researchで再検証する。

各repoは独立した正本を持ち、内部log、会話、prompt、credential、PRIVATE_RAW、RESTRICTEDを兄弟repoへ渡しません。

### 根底にある問い

Agentic Artは、生成AIを「古代の芸術家に霊感を与えた精霊のような存在なのか、人間の思考の延長に過ぎないのか」という問いへの態度として、芸術の契機を「精霊や風が運び、人間が受け取って具象化する」と捉えています。全体の背景は[`agentic-art-orchestration`のREADME](https://github.com/masa-san-jp/agentic-art-orchestration#根底にある問い)を正本とし、この姿勢は前身プロジェクト「Vibe Art」（2025年）から受け継がれています。

### このrepoの使い方

このrepoはResearchのhandoffを受理し、制作プラン、試作、実行、品質結果、production resultを管理します。canonical planはOrchestrationが検証してProjectへexport-only投影します。プラン生成は物理制作、購入、契約、公開を実施したことを意味しません。


このrepoが管理するのは、次の正本です。

- handoff、schema、config、template
- Git外のproduction projectを生成・検証するCLI
- append-only logとreplay可能なruntime / execution projection
- 最小権限・proposal-onlyのagent harness
- evidence、observation、production result、export manifest
- validator、offline EVAL、security / chaos test、resumable release gate

`CONTRACT-001`は実プロジェクト`harmony-study`のREADY handoff/export bundleを受理し、完了しました。同一handoffの冪等再受理、research handoff schema snapshot、Production-ownedの`schemas/production-result.schema.json` v1、registry hash、bundle内common schemaのoffline参照解決が確定しています。受理済みprojectはGit外output rootの`production/harmony-study`です。`PLANNING-SCHEMA-001`と`PLANNING-BUILD-001`では、受理済みhandoffから`PL001`のscope、仕様、WBS、資源、予算、日程、risk、approval requirement、coverage、内部canonical YAMLを決定的に生成できます。`PLANNING-DOCUMENT-001`では、それらを人間が読んで制作するための唯一の受け渡し成果物`03_plan/production-plan.md`へ統合します。受理時に構造化`production-brief.yaml`の完成像・テーマ・メッセージ・コンセプトを検証し、計画書の冒頭4節を表として描画します。制作プランには、handoffのsource-ref indexからコンセプト・ビジュアル・手法などの恒久HTTPS参照URLを掲載します。必須カテゴリのURL不足、brief不足、先行作品未調査、`MERELY_PLAINER`はblocking gap、query・credential・fragment付きURLは生成エラーです。構造化YAMLとagent contextは検証・再生成用に保持しますが、ユーザーへ渡す制作プランは統合Markdown一つです。`PROTOTYPE-001`では、物理実行なしに`PC001`の試作run、test、review、iteration、change-controlの記録形式とfail-closed検証を生成できます。`RUNTIME-001`では、`EVT000001`からのappend-only event log、state replay、BLOCKED resume、改ざん検出を検証できます。`RUNTIME-002`では、`EVT000002`のtask graph登録、決定的task選択、lease heartbeat/recovery、TRANSIENT retry、target hash付きapproval、effect冪等性を検証できます。`EXECUTION-001`では、`EXE000001`以降のappend-only execution logと、output version・quality・installation projectionを追加し、asset本体を保存せずにURI・版・SHA-256・権利・外部検証状態を追跡できます。`FEEDBACK-001`では、これらの投影から`production-result.yaml`を決定的に生成し、結果本体とmanifestだけのGit外bundleへexportできます。

Viewer response integration is aggregate-only. An explicit `viewer_response` DTO may be carried from a prototype test into `production-result/v1`; counts must reconcile, external evidence has zero measured sample, and no free text, identifiers, diagnoses, raw assets, or credentials are accepted. Use `tools/build_plan.py --viewer-assessment` to display a validated assessment and keep blind/frame review as a blocking requirement for conservative statuses.

## 兄弟リポジトリと責任分界

このrepoは単独の作品保管庫ではなく、ResearchからProductionへの版固定された境界を担当します。各repoは自分のschema・Issue・データの正本を持ち、他repoのworking treeを直接読みません。

8リポジトリの全体図と、各repoの所有範囲・受け渡しは、親repoの [repository map](https://github.com/masa-san-jp/agentic-art-orchestration/blob/main/docs/repository-map.md) を参照してください。このREADMEでは、Productionから見た接続と、Production固有の実行方法を説明します。

```text
入力KB群 ──> agentic-art-orchestration ──> agentic-art-research
             (横断control plane)            (仮説・要件・handoff)
                                                   │ 固定bundle
                                                   v
                                          agentic-art-production
                                          (計画・実行・result)
                                                   │ result bundle / evidence候補
                                                   └──────────────> researchへ還流
```

| リポジトリ | 役割 | 当repoとの接続 |
| --- | --- | --- |
| [agentic-art-orchestration](https://github.com/masa-san-jp/agentic-art-orchestration) | 複数repoを横断するcontrol plane。repo pin、依存関係、workspace、監査、横断qualificationを管理する | 当repoの内部schemaやproject dataを所有・複製せず、repo/commitと境界契約を参照する |
| [agentic-art-research](https://github.com/masa-san-jp/agentic-art-research) | 証拠・主張・インサイトから制作判断、制作仮説、Prototype Plan、Production Handoffを作る上流repo | 当repoは固定commit・schema hash・manifest付きhandoff bundleだけを受理し、結果bundleを証拠候補として返す |
| [self-model-notes](https://github.com/masa-san-jp/self-model-notes) | orchestrationが扱うSelf Model入力KB | 当repoへ直接handoffしない。必要な研究成果はresearch側で正規化する |
| [art-history-notes](https://github.com/masa-san-jp/art-history-notes) | orchestrationが扱う芸術史入力KB | 当repoへ直接handoffしない。必要な研究成果はresearch側で正規化する |
| [marketing-trends-notes](https://github.com/masa-san-jp/marketing-trends-notes) | orchestrationが扱うマーケティング変化入力KB | 当repoへ直接handoffしない。必要な研究成果はresearch側で正規化する |
| [viewer-response-notes](https://github.com/masa-san-jp/viewer-response-notes) | viewer反応の集計と制作要件評価を扱うrepo | Productionのresultを直接の作業入力にせず、定義済みのresult / feedback境界で扱う |
| [agentic-art-project](https://github.com/masa-san-jp/agentic-art-project) | 公開制作プラン、作品、制作記録のカタログ | Productionから直接公開せず、canonical planをOrchestration経由でProjectへ投影する |

利用者が最初に読む順番は、目的に応じて次のとおりです。

1. **調査・制作仮説を作る**: [agentic-art-research](https://github.com/masa-san-jp/agentic-art-research)
2. **handoffを受けて制作を始める**: このrepoの[Quick start](#quick-start-合成fixtureを動かす)
3. **複数repoのpin・横断状態を確認する**: [agentic-art-orchestration](https://github.com/masa-san-jp/agentic-art-orchestration)
4. **制作結果を還流する**: [`tools/export_result.py`](tools/export_result.py)とresult契約を確認する

## できること / できないこと

| できること | できないこと・自動では行わないこと |
| --- | --- |
| 固定されたhandoff bundleをmanifest・schema hash・commitとともに受理する | 隣接するresearch working treeを直接読む |
| handoffから計画、prototype control、runtime/execution台帳を生成する | 完成作品、RAW、動画、音声、3D asset本体をGitへ保存する |
| workerの提案をlease・context・policy・schemaで検証し、replayする | workerがproduction lifecycleや外部effectを直接変更する |
| evidence / observation metadataをappend-onlyで取り込む | 購入、契約、支払い、公開、送信、削除、物理作業を無承認で実行する |
| `production-result.yaml`と最小export bundleを決定的に生成する | 未実施・外部確認待ちを成功状態へ補正する |

外部・物理effectは、人間承認と外部evidenceが揃うまで`WAITING_APPROVAL`、`BLOCKED`、`NOT_RUN`、または`EXTERNAL_VALIDATION_REQUIRED`として扱います。

## Quick start: 合成fixtureを動かす

前提はPython 3.11以上です。依存はrepository-local virtual environmentへ入れます。

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

最小handoffから、repo外にprojectを作り、計画とprototype controlを生成・検証します。

```bash
OUTPUT_ROOT="$(mktemp -d /tmp/agentic-art-production.XXXXXX)"
PROJECT_ROOT="$OUTPUT_ROOT/production/smoke"

.venv/bin/python tools/new_production.py smoke \
  --handoff tests/fixtures/handoff/minimal \
  --output-root "$OUTPUT_ROOT"
.venv/bin/python tools/validate.py \
  --project-root "$PROJECT_ROOT" --format json
.venv/bin/python tools/build_plan.py \
  --project-root "$PROJECT_ROOT"
.venv/bin/python tools/build_prototype.py \
  --project-root "$PROJECT_ROOT"
.venv/bin/python tools/validate.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
.venv/bin/python tools/run_runtime.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" bootstrap \
  --occurred-at 2026-08-12T18:00:00+09:00 \
  --actor-kind SYSTEM --actor-id startup/local
.venv/bin/python tools/run_runtime.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" init-tasks \
  --occurred-at 2026-08-12T18:00:01+09:00 \
  --actor-kind SYSTEM --actor-id startup/local
.venv/bin/python tools/run_runtime.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" replay
.venv/bin/python tools/run_execution.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" init
.venv/bin/python tools/run_execution.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" replay
.venv/bin/python tools/build_result.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" \
  --result-id PR001 \
  --generated-at 2026-08-12T18:00:00+09:00 \
  --target-state BLOCKED
.venv/bin/python tools/export_result.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" \
  --output "$AAP_BOOTSTRAP_ROOT/results/smoke/PR001"
.venv/bin/python tools/run_evaluation.py --format text
```

### 制作内容の受入（AAK-10）

要素ルート（research の段階 C）から受け取った handoff では、method を[段階 D の要素 CLI](docs/plan-elements.md)（`tools/plan_elements.py next|answer`）で1要素ずつ作る。CLI が method を書き、計画・試作・判定まで実行する。
外部エージェントは受理済みhandoffから[Production-owned method](docs/plan-actionability.md)を外部projectへ記録し、`build_plan.py`、`plan_actionability.py --project-root ...`の順に実行する。後者は媒体に必要な仕様・最初の制作作業・物・条件と実ファイルを検証する。`PLAN_READY`は内容の充足であり、制作・購入・設営の実施や承認ではない。必須の未確定値や手順不足を成功JSONで代用しない。

### 制作知識の保存・次回利用（AAK-11）

[Production memory](docs/production-memory.md)は正規観察・resultの検証済み選択を、明示したowner Gitへ保存する。計画・simulation・試作・実測を区別し、次のplanでは設備・技能・サイズ・安全・通貨／時点を照合して採否を記録する。採用は実際の提案工程へ反映し、訂正・撤回時には影響するplanを再検証へ戻す。観察0件から実績を生成せず、知識還流待ちを制作プランの未完了と混同しない。

## Native public-plan review (Issue 69)

`tools/public_plan_review.py --project-root <external-project>` prepares a `public-plan-review-packet/v1` without granting consent or publishing. It binds aggregate/body/assets to a native approval target `public-plan-review/<plan-id>` and SHA-256. Existing native runtime approvals are replayed and checked for HUMAN authority, exact PUBLICATION review scope, validity window, latest revision and three explicit constraints: `public-plan-review:content_safety=PASSED`, `public-plan-review:rights=PASSED`, `public-plan-review:consent=PASSED`. This scope reviews content only; it does not authorize the CLI to perform a remote publication. Runtime approval recording remains a trusted caller boundary; arbitrary JSON is not authenticated human consent.

The packet lists missing decisions and preserves the ready plan. Do not ask for a new approval when a valid recorded one already covers the target. Use `tools/public_plan_attestation.py --project-root <external-project> --native-review --producer-commit <qualified-commit> --generated-at <timestamp>` to attest the prepared native review. On delivery, `--check --require-native-review` rechecks current approval validity/revocation. Plain `--review` and `--check` retain legacy byte-verification semantics for existing records; they are not evidence of a current native approval and cannot qualify the new strict delivery lane. Review packets/runtime logs remain external; only opaque approval ID/revision references enter public attestations.
