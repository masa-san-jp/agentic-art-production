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
| Execution | `05_execution/production-log.jsonl` | `execution-event.v1`、`output-version.v1`、`quality-result.v1`、`installation-*` | output、quality、installation registers |
| Result | `08_runtime/production-result.yaml` | `production-result.v1` | minimal export bundle |
| Diagnostics | CLI findings | `diagnostic.v1` | JSON/text findings and exit code |

## Schema groups

### Handoff and project

`production-handoff.v1`はresearch-ownedのhandoff snapshot、`handoff-receipt.v1`はProduction側の受理記録、`production-project.v1`は生成projectのidentity・lifecycle・source referenceを表す。handoffのmanifest hashとsource commitを失わず、同じhandoff ID/hashの再受理だけを冪等成功とする。

### Planning and prototype

`planning.v1`、`production-plan.v1`、`visual-package.v1`、`selection-record.v1`、`assumption.v1`、`scope-baseline.v1`、`deliverable.v1`、`technical-spec.v1`、`acceptance-test.v1`、`material.v1`、`resource.v1`、`work-package.v1`、`task.v1`、`schedule.v1`、`budget.v1`、`risk.v1`、`approval-requirement.v1`、`approval-register.v1`、`coverage-report.v1`が計画の入力と集約を構成する。`production-brief.v1`は完成像、テーマ、メッセージ、コンセプトを構造化し、受理時に`who_disagrees`の無反論を拒否する。`production-plan.v1`の`reference_access`は、handoff source-refのID、分類、要約、record hash、恒久HTTPS URLと可用状態を保持する。`visual-package.v1`は、計画から生成する閲覧可能なreference boardと`SIMULATED`/`CONCEPTUAL` mockupを区別し、各assetの相対path、opaque URI、hash、source repository@commit、evidence locator、rights/safety状態、未実施のphysical/external validationを保持する。`03_plan/production-plan.md`はこれらの検証済み投影と受理済みhandoffの制作判断情報を統合した唯一の人間向け出力であり、冒頭の完成像・テーマ・メッセージ・コンセプトを表形式で掲載し、コンセプト・ビジュアル・手法などの参照URLも制作リファレンスとして掲載する。briefの不足、先行作品未調査、技術を外しても成立する`MERELY_PLAINER`はblocking gapとなる。必須カテゴリのURL不足はblocking gap、危険なURLは生成エラーとなる。`production-plan.yaml`や`agent-contexts/`は機械検証・再生成・内部運用用のcanonical/projectionとして外部project内に残る。

Prototypeは`prototype.v1`を定義正本とし、`prototype-control.v1`、`prototype-run.v1`、`prototype-test-result.v1`、`prototype-review.v1`、`iteration-decision.v1`、`change-request.v1`で試作、評価、変更境界を追跡する。未実施testは`NOT_RUN`として保持し、未承認のbaseline変更は受理しない。

### Runtime and approval

`runtime-event.v1`がappend-only event、`runtime-state.v1`がreplay projection、`runtime-task.v1`、`runtime-lease.v1`、`runtime-effect.v1`がtask graph、lease、effect evidenceを表す。sequence、previous hash、event hash、state hash、legal transition、completion evidenceを検証し、projectionの自動修復はしない。

`approval.v1`はauthority、scope、target hash、expiry、revocationを持つ。wildcard targetは許可せず、期限切れ・対象hash不一致・取消済みapprovalをeffectの根拠にしない。`public-plan-review/v1`の任意`authority`は`HUMAN`（既存approvalで公開レビューを再検証）または`AUTOMATIC_PLAN`（正規PLAN_READYの機械検証済みplan recordのみ）で、後者は人間同意や外部effectの許可を表さない。

### Execution and result

`asset-reference.v1`はasset bodyを持たず、opaque URI、version、SHA-256、rights statusだけを持つ。`output-version.v1`、`output-versions.v1`、`quality-result.v1`、`quality-results.v1`、`installation-plan.v1`、`installation-result.v1`、`installation-results.v1`、`execution-event.v1`がexecution logとregisterを構成する。PASS、AVAILABLE、APPROVED、SUCCEEDEDは必要なevidenceがなければfail closedとなる。

`production-result.v1`はhandoff、plan、prototype、runtime、executionの結果を集約する。result IDはcontent hashと共に冪等性を判定し、同じIDの異なるcontentは拒否する。export bundleはmanifest宣言の2ファイルに限定し、PRIVATE_RAW、credential、signed URL、asset bodyを含めない。

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
