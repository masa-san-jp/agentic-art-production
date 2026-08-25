# Operations and recovery runbook

このrunbookは、Git外のproduction projectを安全に運用し、失敗を診断して再開するための手順である。repoはprotocolの正本、projectのcanonical fileはproject側にあり、生成物は再生成可能なprojectionとして扱う。

## 境界と正本

```bash
REPOSITORY_ROOT="$(pwd)"
PROJECT_ROOT="/path/to/output-root/production/<project-id>"
```

`PROJECT_ROOT`は`REPOSITORY_ROOT`配下に置かない。次のファイルがsource of truthである。

- handoff、scope、specification、plan、prototypeは各canonical YAML/JSON。
- `08_runtime/run-log.jsonl`はruntimeのappend-only event log。`production-state.json`はreplay projection。
- `05_execution/evidence-log.jsonl`は外部・物理証跡メタデータのappend-only event log。`evidence-register.yaml`はreplay projectionであり、証跡本体は保存しない。
- `05_execution/production-log.jsonl`はoutput、quality、installation、observationのappend-only log。各register YAMLはprojectionであり、`observations.yaml`もこのlogから再生成する。
- `08_runtime/production-result.yaml`はresultのcanonical aggregate。export bundleは`manifest.yaml`と`production-result.yaml`だけである。

projectionを手編集して状態を直してはならない。再生成できる場合はcanonical sourceから再生成し、再生成できない破損は停止して診断を残す。

## 通常運用

### project、plan、prototype

```bash
.venv/bin/python tools/validate.py --project-root "$PROJECT_ROOT" --format json
.venv/bin/python tools/build_plan.py --project-root "$PROJECT_ROOT"
.venv/bin/python tools/build_prototype.py --project-root "$PROJECT_ROOT"
.venv/bin/python tools/validate.py --project-root "$PROJECT_ROOT" --format json
```

### runtime

```bash
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" bootstrap \
  --occurred-at 2026-08-12T18:00:00+09:00 \
  --actor-kind SYSTEM --actor-id operations/local
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" init-tasks \
  --occurred-at 2026-08-12T18:00:01+09:00 \
  --actor-kind SYSTEM --actor-id operations/local
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" next-task \
  --occurred-at 2026-08-12T18:00:02+09:00
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" replay
```

taskをclaimする場合はlease token、expiry、idempotency keyを必ず固定して記録する。leaseの有効期限中にheartbeatし、終了後は必ずevidenceを付けて`complete-task`、`retry`、または`fail`を行う。`UNKNOWN` effectはreconciliationなしにretryしない。

### executionとresult

```bash
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" init
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" replay
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" record-evidence \
  --record-json /path/to/evidence-metadata.json \
  --occurred-at 2026-08-12T18:00:03+09:00 \
  --actor-kind AGENT --actor-id operations/local \
  --idempotency-key evidence/EVD001/1
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" replay-evidence
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" record-observation \
  --record-json /path/to/observation-record.json \
  --occurred-at 2026-08-12T18:00:04+09:00 \
  --actor-kind AGENT --actor-id operations/local \
  --idempotency-key observation/OB001/1
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" replay
.venv/bin/python tools/build_result.py --project-root "$PROJECT_ROOT" \
  --result-id PR001 --generated-at 2026-08-12T18:00:00+09:00 \
  --production-commit "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)" \
  --target-state BLOCKED
.venv/bin/python tools/export_result.py --project-root "$PROJECT_ROOT" \
  --output "/path/to/output-root/results/<project-id>/PR001"
```

outputはopaque URI、version、SHA-256、rights statusだけで参照する。asset body、credential、signed URLをprojectやresultへコピーしない。`NOT_RUN`と`EXTERNAL_VALIDATION_REQUIRED`は未完了の事実であり、`PASS`や`AVAILABLE`へ書き換えない。

証跡recordの入力はmetadata-onlyで、bodyをCLIへ渡さない。成功状態へ進む前に、canonical recordの`evidence_refs`へ登録済みVERIFIED recordの`{evidence_id, revision}`を指定する。target_refsは対象IDに一致し、PENDING、REJECTED、未登録、URI文字列、対象不一致はfail closedとなる。URIはevidence recordだけに保持する。

