# Agentic Art Production リポジトリ実行計画

- 作成日: 2026-08-11
- 状態: `BOOTSTRAP-001` DONE / `CONTRACT-001` DONE / `PLANNING-SCHEMA-001` DONE / `PLANNING-BUILD-001` DONE / `PROTOTYPE-001` DONE / `RUNTIME-001` DONE / `RUNTIME-002` DONE / `EXECUTION-001` READY
- 対応仕様: `docs/20260811-agentic-art-production-system-design-specification.md`
- 実装契約: `docs/20260811-agentic-art-production-implementation-contract-specification.md`
- 実行対象: GPT-5.6 LunaまたはClaude Sonnet級の、ファイル編集・コマンド実行・Git操作が可能なエージェント

## Purpose / Big Picture

完成後は、エージェントが`agentic-art-research`のhandoff bundleを検証して制作projectを作成し、採択、仕様化、工程・資源・予算・日程、試作、変更管理、本制作、設営、受入、結果還流を有限の状態機械で管理できる。

最低限の利用確認は次とする。

```bash
AAP_SMOKE_ROOT="$(mktemp -d /tmp/agentic-art-production-smoke.XXXXXX)"
python3 tools/new_production.py harmony-production --handoff tests/fixtures/handoff/harmony --output-root "$AAP_SMOKE_ROOT"
python3 tools/validate.py --check
python3 tools/validate.py --project-root "$AAP_SMOKE_ROOT/production/harmony-production"
python3 tools/run_project.py --project-root "$AAP_SMOKE_ROOT/production/harmony-production" --offline-fixture tests/fixtures/harmony-production
python3 tools/build_plan.py --project-root "$AAP_SMOKE_ROOT/production/harmony-production"
python3 tools/build_result.py --project-root "$AAP_SMOKE_ROOT/production/harmony-production"
python3 tools/export_result.py --project-root "$AAP_SMOKE_ROOT/production/harmony-production" --output "$AAP_SMOKE_ROOT/results/harmony-production"
python3 -m unittest discover -s tests -v
```

## Agent Operating Contract

- 会話履歴を前提にしない。`AGENTS.md`、設計仕様、本計画、task queueを正本とする。
- wire format、hash、共通型、状態遷移、runtime、approval、安全上限は実装契約仕様を正本とする。
- 依存完了済みの最小ID `READY` taskを一件だけ実行する。
- task開始時に`IN_PROGRESS`、完了時に`DONE`へ更新する。
- 仕様にない方針を黙って導入しない。可逆で保守的な実装を選ぶ。
- 実作品、私的原文、credential、signed URL、契約書原本をfixtureやGitへ入れない。
- 外部接続、購入、契約、公開、削除、物理作業をテストや実装中に実行しない。
- 外部serviceがなくても合成fixtureで全工程を検証できるようにする。

## Progress

