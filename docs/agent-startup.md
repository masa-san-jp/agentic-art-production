# Agent startup guide

このrepoは、実作品や外部効果を保存・実行する場所ではなく、Agentic Art Productionのprotocol、schema、validator、test、synthetic fixtureを管理する正本である。実projectはGit外のoutput rootへ生成する。

## 最初に読むもの

会話履歴を前提にせず、次の順で読む。

1. `AGENTS.md`
2. `docs/20260811-agentic-art-production-system-design-specification.md`
3. `docs/20260811-agentic-art-production-implementation-contract-specification.md`
4. `docs/20260811-agentic-art-production-repository-execution-plan.md`
5. `PLANS.md`
6. `execution/task-queue.yaml`
7. `docs/operations-runbook.md`
8. `docs/schema-reference.md`

`AGENTS.md`のoutput境界、安全規則、task選択規則を優先する。研究repoのworking treeや、Git外projectの私的原文を直接参照してはならない。

## 開始前のpreflight

```bash
git status -sb
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/validate.py --check --format json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format text
```

既存の`.venv`は再利用してよい。検証に失敗した場合は、最初に診断の`rule`、`file`、`location`、`reason`、`remediation`を読み、状態を手編集で補正しない。

## taskの選択と変更

`execution/task-queue.yaml`で依存関係が完了した最小IDの`READY` taskを一件選ぶ。開始時に`IN_PROGRESS`、完了時に`DONE`へ更新し、実行計画のProgress、Discoveries、Decision Log、task handoffも同じ変更で更新する。

変更は`agent/<task-description>`ブランチで行う。正本のschema、config、CLI、testを変更した場合は、正常系・失敗系・再実行系を追加し、Git外の実projectや生成resultをcommitしない。

## projectを作る最小手順

以下は合成handoffからGit外projectを作るoffline手順である。`OUTPUT_ROOT`はrepo外の専用ディレクトリに置く。

```bash
OUTPUT_ROOT="$(mktemp -d /tmp/agentic-art-production.XXXXXX)"
PROJECT_ROOT="$OUTPUT_ROOT/production/smoke"

.venv/bin/python tools/new_production.py smoke \
  --handoff tests/fixtures/handoff/minimal \
  --output-root "$OUTPUT_ROOT"
.venv/bin/python tools/validate.py --project-root "$PROJECT_ROOT"
.venv/bin/python tools/build_plan.py --project-root "$PROJECT_ROOT"
.venv/bin/python tools/build_prototype.py --project-root "$PROJECT_ROOT"
.venv/bin/python tools/validate.py --project-root "$PROJECT_ROOT"
```

`build_plan.py`が表示する`03_plan/production-plan.md`が、制作担当者へ渡す唯一の統合制作計画書である。`03_plan/production-plan.yaml`、分割register、`agent-contexts/`は検証・再生成・内部運用のために保持し、人間向け成果物として別々に渡さない。既存の`human-brief.md`を含むprojectは、上書きせず退避してから再生成する。

runtimeを開始するときは、時刻とactorを明示してreplay可能にする。

```bash
.venv/bin/python tools/run_runtime.py \
  --project-root "$PROJECT_ROOT" bootstrap \
  --occurred-at 2026-08-12T18:00:00+09:00 \
  --actor-kind SYSTEM --actor-id startup/local
.venv/bin/python tools/run_runtime.py \
  --project-root "$PROJECT_ROOT" init-tasks \
  --occurred-at 2026-08-12T18:00:01+09:00 \
  --actor-kind SYSTEM --actor-id startup/local
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" replay
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" init
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" replay
```

runtimeの`run-log.jsonl`とexecutionの`production-log.jsonl`が追記型の正本であり、state/register YAMLはreplay projectionである。projectionを直接編集せず、破損時は[operations-runbook.md](operations-runbook.md)の復旧手順に従う。

## resultを作る・渡す

制作実行が完了していない場合も、未実施は`NOT_RUN`、外部確認待ちは`EXTERNAL_VALIDATION_REQUIRED`として記録する。成功へ補正しない。

```bash
.venv/bin/python tools/build_result.py \
  --project-root "$PROJECT_ROOT" \
  --result-id PR001 \
  --generated-at 2026-08-12T18:00:00+09:00 \
  --production-commit "$(git rev-parse HEAD)"
.venv/bin/python tools/export_result.py \
  --project-root "$PROJECT_ROOT" \
  --output "$OUTPUT_ROOT/results/smoke/PR001"
```

export bundleに入るのは`manifest.yaml`と`production-result.yaml`だけである。asset本体、`PRIVATE_RAW`、credential、signed URL、個人情報、契約書原本は含めない。

## 終了前のhandoff

```bash
.venv/bin/python tools/validate.py --check --format json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format json
git diff --check
git status -sb
```

最後に、変更ファイル、実行コマンドと結果、Git外へ生成したproject/resultの場所、未解決gap、次の`READY` taskを実行計画へ残す。外部effect、公開、購入、契約、削除、物理作業は人間承認なしに実行しない。
