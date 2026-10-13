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
| Handoff | `00_handoff/production-handoff.yaml`、manifest、provenance、`artifacts/production-brief.yaml` | `production-handoff.v1`、`handoff-receipt.v1`、`production-brief.v1` | receipt、source-ref index、構造化brief |
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

`planning.v1`、`production-plan.v1`、`visual-package.v1`、`selection-record.v1`、`assumption.v1`、`scope-baseline.v1`、`deliverable.v1`、`technical-spec.v1`、`acceptance-test.v1`、`material.v1`、`resource.v1`、`work-package.v1`、`task.v1`、`schedule.v1`、`budget.v1`、`risk.v1`、`approval-requirement.v1`、`approval-register.v1`、`coverage-report.v1`が計画の入力と集約を構成する。`production-brief.v1`は完成像、テーマ、メッセージ、コンセプトを構造化し、受理時に`who_disagrees`の無反論を拒否する。`production-plan.v1`の`reference_access`は、handoff source-refのID、分類、要約、record hash、恒久HTTPS URLと可用状態を保持する。必須参照カテゴリへのタスクまたはvisual package単位の依存を現在のhandoffから安全に導出できないため、`config/reference-policy.yaml`の`PLAN_WIDE_REQUIRED_CATEGORIES`を計画全体の依存モデルとして適用する。`visual-package.v1`は、計画から生成する閲覧可能なreference boardと`SIMULATED`/`CONCEPTUAL` mockupを区別し、各assetの相対path、opaque URI、hash、source repository@commit、evidence locator、rights/safety状態、未実施のphysical/external validationを保持する。`03_plan/production-plan.md`はこれらの検証済み投影と受理済みhandoffの制作判断情報を統合した唯一の人間向け出力であり、冒頭の完成像・テーマ・メッセージ・コンセプトを表形式で掲載し、コンセプト・ビジュアル・手法などの参照URLも制作リファレンスとして掲載する。briefの不足、先行作品未調査、技術を外しても成立する`MERELY_PLAINER`はblocking gapとなる。必須カテゴリのURL不足はblocking gap、危険なURLは生成エラーとなる。`production-plan.yaml`や`agent-contexts/`は機械検証・再生成・内部運用用のcanonical/projectionとして外部project内に残る。

Prototypeは`prototype.v1`を定義正本とし、`prototype-control.v1`、`prototype-run.v1`、`prototype-test-result.v1`、`prototype-review.v1`、`iteration-decision.v1`、`change-request.v1`で試作、評価、変更境界を追跡する。`digital-prototype-renderer`のrunは、構造化された寸法・材料・数量・viewer/installation relationから、既定の`deterministic-svg`または設定 override で宣言したprovider-neutral command adapterによる`image/*`を生成し、同一bytesを`03_plan/media/prototype/`へpreviewとして置く。run ID、source prototype plan ID、plan ID/revision/hash、renderer name/version/generator ID、媒体型、SHA-256、byte length、`simulated`のepistemic status、`not_physical_evidence: true`を記録し、出力hashをmetadata-onlyの`PROTOTYPE` evidenceとして登録する。本文リンク、原本、preview、hashが揃わない場合は`plan_actionability`が`PROTOTYPE_OUTPUT_MISSING`で停止する。command adapterはshell無効、timeout、stdout/stderr上限（現実装はプロセス完了後判定）、非零終了、symlinkを含む相対出力境界、媒体型と拡張子の対応を要求する。旧来のGit外SVG記録は新provenance項目なしでもlegacyとして受理する。物理試作の未実施testは従来どおり`NOT_RUN`として保持し、未承認のbaseline変更は受理しない。

### Runtime and approval

`runtime-event.v1`がappend-only event、`runtime-state.v1`がreplay projection、`runtime-task.v1`、`runtime-lease.v1`、`runtime-effect.v1`がtask graph、lease、effect evidenceを表す。sequence、previous hash、event hash、state hash、legal transition、completion evidenceを検証し、projectionの自動修復はしない。runtimeのevidence refsはURIではなく、登録済み`evidence_id`と`revision`のobject refである。