- [x] (2026-08-11) `DESIGN-001`: 設計仕様、実行計画、Agent規則、初期task queueを作成。
- [x] (2026-08-11) `DESIGN-002`: Bootstrap前の実装契約、output境界、bundle自己完結性、決定性、runtime、承認、安全上限を確定。
- [x] (2026-08-11) `BOOTSTRAP-001`: repo骨格、設定、共通schema、CI、基礎validator。
- [x] (2026-08-12) `CONTRACT-001`: `harmony-study`のREADY handoffをclean source commit `b6a95228ac1bfaf7189aba907bd5cd05d1a43b6a`からexportし、bundle内schema、manifest hash、provenance、source-ref index、非blocking gapを検証。production側で`RC001 ACCEPTED`、`HANDOFF_VALIDATED` projectをGit外output rootへ生成。
- [x] (2026-08-12) 受理互換性hardening: research-owned common schema IDをbundleからoffline解決し、schema内の`PRIVATE_RAW`/`RESTRICTED`語彙をpayload security scanから分離。Google Drive/macOSの`Icon\\r` sidecarをwire file setから除外し、14 testで固定。
- [x] (2026-08-12) Cross-repo result contract prework: Production-owned `production-result` v1 schema、registry hash、research consumer互換性を固定。research側`agent/handoff-build`の`9d162b1`でsnapshot適用、boundary probe修正、consumer E2E、release gate 3回を完了した。result builder/exporterと相互E2E本体は`EXECUTION-001`完了後に着手する。
- [x] (2026-08-12) `PLANNING-SCHEMA-001`: scope、deliverable、spec、WBS、resource、budget、schedule、risk、approval requirement、coverage、acceptance-test schemaを追加し、参照・循環・外部effect gateをvalidatorへ実装。
- [x] (2026-08-12) `PLANNING-BUILD-001`: 受理済みhandoffから決定的plan、coverage report、依存graph、critical path、human brief、task-minimal agent contextsをGit外output rootへ生成。
- [x] (2026-08-12) `PROTOTYPE-001`: prototype run、test result、dimension別review、iteration decision、change requestのschema・validator・決定的builder・fail-closed fixtureを実装。受理済み`harmony-study`へ`PC001`を生成。
- [x] (2026-08-12) `RUNTIME-001`: 状態機械、append-only event log、state replay、BLOCKED resume、idempotency、改ざん・projection divergence検出を実装。
- [x] (2026-08-12) `RUNTIME-002`: task graph、決定的eligible選択、lease/heartbeat/expiry recovery、TRANSIENT retry limit、approvalのauthority/expiry/revocation/target hash検証、effect target hash・冪等性・unknown outcome停止を実装。合成fixtureでkill-and-resume、retry、stale lease、expired/revoked/hash-mismatched approval、duplicate effectを検証。
- [ ] `EXECUTION-001`: output version、quality、asset reference、installation。（次の開始点）
- [ ] `FEEDBACK-001`: production result生成、export、research互換性。
- [ ] `EVAL-001`: representative E2E、security、chaos、determinism。
- [ ] `DOCS-001`: onboarding、運用、障害対応、schema reference。
- [ ] `RELEASE-001`: release gate、CI evidence、v1.0.0候補。

## Surprises & Discoveries

