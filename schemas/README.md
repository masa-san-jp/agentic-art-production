# Schemas

このdirectoryはProductionのcanonical schemaを置く。共通定義は`common.schema.json`を参照し、外部repo所有のhandoff/result schemaはcommit固定snapshotとして`external/`へ追加する。

schemaを置き換える場合は、schema registry、migration、fixture、validator、versionを同じ変更で更新する。

Planning domainは`planning.schema.json`を定義正本とし、scope、selection、assumption、deliverable、technical spec、acceptance-test fixture、material、resource、WBS/task、schedule、budget、risk、approval requirement、coverageのentry schemaから参照する。`production-plan.schema.json`は集約された決定的planを検証する。tasklessな未開始計画を表現できるようschedule/task critical pathの空配列を許容し、mandatory requirement未接続時は構造化blocking gapを必須とする。計画導出器はhandoffの明示入力にない媒体、数量、担当、工程、材料、資源、効果種別を補完しない。

Prototype domainは`prototype.schema.json`を定義正本とし、prototype run、test result、dimension別review、iteration decision、change request、aggregate controlを検証する。未実施testは`NOT_RUN`であり、外部検証が必要な`PASS`、失敗後のchange requestなしの`REVISE`、未承認のMAJOR/CRITICAL baseline変更を拒否する。

Runtimeは`runtime-event.schema.json`、`runtime-state.schema.json`、`runtime-task.schema.json`、`runtime-lease.schema.json`、`runtime-effect.schema.json`を使う。`08_runtime/run-log.jsonl`がappend-onlyのcanonical sourceで、`production-state.json`は全eventをreplayしたprojectionである。sequence、previous hash、event hash、state hash、legal transition、BLOCKED resume evidence、completion evidence、task dependency、lease token、retry limit、effect target hash、approval expiry/revocationを検証し、projectionの自動修復は行わない。

`production-result.schema.json`はProductionが所有する`production-result` v1の正本である。Production commit `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b`からのraw SHA-256固定snapshotをresearch側`agent/handoff-build` commit `9d162b17394fd121ab7f986321b24a152684a9a5`へ適用し、consumer互換性とrelease gateを確認済みである。`tools/build_result.py`はprojectのhandoff、plan、prototype、runtime、execution projectionから決定的な結果を作り、`tools/export_result.py`は`production-result.yaml`と`manifest.yaml`だけをGit外bundleへ出力する。asset body、credential、signed URLは結果にもbundleにも含めない。

Executionは`output-version.schema.json`、`quality-result.schema.json`、`installation-plan.schema.json`、`installation-result.schema.json`、`execution-event.schema.json`を使う。`05_execution/production-log.jsonl`がoutput・quality・installationの追記元で、各YAML registerはreplay projectionである。assetは`asset-reference.schema.json`のopaque URI・version・SHA-256・rights statusだけで表し、asset body・credential・signed URLは保存しない。PASS、AVAILABLE、APPROVED、SUCCEEDEDは対応する品質・承認・安全・外部証拠がない限りfail closedとなり、未実施は`NOT_RUN`または`EXTERNAL_VALIDATION_REQUIRED`として保持する。
Evidenceは`evidence-record.schema.json`、`evidence-event.schema.json`、`evidence-register.schema.json`を使う。`05_execution/evidence-log.jsonl`が外部・物理証跡metadataのappend-only source、`evidence-register.yaml`がprojectionである。canonical recordのevidence refはURI文字列ではなく、登録済みVERIFIED recordの`{evidence_id, revision}`に限定する。Evidence本体は保存せず、URI・hash・target・verification・rights/privacy・limitationsだけを保持する。旧URI形式の移行規則は`docs/evidence-migration.md`に記載する。
