# Schemas

このdirectoryはProductionのcanonical schemaを置く。共通定義は`common.schema.json`を参照し、外部repo所有のhandoff/result schemaはcommit固定snapshotとして`external/`へ追加する。

schemaを置き換える場合は、schema registry、migration、fixture、validator、versionを同じ変更で更新する。

Planning domainは`planning.schema.json`を定義正本とし、scope、selection、assumption、deliverable、technical spec、acceptance-test fixture、material、resource、WBS/task、schedule、budget、risk、approval requirement、coverageのentry schemaから参照する。`production-plan.schema.json`は集約された決定的planを検証する。

Prototype domainは`prototype.schema.json`を定義正本とし、prototype run、test result、dimension別review、iteration decision、change request、aggregate controlを検証する。未実施testは`NOT_RUN`であり、外部検証が必要な`PASS`、失敗後のchange requestなしの`REVISE`、未承認のMAJOR/CRITICAL baseline変更を拒否する。

Runtimeは`runtime-event.schema.json`と`runtime-state.schema.json`を使う。`08_runtime/run-log.jsonl`がappend-onlyのcanonical sourceで、`production-state.json`は全eventをreplayしたprojectionである。sequence、previous hash、event hash、state hash、legal transition、BLOCKED resume evidence、completion evidenceを検証し、projectionの自動修復は行わない。

`production-result.schema.json`はProductionが所有する`production-result` v1の正本である。Production commit `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b`からのraw SHA-256固定snapshotをresearch側`agent/handoff-build` commit `9d162b17394fd121ab7f986321b24a152684a9a5`へ適用し、consumer互換性とrelease gateを確認済みである。schema単体の追加は、実際のresult生成・export・相互fixture検証の完了を意味しない。