- 2026-08-11: 新規repoは空であり、設計文書だけでは実行順序と再開地点が残らないため、Agent規則、ExecPlan、task queueを同時に初期化する。
- 2026-08-11: productionとresearchを一つの状態機械にすると、制作中断がresearch完了を無効化する。handoff/result契約だけを共有し、stateは分離する。
- 2026-08-11: 物理制作をAIの内部effectとして扱うと実施捏造が起きる。effect typeとexternal validation状態をdomain contractに含める。
- 2026-08-11: 正確な金額、日時、担当は入力がなければ確定できない。未確定を`null`またはgapとして保持し、概算帯と基準線を区別する。
- 2026-08-12: research側のworking treeはcleanなHEADへ更新され、handoff schema raw snapshotはProductionへ登録できたが、clean sourceに対応するREADY self-contained export bundleはまだtracked・提供されていない。ProductionはREADY handoffとexport fixtureが揃うまで受理を完了しない。
- 2026-08-12: research側のhandoff release gateはbase release、fixture contract、fail-closed、後方互換性をPASSしたが、これはテスト時一時bundleの生成能力を示すだけである。実際の外部`harmony-study` outputには`production-handoff.yaml`がなく、Productionが受理できるREADY bundleはまだ生成できない。
- 2026-08-12: research側の`HANDOFF-E2E-001`はProduction-owned result schemaとconsumer compatibilityを待っている。Production側にresult schemaが未登録だったため、M6を全面開始せず、まずself-contained v1 schemaとresearch importer互換fixtureだけを前倒しで追加した。実制作結果の生成/exportはexecution/runtime契約完了後に行う。
- 2026-08-12: Productionのclean commit `fb15f32`からresearch側一時コピーへのresult schema snapshot取得は成功し、raw SHA-256も一致した。ただしresearchの`handoff_release_check`は、schema snapshot存在後も「欠落入力」を`EXTERNAL-SCHEMA`として期待するため`FEEDBACK-INPUT`で誤失敗する。次のcross-repo開始点はresearch側のboundary probe修正であり、Production schemaの不整合ではない。
- 2026-08-12: research側`agent/handoff-build`のclean commit `9d162b1`でProduction result schema snapshot、consumer互換性、release gate 3回が完了した。Production側の残る外部entry gateは、実際のREADY `production-handoff.yaml`とclean export bundleだけであり、test-time fixtureを受理済み入力へ昇格させない。
- 2026-08-12: 実`harmony-study` bundleの受理で、production validatorがproduction-owned common schema IDだけを登録していたため、research-owned schemaの`$ref`がnetwork解決へフォールバックした。bundle内common schemaを優先し、宣言済み`$id`をoffline registryへ登録して解消した。
- 2026-08-12: Google Drive/macOSの同期ディレクトリには空の`Icon\\r` sidecarが自動生成され、wire payloadには含めず、受理時だけ明示的なfilesystem metadataとして除外した。
- 2026-08-12: `harmony-study`には金額・見積・会場日程が入力されていないため、予算をゼロと偽装せず金額nullのestimate gap、日程を絶対日時と偽装せずrelative scheduleとして計画化した。
- 2026-08-12: `AGENT_RECOMMENDED`の選択は`PROVISIONAL`のまま保持し、物理prototype taskは`AR001 REQUIRED`・`BLOCKED`にした。plan生成は実行承認や外部effectを意味しない。
- 2026-08-12: prototype controlは物理実行の代わりに`PRT001 BLOCKED`、`PTR001 NOT_RUN`、`RV001 NOT_STARTED`、`ITD001 WAITING_FOR_RUN`を生成する。結果がない状態をPASSへ補正しない。
- 2026-08-12: `FAIL`のprototypeには`REVISE`または`BLOCK` decisionを要求し、`REVISE`にはchange requestを要求する。MAJOR変更の適用にはresearch reviewとhuman approvalを要求し、CRITICAL変更はprototype control層で適用不可とする。
- 2026-08-12: runtime bootstrapでは、既存projectionが`HANDOFF_VALIDATED`ならeventを捏造せずrevision 0のbaselineを維持し、plan生成済みで`PLANNING`なら`EVT000001`の受理済みhandoff→planning transitionを記録する。
- 2026-08-12: `production-state.json`の直接編集やevent logのpartial line・hash mismatch・state divergenceは自動repairせず拒否し、同じidempotency keyと同じ内容の再実行だけをno-opとして扱う。
- 2026-08-11: handoff本体はacceptance testやPrototype PlanをID参照するため、handoff YAMLとschemaだけではoffline self-containedにならない。manifestにhypothesis、requirement、test、prototype、source indexのsnapshotを必須化する。
- 2026-08-11: `production-state.json`を単独正本にするとkill-and-resumeとreplay acceptanceが曖昧になる。hash chain付きevent logを正本、stateを検証済みprojectionに分離する。
- 2026-08-11: Bootstrap前のlocal PythonにはPyYAMLがなく、`tests/`と`tools/validate.py`も未作成である。DESIGN-002はMarkdown、Ruby標準YAML parser、dependency check、`git diff --check`で検証し、Python依存と正式validatorはBOOTSTRAP-001で作成する。
- 2026-08-11: システムPythonへPyYAML/jsonschemaを追加できないため、依存を`requirements.txt`とCIへ固定し、実行確認は一時virtual environmentで行った。repository内の`.venv`もtracked対象外とした。
- 2026-08-11: minimal handoff fixtureでbundle検証・生成経路は確認できるが、research側のtest-time bundleは互換性証明にならない。`CONTRACT-001`はREADY handoff、clean export bundle、provenanceが揃うまで完了扱いにしない。

## Decision Log

