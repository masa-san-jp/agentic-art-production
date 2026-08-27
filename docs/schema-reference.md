# Schema reference

schemaの登録正本は`config/schema-registry.yaml`である。各entryの`id`、`path`、`status`、version、外部snapshotのsource commit、取得日時、raw SHA-256を変更せずに確認する。実行時のnetwork取得は前提にせず、登録済みlocal snapshotを使う。

## 共通ルール

- canonical JSONは`json-sort-keys-compact-utf8-v1`で正規化する。
- hashはcanonical contentまたはraw file contentのSHA-256を使い、表記は`sha256:<64 hex>`とする。
- schema参照はregistryのlocal pathから解決し、未登録・path traversal・外部network依存は拒否する。
- schema、config、fixture、validatorの互換性を変える場合は、version、migration、test、task queueを同じ変更で更新する。
- 外部schemaはimmutable snapshotであり、source repository、40桁commit、取得日時、raw hashが揃わないものを受理しない。

## ライフサイクル対応表

| 段階 | 主なcanonical file | schema family | projection / output |
| --- | --- | --- | --- |
| Handoff | `00_handoff/production-handoff.yaml`、manifest、provenance | `production-handoff.v1`、`handoff-receipt.v1`、`handoff-receipts.v1`、`handoff-impact-report.v1` | receipt、source-ref index、handoff history、impact report |
| Project | `project.yaml`、scope、selection、assumption | `production-project.v1`、`scope-baseline.v1` | project validation |
| Planning | `03_plan/production-plan.yaml` | `production-plan.v1`、planning entry schemas | `production-plan.md`（唯一の人間向け統合計画書）、coverage、agent context |
| Prototype | `04_prototype/prototype-control.yaml`、run、test、review | `prototype-control.v1`、`prototype-run.v1`、`prototype-test-result.v1`、`prototype-review.v1` | iteration decision、change request |
| Runtime | `08_runtime/run-log.jsonl` | `runtime-event.v1` | `production-state.v1`、task/lease/effect projection |
| Approval | runtime approval record | `approval.v1`、`approval-requirement.v1`、`approval-register.v1` | approval scope、expiry、revocation |
| Evidence | `05_execution/evidence-log.jsonl` | `evidence-event.v1`、`evidence-record.v1`、`evidence-register.v1` | `evidence-register.yaml` |
| Execution | `05_execution/production-log.jsonl` | `execution-event.v1`、`output-version.v1`、`quality-result.v1`、`installation-*`、`observation-record.v1` | output、quality、installation、observation registers |
| Result | `08_runtime/production-result.yaml`、`08_runtime/completion-report.json` | `production-result.v1`、`completion-report.v1` | minimal export bundle、target-state completion gate |
| Diagnostics | CLI findings | `diagnostic.v1` | JSON/text findings and exit code |
| Agent harness | `08_runtime/agent-harness/agent-run-log.jsonl`、run、context、grant、invocation、action | `agent-run.v1`、`agent-context.v1`、`capability-grant.v1`、`worker-invocation.v1`、`agent-action-envelope.v1`、`agent-run-event.v1`、`agent-run-state.v1` | replay projection、bounded worker response、broker decision |

## Schema groups

### Handoff and project

`production-handoff.v1`はresearch-ownedのhandoff snapshot、`handoff-receipt.v1`はProduction側の受理記録、`production-project.v1`は生成projectのidentity・lifecycle・source referenceを表す。handoffのmanifest hashとsource commitを失わず、同じhandoff ID/hashの再受理だけを冪等成功とする。

`handoff-receipts.v1`は`00_handoff/handoff-log.jsonl`から導出する全受理receiptのprojectionで、`handoff-impact-report.v1`はcurrentとcandidateのcanonical artifact、plan、execution/evidence/observation/resultへの影響をold/new hashとaffected IDで記録する。改訂のcurrent projectionは`00_handoff/production-handoff.yaml`、`handoff-receipt.yaml`、`source-bundle/`、`source-bundle-manifest.yaml`、`03_plan/`であり、過去版は`00_handoff/history/`、`source-bundles/`、`03_plan/history/`、`08_runtime/history/`へ保持する。受理は`tools/new_production.py --accept-revision`のstaging検証後にだけ反映され、historyのhash chain、projection integrity、lineage divergenceはfail closedとなる。

