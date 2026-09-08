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

### このrepoの使い方

このrepoはResearchのhandoffを受理し、制作プラン、試作、実行、品質結果、production resultを管理します。canonical planはOrchestrationが検証してProjectへexport-only投影します。プラン生成は物理制作、購入、契約、公開を実施したことを意味しません。


1. `AGENTS.md`
2. `docs/20260811-agentic-art-production-system-design-specification.md`
3. `docs/20260811-agentic-art-production-implementation-contract-specification.md`
4. `docs/20260811-agentic-art-production-repository-execution-plan.md`
5. `PLANS.md`
6. `execution/task-queue.yaml`
7. `docs/agent-startup.md`
8. `docs/operations-runbook.md`
9. `docs/schema-reference.md`
10. `docs/release-gate.md`

新しいエージェントの開始手順は[`docs/agent-startup.md`](docs/agent-startup.md)、通常運用と復旧は[`docs/operations-runbook.md`](docs/operations-runbook.md)、schemaの対応表と互換性規則は[`docs/schema-reference.md`](docs/schema-reference.md)、v1.0.0候補の検証は[`docs/release-gate.md`](docs/release-gate.md)を参照してください。

`CONTRACT-001`は実プロジェクト`harmony-study`のREADY handoff/export bundleを受理し、完了しました。同一handoffの冪等再受理、research handoff schema snapshot、Production-ownedの`schemas/production-result.schema.json` v1、registry hash、bundle内common schemaのoffline参照解決が確定しています。受理済みprojectはGit外output rootの`production/harmony-study`です。`PLANNING-SCHEMA-001`と`PLANNING-BUILD-001`では、受理済みhandoffから`PL001`のscope、仕様、WBS、資源、予算、日程、risk、approval requirement、coverage、内部canonical YAMLを決定的に生成できます。`PLANNING-DOCUMENT-001`では、それらを人間が読んで制作するための唯一の受け渡し成果物`03_plan/production-plan.md`へ統合します。受理時に構造化`production-brief.yaml`の完成像・テーマ・メッセージ・コンセプトを検証し、計画書の冒頭4節を表として描画します。制作プランには、handoffのsource-ref indexからコンセプト・ビジュアル・手法などの恒久HTTPS参照URLを掲載します。必須カテゴリのURL不足、brief不足、先行作品未調査、`MERELY_PLAINER`はblocking gap、query・credential・fragment付きURLは生成エラーです。構造化YAMLとagent contextは検証・再生成用に保持しますが、ユーザーへ渡す制作プランは統合Markdown一つです。`PROTOTYPE-001`では、物理実行なしに`PC001`の試作run、test、review、iteration、change-controlの記録形式とfail-closed検証を生成できます。`RUNTIME-001`では、`EVT000001`からのappend-only event log、state replay、BLOCKED resume、改ざん検出を検証できます。`RUNTIME-002`では、`EVT000002`のtask graph登録、決定的task選択、lease heartbeat/recovery、TRANSIENT retry、target hash付きapproval、effect冪等性を検証できます。`EXECUTION-001`では、`EXE000001`以降のappend-only execution logと、output version・quality・installation projectionを追加し、asset本体を保存せずにURI・版・SHA-256・権利・外部検証状態を追跡できます。`FEEDBACK-001`では、これらの投影から`production-result.yaml`を決定的に生成し、結果本体とmanifestだけのGit外bundleへexportできます。

Viewer response integration is aggregate-only. An explicit `viewer_response` DTO may be carried from a prototype test into `production-result/v1`; counts must reconcile, external evidence has zero measured sample, and no free text, identifiers, diagnoses, raw assets, or credentials are accepted. Use `tools/build_plan.py --viewer-assessment` to display a validated assessment and keep blind/frame review as a blocking requirement for conservative statuses.

## Local checks

システムPythonへ依存を追加せず、repository-local virtual environmentで実行します。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
# commit後のclean treeでRELEASE-001を実行
.venv/bin/python tools/run_release_gate.py --runs 3 --format text
```

最小handoff fixtureからの生成確認:

```bash
AAP_BOOTSTRAP_ROOT="$(mktemp -d /tmp/agentic-art-production-bootstrap.XXXXXX)"
.venv/bin/python tools/new_production.py smoke \
  --handoff tests/fixtures/handoff/minimal \
  --output-root "$AAP_BOOTSTRAP_ROOT"
.venv/bin/python tools/validate.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
.venv/bin/python tools/build_plan.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
.venv/bin/python tools/validate.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
.venv/bin/python tools/build_prototype.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
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

外部エージェントは受理済みhandoffから[Production-owned method](docs/plan-actionability.md)を外部projectへ記録し、`build_plan.py`、`plan_actionability.py --project-root ...`の順に実行する。後者は媒体に必要な仕様・最初の制作作業・物・条件と実ファイルを検証する。`PLAN_READY`は内容の充足であり、制作・購入・設営の実施や承認ではない。必須の未確定値や手順不足を成功JSONで代用しない。

### 制作知識の保存・次回利用（AAK-11）

[Production memory](docs/production-memory.md)は正規観察・resultの検証済み選択を、明示したowner Gitへ保存する。計画・simulation・試作・実測を区別し、次のplanでは設備・技能・サイズ・安全・通貨／時点を照合して採否を記録する。採用は実際の提案工程へ反映し、訂正・撤回時には影響するplanを再検証へ戻す。観察0件から実績を生成せず、知識還流待ちを制作プランの未完了と混同しない。