| 日付 | 決定 | 代替案 | 理由・影響 |
|---|---|---|---|
| 2026-08-11 | file-based canonical storeから開始 | DB/Web appから開始 | schemaと状態契約を先に安定させる |
| 2026-08-11 | handoff schemaはresearchのsnapshotを使う | productionで再定義 | 正本競合を避ける |
| 2026-08-11 | production result schemaは本repoが所有 | researchが両契約を所有 | 実行結果の意味を生成側が保証する |
| 2026-08-11 | asset本体をGitへ置かない | Git LFSを必須化 | 初期安全境界と運用を単純化する |
| 2026-08-11 | external/physical effectは承認とrecordのみ | agentが自動実行 | 越権と実施捏造を防ぐ |
| 2026-08-11 | budget statusをestimate/commit/actualで分ける | 単一amount | 推定と実支出を混同しない |
| 2026-08-11 | 実projectをprotocol repo外の明示output rootへ置く | `projects/`へ常設 | private data、asset、machine pathのGit混入を防ぐ |
| 2026-08-11 | canonical hashをcompact sorted-key UTF-8 JSONへ固定 | YAML raw bytesまたは未定 | research実装と一致し、表記差と意味差を分離できる |
| 2026-08-11 | money/quantityを10進文字列で保存 | JSON number | binary float、暗黙丸め、無単位値を防ぐ |
| 2026-08-11 | append-only event logをruntime正本、stateをprojectionとする | state単独正本 | deterministic replayとcrash recoveryを同じ契約で満たす |
| 2026-08-11 | v1はsingle canonical writer | 複数writerの分散lock | file-based runtimeの競合と二重effectを限定する |
| 2026-08-11 | approvalと外部実施evidenceを分離 | approvalを実施証明として兼用 | 越権と実施捏造を防ぐ |
| 2026-08-11 | Python依存は`requirements.txt`へ固定し、実行環境へはvirtual environmentで導入 | システムPythonへ直接導入 | CIとlocal再現性を保ち、環境を汚染しない |
- 2026-08-12 | minimal fixtureのruntime受入では、planning上の物理taskを検証可能なruntime READYへ投影するが、approval/resource/material eligibilityは別途fail-closedで維持する | planning BLOCKEDをruntimeでも固定 | task lease/retry/effect経路を合成fixtureで検証しつつ、実際の承認・資源・物理作業を発生させないため |
- 2026-08-12 | runtime effect開始時は、approval requirementの`target_ref`と`target_sha256`をeffect intentにも一致させる | taskごとの任意targetを許可 | approvalが許可した対象と実行intentの乖離を防ぐため |

## Outcomes & Retrospective

設計段階では、researchの芸術判断を保ったまま実制作に必要な運用情報を独立管理する構造に加え、実装者へ残っていたwire format、hash、共通型、runtime、承認、安全上限を確定した。`BOOTSTRAP-001`では、設定・schema・安全検査・canonical serializer・bundle loader・project generator・CI・正常/失敗/再実行テストを追加した。minimal fixtureによる機構確認は完了したが、handoff互換性の最終確定はresearch側clean commitのschemaとexpected bundleを外部entry gateとして残している。

research側のProduction result schema/consumer連携は、Production commit `fb15f32`のschema snapshotを`agent/handoff-build`へ適用し、consumer E2Eと`--require-schema-snapshot` gate 3回を完了した。続いて`harmony-study`の実出力をPRODUCTION_HANDOFFへ拡張し、`HO001 READY`のclean bundleを生成した。Productionはbundleをnetworkなしで検証し、`RC001 ACCEPTED`、`HANDOFF_VALIDATED`のproduction projectをGit外output rootへmaterializeした。計画とprototype controlまで完了し、次の開始点は`RUNTIME-001`である。

### RUNTIME-001 handoff