ライフサイクル遷移の条件は`tools/lib/lifecycle_guards.py`がcanonical project recordから評価する。`from_state`、`to_state`、理由、completion URIなどのpayloadだけではgateを満たさない。各`PROJECT_STATE_TRANSITIONED` eventには`payload.guard_evidence`（評価時点のcanonical record hash mapと`records_sha256`）をruntimeが固定し、replay時に同じguardを再評価して照合する。recordの削除、改変、未検証evidence、stale/revoked/hash-mismatched approval、READYでないcompletion report、terminal resultとのhash不一致はfail closedとなり、guard失敗時はevent log、state projection、manifestを変更しない。

遷移別の正本は次の通りである。

- `HANDOFF_VALIDATED -> PLANNING`: receipt、handoff、project manifest、bundle manifestのaccepted key/hash。
- `PLANNING/REVIEWING -> READY_FOR_PRODUCTION`: selection、scope/specification/WBS/budget/schedule/risk、prototype controlのreview/iteration decision、blocking gap。
- `READY_FOR_PROTOTYPE -> PROTOTYPING` と `READY_FOR_PRODUCTION -> PRODUCING`: task、resource、material、approval、stopping policy、runtime task graph。
- `PROTOTYPING -> REVIEWING` と `INSTALLING -> VALIDATING`: terminal recordと`VERIFIED` evidence、UNKNOWN effectの不存在。
- `VALIDATING -> COMPLETE*`: `08_runtime/completion-report.json`の`READY`、`production-result.yaml`のintegrity hash、全checkの`PASS`、terminal targetとgap IDの一致。

`approval.v1`はauthority、scope、target hash、expiry、revocationを持つ。wildcard targetは許可せず、期限切れ・対象hash不一致・取消済みapprovalをeffectの根拠にしない。`public-plan-review/v1`の任意`authority`は`HUMAN`（既存approvalで公開レビューを再検証）または`AUTOMATIC_PLAN`（正規PLAN_READYの機械検証済みplan recordのみ）で、後者は人間同意や外部effectの許可を表さない。

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

### Proposed production methods (AAK-10)

`production-method/v1` is an additive Production-owned input in external `02_specification/production-method.yaml`; its embedded aggregate and `plan-actionability/v1` assessment retain proposed specifications, explicit unknown-resolution actors/conditions, native step references and separate knowledge/code commits. See [content qualification and compatibility](plan-actionability.md). Legacy aggregates need no migration and do not qualify as `PLAN_READY` without this assessment.

### Curated production knowledge (AAK-11)

`production-knowledge-request/v1` selects native observations/result and a proposed method for `production-memory/v1` payloads. Native observation schemas, source/result hashes and the closed payload validator remain authoritative. `artifact-record/v1` and `knowledge-write-receipt/v1` are the shared exchange boundary. The additive `production-plan-reuse/v1` aggregate projection is independently reconstructed from an immutable owner snapshot. See [storage, revision and reuse](production-memory.md); no existing project/result migration is performed.

## Native public-plan review (Issue 69)

`tools/public_plan_review.py --project-root <external-project>` prepares a `public-plan-review-packet/v1` without granting consent or publishing. It binds aggregate/body/assets to a native approval target `public-plan-review/<plan-id>` and SHA-256. Existing native runtime approvals are replayed and checked for HUMAN authority, exact PUBLICATION review scope, validity window, latest revision and three explicit constraints: `public-plan-review:content_safety=PASSED`, `public-plan-review:rights=PASSED`, `public-plan-review:consent=PASSED`. This scope reviews content only; it does not authorize the CLI to perform a remote publication. Runtime approval recording remains a trusted caller boundary; arbitrary JSON is not authenticated human consent.

The packet lists missing decisions and preserves the ready plan. Do not ask for a new approval when a valid recorded one already covers the target. Use `tools/public_plan_attestation.py --project-root <external-project> --native-review --producer-commit <qualified-commit> --generated-at <timestamp>` to attest the prepared native review. On delivery, `--check --require-native-review` rechecks current approval validity/revocation. Plain `--review` and `--check` retain legacy byte-verification semantics for existing records; they are not evidence of a current native approval and cannot qualify the new strict delivery lane. Review packets/runtime logs remain external; only opaque approval ID/revision references enter public attestations.
