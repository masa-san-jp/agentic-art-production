# Agentic Art Production

研究側のhandoffを受け取り、制作計画・試作・実行記録・証跡・結果exportまでを追跡可能にする、offline-firstのprotocol repositoryとagent execution harnessです。

## まず結論

このrepoが管理するのは、次の正本です。

- handoff、schema、config、template
- Git外のproduction projectを生成・検証するCLI
- append-only logとreplay可能なruntime / execution projection
- 最小権限・proposal-onlyのagent harness
- evidence、observation、production result、export manifest
- validator、offline EVAL、security / chaos test、resumable release gate

このrepo自体に実作品やproduction projectを保存しません。project、result、asset metadataは、利用者が指定するGit外のoutput rootへ生成します。

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
  --project-root "$PROJECT_ROOT" --format json

echo "project: $PROJECT_ROOT"
```

成功すると、`$PROJECT_ROOT`の下にhandoff、plan、prototype controlが生成されます。`$OUTPUT_ROOT`はrepoの外にあるため、projectや生成resultがこのGit repositoryへ混入しません。実際のhandoffを使う場合も、隣接repoではなく、固定commit・schema hash・manifestを含むbundleまたはZIPを`--handoff`へ渡してください。

## 用途別の入口

| やりたいこと | 最初に使うもの | 次に読むもの |
| --- | --- | --- |
| 新しいprojectを受理する | `tools/new_production.py` | [`docs/agent-startup.md`](docs/agent-startup.md) |
| plan / prototypeを作る | `tools/build_plan.py` / `tools/build_prototype.py` | [`docs/operations-runbook.md`](docs/operations-runbook.md) |
| runtime taskをreplay可能に実行する | `tools/run_runtime.py` | [`docs/operations-runbook.md`](docs/operations-runbook.md) |
| agent workerを安全に動かす | `tools/run_agent_harness.py` | [`docs/agent-startup.md`](docs/agent-startup.md) |
| evidence / observation / resultを扱う | `tools/run_execution.py`, `tools/build_result.py`, `tools/export_result.py` | [`docs/operations-runbook.md`](docs/operations-runbook.md) |
| schemaと互換性を確認する | `tools/validate.py` | [`docs/schema-reference.md`](docs/schema-reference.md) |
| offline受入評価を実行する | `tools/run_evaluation.py` | [`docs/release-gate.md`](docs/release-gate.md) |

人間へ渡す制作計画は、project内の`03_plan/production-plan.md`です。`production-plan.yaml`、register、agent contextは再生成・検証用の機械正本であり、別の人間向け成果物として扱いません。

## agent harnessを使う

agent harnessは、1 runを1 task leaseとimmutable contextへ束縛し、workerの出力をaction proposalとして記録します。`start`、`step`、`run`、`resume`、`status`、`cancel`を提供しますが、workerの完了はproduction taskや外部effectの完了を意味しません。

projectを作った後の基本形は次のとおりです。時刻とrun IDは利用者側で固定して渡します。

```bash
.venv/bin/python tools/run_agent_harness.py start \
  --project-root "$PROJECT_ROOT" --run-id ARN000001 \
  --adapter-profile scripted-fake --started-at 2026-08-29T12:00:00+09:00
.venv/bin/python tools/run_agent_harness.py run \
  --project-root "$PROJECT_ROOT" --run-id ARN000001 \
  --occurred-at 2026-08-29T12:00:01+09:00
.venv/bin/python tools/run_agent_harness.py status \
  --project-root "$PROJECT_ROOT" --run-id ARN000001
```

実運用で必要なapproval、evidence、復旧、revision受理の引数は、上記のrunbookとstartup guideを参照してください。`--help`は各CLIの実装済み引数を確認する一次情報です。

## repositoryを検証する

通常のローカル検証は次の4つです。

```bash
.venv/bin/python tools/validate.py --check --format json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format json
git diff --check
```

GitHub ActionsではPython 3.11 / 3.12でvalidator、全テスト、offline evaluationを実行します。release gateは、公開やtag作成とは分離した、clean commit単位の手動検証です。

## release gateと中断からの再開

release gateは、同じclean commitに対してrepository validation、全テスト、EVAL、agent harness検査を既定3回連続で実行します。evidence/checkpointはrepo外に保存し、各check完了時にatomic更新します。

```bash
RELEASE_EVIDENCE_ROOT="$(mktemp -d /tmp/agentic-art-production-release.XXXXXX)"
.venv/bin/python tools/run_release_gate.py \
  --runs 3 \
  --evidence "$RELEASE_EVIDENCE_ROOT/release-gate.yaml" \
  --format text
```

途中で停止した場合は、同じcommit・同じrun数・同じevidence pathで再開します。完了済みcheckは繰り返しません。

```bash
.venv/bin/python tools/run_release_gate.py \
  --runs 3 --resume \
  --evidence "$RELEASE_EVIDENCE_ROOT/release-gate.yaml" \
  --format text
```

evidenceのcommit、run数、check順序が現在と違う場合、working treeがdirtyの場合、失敗済みcheckpointの場合は再開を拒否します。tag、公開、配布、外部通知はrelease gateが自動実行する範囲ではなく、人間承認が必要です。詳細は[`docs/release-gate.md`](docs/release-gate.md)を参照してください。

## よくあるつまずき

- `repository working tree is not clean`: 変更をcommitするか、変更内容を退避してからrelease gateを実行します。検証中にtreeが変わった場合も候補は成立しません。
- `output root`や`release checkpoint`のpath boundaryエラー: projectとevidenceをこのrepoの外へ移します。`mktemp -d /tmp/...`を使うと安全です。
- `release checkpoint already exists`: 同じ試行を続けるなら`--resume`、修正後の新しいclean commitでやり直すなら新しいevidence pathを使います。
- `validator`の失敗: JSON出力の`rule`、`file`、`location`、`reason`、`remediation`を先に確認します。projectionやYAMLを手編集して成功扱いにしません。
- `WAITING_APPROVAL` / `EXTERNAL_VALIDATION_REQUIRED`: agentやCLIの異常ではなく、設計された承認・外部確認境界です。人間の承認や外部evidenceが揃うまで状態を補正しません。

## repositoryの見取り図

```text
config/       語彙、policy、schema registry
schemas/      versioned contract
templates/    Git外projectの初期構造
tools/        受理、生成、runtime、execution、harness、検証CLI
tests/        unit / contract / synthetic E2E
docs/         startup、運用復旧、schema、release gate
execution/    task queueとExecPlanの正本
```

実装エージェントは[`AGENTS.md`](AGENTS.md)のread order、output boundary、安全規則、task選択規則に従います。計画の全体像は[`docs/20260811-agentic-art-production-repository-execution-plan.md`](docs/20260811-agentic-art-production-repository-execution-plan.md)にあります。

## 正確性・利用条件

- このREADMEのコマンドは、現在のCLI名とGit外output boundaryに合わせています。引数の詳細は各CLIの`--help`を優先してください。
- このrepoに実作品、private raw、credential、signed URL、契約書原本を追加しないでください。
- このcloneには`LICENSE`ファイルがないため、再配布条件は明示されていません。作品、素材、外部schemaの権利は各recordと提供元の条件を確認してください。
- このrepoの実装・offline評価がPASSしても、現実の会場、資材、見積、承認、外部effectが存在することを意味しません。