```text
Task: RUNTIME-001
Status: DONE
Changed canonical files: schemas/runtime-state.schema.json, config/schema-registry.yaml, tools/lib/runtime.py, tools/run_runtime.py, tools/validate.py, tests/test_bootstrap.py, README.md, schemas/README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/08_runtime/run-log.jsonl` and updated `production-state.json`; no protocol data or project assets were added to Git
Commands executed: `python tools/run_runtime.py --project-root <output-root>/production/harmony-study bootstrap --occurred-at 2026-08-12T16:00:00+09:00 --actor-kind SYSTEM --actor-id runtime/local`; `python tools/run_runtime.py --project-root <output-root>/production/harmony-study replay`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --check`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: `EVT000001` records the accepted handoff to `PLANNING`; replay returns revision 1 with matching event/state hashes; 22 tests pass, including BLOCKED resume, idempotency, illegal completion, tampered event, and state divergence rejection
New validation rules: contiguous sequence, previous-event hash chain, canonical event hash, append-only newline-complete JSONL, atomic projection hash, legal lifecycle transitions, blocker/resume evidence, cancellation authority, and substantiated completion
Approvals simulated: none; no physical work, purchase, contract, publication, deletion, or external effect was performed
Surprises and decisions: a `HANDOFF_VALIDATED` baseline does not require a synthetic event; plan-generated `PLANNING` state is initialized by one explicit `EVT000001` transition
Remaining risks: `TK004` is the only eligible synthetic read-only task in the current `harmony-study` plan; physical tasks remain blocked by resource/material/`AR001` approval gates and no external effect was executed
Next READY task: `EXECUTION-001`
Exact restart command: `git status --short && python tools/run_runtime.py --project-root <output-root>/production/harmony-study replay`
```

### RUNTIME-002 handoff

```text
Task: RUNTIME-002
Status: DONE
Changed canonical files: schemas/runtime-event.schema.json, schemas/runtime-state.schema.json, schemas/runtime-task.schema.json, schemas/runtime-lease.schema.json, schemas/runtime-effect.schema.json, config/runtime-policy.yaml, config/schema-registry.yaml, tools/lib/runtime.py, tools/run_runtime.py, tools/validate.py, tests/test_bootstrap.py, README.md, schemas/README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/08_runtime/run-log.jsonl` and `production-state.json`; `EVT000002` registers the plan-derived task graph; no protocol data, credentials, or project assets were added to Git
Commands executed: `python -m unittest discover -s tests -p 'test_*.py'`; `python tools/validate.py --check`; `python tools/run_runtime.py --project-root <output-root>/production/harmony-study init-tasks --occurred-at 2026-08-12T16:30:00+09:00 --actor-kind SYSTEM --actor-id runtime/local`; `python tools/run_runtime.py --project-root <output-root>/production/harmony-study next-task --occurred-at 2026-08-12T16:30:01+09:00`; `python tools/run_runtime.py --project-root <output-root>/production/harmony-study replay`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `git diff --check`
Results: 28 tests pass; repository and `harmony-study` validation pass; replay returns revision 2 with matching event/state hashes and task graph hash; deterministic next eligible task is `TK004`; physical tasks `TK001`/`TK002` remain non-claimable until resources, material, and `AR001` approval are valid
New validation rules: task dependencies and deterministic ordering, single-owner lease token, heartbeat expiry, kill-and-resume recovery, TRANSIENT-only retry with configured max attempts, immutable approval revisions, approval authority/expiry/revocation/target hash, effect key/target hash idempotency, and unknown external outcome fail-closed retry blocking
Approvals simulated: approval fixtures use synthetic HUMAN records only; no physical work, purchase, contract, publication, deletion, network fetch, or external effect was performed
Surprises and decisions: planning intentionally marks physical tasks `BLOCKED` before approval. Runtime maps approval-gated planning tasks to runtime `READY`, while eligibility still fails closed until matching approval, resources, and materials are available. This keeps approval as an executable gate without pretending that approval already exists
Remaining risks: no adapter executes external effects; an `UNKNOWN` effect requires reconciliation evidence; current `harmony-study` has no valid approval or confirmed resources/materials for physical tasks
Next READY task: `EXECUTION-001`
Exact restart command: `git status --short && python tools/run_runtime.py --project-root <output-root>/production/harmony-study replay`
```

### BOOTSTRAP-001 handoff

```text
Task: BOOTSTRAP-001
Status: DONE
Changed canonical files: AGENTS.md, PLANS.md, README.md, requirements.txt, .gitignore, .github/workflows/validate.yml, config/, schemas/, templates/, tests/, tools/, docs/20260811-agentic-art-production-repository-execution-plan.md, execution/task-queue.yaml
Generated files: none in repository; external smoke output only
Commands executed: `python3 -m unittest discover -s tests -v`; `python3 tools/validate.py --check`; `python3 -m py_compile tools/*.py tools/lib/*.py`; `git diff --check`
Results: repository validator and 12-test suite pass; generated project accepts the minimal fixture; tampered bundle, duplicate YAML, invalid JSONL, repository boundary, private marker, path traversal, and schema errors fail closed
New validation rules: duplicate keys, canonical scalar, schema reference, path safety, archive limits, manifest hash, provenance, URI, safety marker, repository output boundary
Approvals simulated: none
Surprises and decisions: dependencies were unavailable in system Python, so validation ran in a temporary virtual environment
Remaining risks: real research handoff schema and export bundle are external inputs
Next READY task: `CONTRACT-001`
Exact restart command: `git status --short && python3 tools/validate.py --check`
```

### CONTRACT-001 handoff

```text
Task: CONTRACT-001
Status: DONE
Changed canonical files: schemas/external/production-handoff.v1.schema.json, schemas/production-result.schema.json, config/schema-registry.yaml, tools/lib/bundle.py, tools/lib/schema.py, tools/lib/validate.py, tools/new_production.py, tests/test_bootstrap.py, README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/` with `manifest.yaml`, `00_handoff/`, and `08_runtime/production-state.json`; no project data was added to Git
Commands executed: `python tools/export_handoff.py ...`; `python tools/new_production.py harmony-study ...`; `python tools/validate.py --project-root ...`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: `HO001` is READY with clean source provenance; production receipt `RC001` is ACCEPTED; project starts at `HANDOFF_VALIDATED`; bundle file-set and canonical hashes match
New validation rules: external schema snapshot, manifest hash, file-set hash, provenance cleanliness, payload marker separation, sidecar handling, offline `$ref` resolution, output-root boundary, idempotent project materialization
Approvals simulated: none; no publication, purchase, contract, payment, deletion, network schema fetch, or physical external effect was performed
Surprises and decisions: research common schema ID and synchronized `Icon\\r` metadata required offline resolver and file-set hardening
Remaining risks: budget/calendar/resource/material/selection input gaps remain explicit
Next READY task: `PLANNING-SCHEMA-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

