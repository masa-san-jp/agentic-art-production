# Agentic Art Production

`agentic-art-research`が生成した制作仮説・要件・Prototype Planを受け取り、制作仕様、工程、資源、予算、試作、本制作、設営、受入、結果還流までを追跡可能にする制作基盤です。

## 利用者向けの最短ルート

このリポジトリは、検証済みのResearch handoffから制作計画と結果の記録を組み立てるruntimeです。実作品のassetを保存したり、購入・契約・物理作業を自動実行したりはしません。

| したいこと | 入口 |
| --- | --- |
| 新しい制作プロジェクトを作る | [`tools/new_production.py`](tools/new_production.py) と [`docs/agent-startup.md`](docs/agent-startup.md) |
| 制作計画・試作・実行を確認する | [`docs/operations-runbook.md`](docs/operations-runbook.md)、[`docs/schema-reference.md`](docs/schema-reference.md) |
| Researchへ結果を返す | [`tools/build_result.py`](tools/build_result.py)、[`tools/export_result.py`](tools/export_result.py) |
| エージェントとして作業する | [`AGENTS.md`](AGENTS.md)、[`execution/task-queue.yaml`](execution/task-queue.yaml) |

実プロジェクトとasset本体は明示したGit外output rootに置き、GitにはURI・版・SHA-256・権利区分などの追跡情報だけを残します。権利・安全・外部検証が未確認のものはgapとして保持します。

設計硬化、handoff受理、計画、試作管理、replay可能なruntime、task lease/retry/effect/approval gate、出力版・品質・設営の追跡台帳、versioned production-resultの生成・export、代表E2E/security/chaos評価まで実装済みです。実作品や外部効果はprotocol repositoryへ保存・実行せず、Git外output rootのproject記録だけを更新します。実装エージェントは次の順で読みます。

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
  --generated-at 2026-08-12T18:00:00+09:00
.venv/bin/python tools/export_result.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke" \
  --output "$AAP_BOOTSTRAP_ROOT/results/smoke/PR001"
.venv/bin/python tools/run_evaluation.py --format text
```