### Planning and prototype

`planning.v1`、`production-plan.v1`、`selection-record.v1`、`assumption.v1`、`scope-baseline.v1`、`deliverable.v1`、`technical-spec.v1`、`acceptance-test.v1`、`material.v1`、`resource.v1`、`work-package.v1`、`task.v1`、`schedule.v1`、`budget.v1`、`risk.v1`、`approval-requirement.v1`、`approval-register.v1`、`coverage-report.v1`が計画の入力と集約を構成する。計画は受理済みhandoffのrequirements、採択hypothesis、acceptance tests、明示されたprototype plansだけから決定的に導出する。prototype planがない場合はtask、material、resourceを生成せず、未確定の値は`rule`、`impact`、`owner`、`blocking`、`resolution_condition`、`source_refs`を持つ構造化gapとして残す。`PLANNING`かつblocking gap付きの計画ではcoverageが100%未満でもschema-validだが、`READY_FOR_PROTOTYPE`へ進めない。`03_plan/production-plan.md`はこれらの検証済み投影と受理済みhandoffの制作判断情報を統合した唯一の人間向け出力であり、`production-plan.yaml`や`agent-contexts/`は機械検証・再生成・内部運用用のcanonical/projectionとして外部project内に残る。

Prototypeは`prototype.v1`を定義正本とし、`prototype-control.v1`、`prototype-run.v1`、`prototype-test-result.v1`、`prototype-review.v1`、`iteration-decision.v1`、`change-request.v1`で試作、評価、変更境界を追跡する。未実施testは`NOT_RUN`として保持し、未承認のbaseline変更は受理しない。

### Runtime and approval

`runtime-event.v1`がappend-only event、`runtime-state.v1`がreplay projection、`runtime-task.v1`、`runtime-lease.v1`、`runtime-effect.v1`がtask graph、lease、effect evidenceを表す。sequence、previous hash、event hash、state hash、legal transition、completion evidenceを検証し、projectionの自動修復はしない。runtimeのevidence refsはURIではなく、登録済み`evidence_id`と`revision`のobject refである。

ライフサイクル遷移の条件は`tools/lib/lifecycle_guards.py`がcanonical project recordから評価する。`from_state`、`to_state`、理由、completion URIなどのpayloadだけではgateを満たさない。各`PROJECT_STATE_TRANSITIONED` eventには`payload.guard_evidence`（評価時点のcanonical record hash mapと`records_sha256`）をruntimeが固定し、replay時に同じguardを再評価して照合する。recordの削除、改変、未検証evidence、stale/revoked/hash-mismatched approval、READYでないcompletion report、terminal resultとのhash不一致はfail closedとなり、guard失敗時はevent log、state projection、manifestを変更しない。

遷移別の正本は次の通りである。

- `HANDOFF_VALIDATED -> PLANNING`: receipt、handoff、project manifest、bundle manifestのaccepted key/hash。
- `PLANNING/REVIEWING -> READY_FOR_PRODUCTION`: selection、scope/specification/WBS/budget/schedule/risk、prototype controlのreview/iteration decision、blocking gap。
- `READY_FOR_PROTOTYPE -> PROTOTYPING` と `READY_FOR_PRODUCTION -> PRODUCING`: task、resource、material、approval、stopping policy、runtime task graph。
- `PROTOTYPING -> REVIEWING` と `INSTALLING -> VALIDATING`: terminal recordと`VERIFIED` evidence、UNKNOWN effectの不存在。
- `VALIDATING -> COMPLETE*`: `08_runtime/completion-report.json`の`READY`、`production-result.yaml`のintegrity hash、全checkの`PASS`、terminal targetとgap IDの一致。

`approval.v1`はauthority、scope、target hash、expiry、revocationを持つ。wildcard targetは許可せず、期限切れ・対象hash不一致・取消済みapprovalをeffectの根拠にしない。

### Execution and result