### PLANNING-SCHEMA-001 handoff

```text
Task: PLANNING-SCHEMA-001
Status: DONE
Changed canonical files: schemas/planning.schema.json, schemas/selection-record.schema.json, schemas/scope-baseline.schema.json, schemas/assumption.schema.json, schemas/deliverable.schema.json, schemas/technical-spec.schema.json, schemas/acceptance-test.schema.json, schemas/material.schema.json, schemas/resource.schema.json, schemas/work-package.schema.json, schemas/task.schema.json, schemas/schedule.schema.json, schemas/budget.schema.json, schemas/risk.schema.json, schemas/approval-requirement.schema.json, schemas/approval-register.schema.json, schemas/coverage-report.schema.json, schemas/production-plan.schema.json, config/schema-registry.yaml, tools/lib/planning.py, tools/validate.py, tests/test_bootstrap.py, execution plan, task queue
Generated files: none in repository; all generated projects remain Git-external
Commands executed: `python -m unittest discover -s tests -v`; `python tools/validate.py --check`; `python tools/new_production.py smoke ...`; `git diff --check`
Results: schema registry, repository validator, and planning fixtures pass; external-effect READY gate and DAG cycle rejection are fixed
New validation rules: typed domain records, references, coverage, cycle/topological order, plan integrity, and external-effect readiness gate
Approvals simulated: none
Surprises and decisions: incomplete budget and schedule inputs remain explicit gaps rather than fabricated values
Remaining risks: prototype and runtime gates remain
Next READY task: `PLANNING-BUILD-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

### PLANNING-BUILD-001 handoff

```text
Task: PLANNING-BUILD-001
Status: DONE
Changed canonical files: tools/build_plan.py, tools/lib/planning.py, tests/test_bootstrap.py, README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/03_plan/` planning projections
Commands executed: `python tools/build_plan.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: deterministic `PL001` and coverage report generated; human brief and task-minimal context packs materialized; no external effect executed
New validation rules: plan generation idempotency, deterministic DAG, critical path, task context, human brief, and resource/material/approval references
Approvals simulated: none
Surprises and decisions: plan is kept PROVISIONAL while physical and external actions remain blocked
Remaining risks: prototype and runtime gates remain
Next READY task: `PROTOTYPE-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