観察recordはmetadata-onlyで、`statement`、`method`、`limitations`を自動生成・要約・補完しない。`source_refs`はkind・id・revisionの固定object、evidenceだけは`{evidence_id, revision}`の固定objectで、参照先が解決できない観察、要件不一致、revision飛び、同一identityの内容変更は拒否する。観察が0件でも正常で、resultへは明示的に記録された最新`ACTIVE`観察だけが還流する。

## 診断の読み方

```bash
.venv/bin/python tools/validate.py --check --format json
.venv/bin/python tools/validate.py --project-root "$PROJECT_ROOT" --format json
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" --format json replay
.venv/bin/python tools/run_execution.py --project-root "$PROJECT_ROOT" replay --format json
```

CLIのJSON診断は`severity`、`rule`、`file`、`location`、`line`、`reason`、`remediation`、`context`を持つ。最初のエラーを隠すために再生成や削除を先に実施せず、canonical source、event hash、projection hash、対象IDを保存する。

## 代表的な復旧

### JSONLの途中行、hash chain、state divergence

`RUNTIME_PARTIAL_LINE`、`RUNTIME_EVENT_HASH`、`RUNTIME_STATE_DIVERGENCE`、`EXECUTION_EVENT_HASH`などが出た場合:

1. `run-log.jsonl`または`production-log.jsonl`をバックアップする。
2. 最後の完全な行、sequence、previous hash、event hashを特定する。
3. projectionを削除・手修正せず、原因と該当行を診断記録へ残す。
4. canonical logの訂正権限がある人間に確認し、訂正後に`replay`を実行する。
5. hash、sequence、projectionが一致した後だけ通常運用を再開する。

自動repair、黙った行削除、stateの手編集は禁止する。訂正の根拠がない場合は`BLOCKED`のまま止める。

### lease expiry、停止後の再開

期限切れleaseは外部effectの状態を確認してから復旧する。

```bash
.venv/bin/python tools/run_runtime.py --project-root "$PROJECT_ROOT" recover \
  --task-id TK001 \
  --occurred-at 2026-08-12T18:30:00+09:00 \
  --idempotency-key runtime/recover/TK001/1
```

effectが`SUCCEEDED`または`FAILED`と証拠付きで確定している場合だけ、その記録を正本へ反映する。外部systemの成否が不明な`UNKNOWN`は再実行せず、reconciliation taskと人間確認を要求する。

### approvalの不一致

`RUNTIME_APPROVAL_EXPIRED`、`RUNTIME_APPROVAL_HASH_MISMATCH`、`RUNTIME_APPROVAL_REVOKED`、`RUNTIME_APPROVAL_WILDCARD`はfail closedである。target hash、scope、expiry、authority、revocationを確認し、期限切れ・対象違い・取消済みapprovalを再利用しない。新たな承認が必要なeffectは、`PURCHASE`、`CONTRACT`、`PUBLICATION`、`DELETION`、`PHYSICAL_EXTERNAL`、`EXTERNAL_WRITE`を含む。

### resultの冪等性とexport境界

同じresult IDと同じcanonical contentはno-opとして扱う。同じIDでcontent hashが違う場合は`RESULT_IDEMPOTENCY_MISMATCH`として停止する。export後はmanifestのfile set、raw hash、result hashを確認し、余分なfile、PRIVATE_RAW、credential、signed URL、unsafe pathがあれば渡さない。

### retentionと削除

generated dataはcanonical sourceから再生成する。receiptとapproval recordはprojectと共に保持し、raw assetはopaque external referenceだけを保持する。projectやrecordを削除する場合は、human approval、対象の明示、保持要件の確認、実施結果の記録が必要である。

## 復旧完了の判定

復旧を完了とするのは、次の全てが満たされた場合だけである。

- canonical logのsequence/hash chainが連続している。
- runtime/execution projectionがreplayで再現し、divergenceがない。
- lease、approval、effectの状態が証拠と一致している。
- result/exportのhashとfile setが一致している。
- validation、unit test、evaluationを再実行し、結果と次のtaskを記録した。

満たせない場合は成功扱いにせず、診断と残gapをhandoffへ残して`BLOCKED`で停止する。