`evidence-record.v1`は外部・物理作業のbodyを保存せず、対象ID、opaque URI、content hash、取得・記録時刻、検証状態、権利・privacy・制約だけを記録する。`05_execution/evidence-log.jsonl`がcanonical append-only source、`evidence-register.yaml`がreplay projectionであり、`evidence_id`とrevisionはimmutableである。`asset-reference.v1`はasset bodyを持たず、opaque URI、version、SHA-256、rights statusだけを持つ。`output-version.v1`、`output-versions.v1`、`quality-result.v1`、`quality-results.v1`、`installation-plan.v1`、`installation-result.v1`、`installation-results.v1`、`execution-event.v1`がexecution logとregisterを構成する。PASS、AVAILABLE、APPROVED、SUCCEEDEDは必要なVERIFIED evidenceがなければfail closedとなる。旧URI形式からの移行規則は[`evidence-migration.md`](evidence-migration.md)に固定する。

`production-result.v1`はhandoff、plan、prototype、runtime、executionの結果を集約する。result IDはcontent hashと共に冪等性を判定し、同じIDの異なるcontentは拒否する。export bundleはmanifest宣言の2ファイルに限定し、PRIVATE_RAW、credential、signed URL、asset bodyを含めない。

`completion-report.v1`はproduction-result wire contractへtarget stateを混入させず、`COMPLETE`、`COMPLETE_WITH_GAPS`、`BLOCKED`の判定、check、gapのcanonical source key、result hashを記録する。`COMPLETE`の不足条件は`REJECTED`として列挙し、同じresult IDのtarget state変更やresult hash不一致は拒否する。

### Agent harness

agent runのeventとprojectionは`agent-run-event.v1`、`agent-run-state.v1`で検証する。`agent-context.v1`はtask-scoped最小入力、`capability-grant.v1`はpolicyとleaseへ束縛された権限、`worker-invocation.v1`はrequest/response hashとbounded execution metadata、`agent-action-envelope.v1`はproposalのwire shapeを表す。tool request/result schemaはbroker adapterの将来拡張用であり、v1のfixture workerは`RECORD_METADATA` proposalだけを返す。

`agent-run-log.jsonl`はcanonical append-only source、`agent-run-state.json`とrun recordは生成projectionである。context、grant、invocation、response、actionにraw lease、credential、local absolute path、signed URLを保存しない。`REQUEST_TOOL`のrequest/resultはbroker発行actionへ内容一致で束縛し、承認が必要なeffect proposalはcurrent planのexact target reference/hashへ束縛する。policyは`config/agent-harness-policy.yaml`、schema path/hashは`config/schema-registry.yaml`が正本で、version 1.0.0では既存production schemaのmigrationは不要である。

`observation-record.v1`は制作中に明示的に記録された観察のappend-only revisionであり、`observations.v1`は`production-log.jsonl`のreplay projectionである。要件・source refsは受理済みhandoff、current plan、prototype control、execution register、evidence registerへ解決できなければならない。result builderは各観察の`statement`、`method`、`limitations`、`related_requirement_ids`を変更せず、最新`ACTIVE`だけをIDのUTF-8 byte順で返す。観察がない、または最新が`RETRACTED`なら`observations: []`とする。移行規則は[`observation-migration.md`](observation-migration.md)に固定する。

## 現在の外部snapshot

現在registryに固定されている重要なsnapshotは次のとおりである。

| schema | 所有repo | source commit | raw SHA-256 |
| --- | --- | --- | --- |
| `production-result.v1` | `masa-san-jp/agentic-art-production` | `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b` | `sha256:5b69090476891629932e5b01260a6217273f8a4771cc004d4922fcd04a0a104a` |
| `production-handoff.v1` | `masa-san-jp/agentic-art-research` | `aba5f1738cc0066c994433d91c333b3cfe5210da` | `sha256:715f2426474de9d957ef3129e0a65d69492ff7e75181272b52cb4cbb850cf0f7` |

値を更新するときは、新旧version、source commit、raw hash、取得日時、consumer test、migrationを記録する。Research側とのresult import互換性を壊す変更は、Productionだけを先に更新してはならない。