### PROTOTYPE-001 handoff

```text
Task: PROTOTYPE-001
Status: DONE
Changed canonical files: schemas/prototype.schema.json, schemas/prototype-run.schema.json, schemas/prototype-test-result.schema.json, schemas/prototype-review.schema.json, schemas/iteration-decision.schema.json, schemas/change-request.schema.json, tools/lib/prototype.py, tools/build_prototype.py, tests/test_bootstrap.py, README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/04_prototype/` control projections; no physical output or external evidence was added
Commands executed: `python tools/build_prototype.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: `PC001` is schema-valid and remains `PLANNING`; physical run is `BLOCKED`, test is `NOT_RUN`, review is `NOT_STARTED`, and iteration decision is `WAITING_FOR_RUN`; no physical or external effect was executed
New validation rules: no PASS without execution and external validation; FAIL requires visible iteration decision; REVISE requires change request; MAJOR applied changes require research/human approval; CRITICAL changes cannot be applied
Approvals simulated: none; no physical work or external validation was performed
Surprises and decisions: handoff supplies a Prototype Plan but no actual run evidence, so control remains fail-closed
Remaining risks: runtime lease/retry/effect/approval gates remain
Next READY task: `RUNTIME-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

### DESIGN-002 handoff

```text
Task: DESIGN-002
Status: DONE
Changed canonical files: docs/20260811-agentic-art-production-implementation-contract-specification.md, PLANS.md, AGENTS.md, .gitignore, execution/task-queue.yaml, README.md
Generated files: none
Commands executed: Markdown fence check; dependency command check; YAML parse/dependency check; `git diff --check`
Results: execution plan and task queue are self-contained; protocol directory names, schemas, states, approval boundary, runtime, path safety, archive limits, and release gate are fixed
New validation rules: strict handoff manifest, canonical JSON/hashes, common scalar types, lifecycle transitions, approvals, path/archive safety, diagnostics, and release entry gates
Approvals simulated: none
Surprises and decisions: event log is canonical and state is a replay projection
Remaining risks: research handoff schema and export bundle are external dependencies
Next READY task: `BOOTSTRAP-001`
Exact restart command: `git status --short`
```

## Context and Orientation

このrepoはprotocol / schema / generator / validator / testだけをGit管理し、実制作projectはGit外へ生成する。Productionとresearchはhandoff/result schemaで接続し、production project stateは独立している。実装の正本はdesign specification、implementation contract、repository execution plan、PLANS、task queueである。

## Plan of Work

1. Bootstrap
2. Contract intake
3. Planning
4. Prototype control
5. Replayable runtime
6. Production execution and installation
7. Result feedback
8. Evaluation and release

## Concrete Steps

1. Read the design spec and implementation contract.
2. Select the lowest READY task from `execution/task-queue.yaml`.
3. Mark it IN_PROGRESS, implement schema/config/validator/generator/tests, then mark it DONE.
4. Run repository and project validation.
5. Record evidence and exact restart command.

## Validation and Acceptance

- `python3 -m unittest discover -s tests -v`
- `python3 tools/validate.py --check`
- `python3 tools/validate.py --project-root <external-project>`
- `python3 tools/run_runtime.py ... replay`
- `git diff --check`

## Idempotence and Recovery

Event log is append-only; state is a replay projection. Re-run with the same inputs and idempotency keys is a no-op; mismatched keys, invalid hashes, partial lines, and projection divergence fail closed.

## Interfaces and Dependencies

- research handoff schema snapshot is pinned in `config/schema-registry.yaml`.
- result schema is production-owned and copied by research as a pinned snapshot.
- generated projects stay outside this repository.
- external and physical effects require human approval/evidence.

## Outcomes & Retrospective

RUNTIME-002 is complete; execution can now be resumed at `EXECUTION-001`. The next implementation must add output versioning, quality/acceptance records, opaque asset references, and installation tracking without committing asset bodies or credentials.
