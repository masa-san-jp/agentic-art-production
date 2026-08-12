# Agentic Art Production リポジトリ実行計画

- 作成日: 2026-08-11
- 状態: `BOOTSTRAP-001` DONE / `CONTRACT-001` DONE / `PLANNING-SCHEMA-001` DONE / `PLANNING-BUILD-001` DONE / `PROTOTYPE-001` DONE / `RUNTIME-001` DONE / `RUNTIME-002` DONE / `EXECUTION-001` DONE / `FEEDBACK-001` DONE / `EVAL-001` DONE / `DOCS-001` DONE / `RELEASE-001` DONE
- 対応仕様: `docs/20260811-agentic-art-production-system-design-specification.md`
- 実装契約: `docs/20260811-agentic-art-production-implementation-contract-specification.md`
- 実行対象: GPT-5.6 LunaまたはClaude Sonnet級の、ファイル編集・コマンド実行・Git操作が可能なエージェント

## Purpose / Big Picture

完成後は、エージェントが`agentic-art-research`のhandoff bundleを検証して制作projectを作成し、採択、仕様化、工程・資源・予算・日程、試作、変更管理、本制作、設営、受入、結果還流を有限の状態機械で管理できる。

最低限の利用確認は次とする。

```bash
AAP_SMOKE_ROOT="$(mktemp -d /tmp/agentic-art-production-smoke.XXXXXX)"
python3 tools/new_production.py smoke --handoff tests/fixtures/handoff/minimal --output-root "$AAP_SMOKE_ROOT"
python3 tools/validate.py --check
python3 tools/validate.py --project-root "$AAP_SMOKE_ROOT/production/smoke"
python3 tools/build_plan.py --project-root "$AAP_SMOKE_ROOT/production/smoke"
python3 tools/build_prototype.py --project-root "$AAP_SMOKE_ROOT/production/smoke"
python3 tools/run_runtime.py --project-root "$AAP_SMOKE_ROOT/production/smoke" bootstrap --occurred-at 2026-08-12T18:00:00+09:00 --actor-kind SYSTEM --actor-id smoke/local
python3 tools/run_runtime.py --project-root "$AAP_SMOKE_ROOT/production/smoke" init-tasks --occurred-at 2026-08-12T18:00:01+09:00 --actor-kind SYSTEM --actor-id smoke/local
python3 tools/run_runtime.py --project-root "$AAP_SMOKE_ROOT/production/smoke" replay
python3 tools/run_execution.py --project-root "$AAP_SMOKE_ROOT/production/smoke" init
python3 tools/run_execution.py --project-root "$AAP_SMOKE_ROOT/production/smoke" replay
python3 tools/build_result.py --project-root "$AAP_SMOKE_ROOT/production/smoke" --result-id PR001 --generated-at 2026-08-12T18:00:02+09:00
python3 tools/export_result.py --project-root "$AAP_SMOKE_ROOT/production/smoke" --output "$AAP_SMOKE_ROOT/results/smoke/PR001"
python3 -m unittest discover -s tests -v
```

## Agent Operating Contract

- 会話履歴を前提にしない。`AGENTS.md`、設計仕様、本計画、task queueを正本とする。
- wire format、hash、共通型、状態遷移、runtime、approval、安全上限は実装契約仕様を正本とする。
- 依存完了済みの最小ID `READY` taskを一件だけ実行する。
- task開始時に`IN_PROGRESS`、完了時に`DONE`へ更新する。
- 仕様にない方針を黙って導入しない。可逆で保守的な実装を選びDecision Logへ残す。
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
- [x] (2026-08-12) `EXECUTION-001`: output version、quality、asset reference、installationのschema、append-only execution log、projection、CLI、asset URI security、冪等性、改ざん検出、外部検証待ちfixtureを実装。
- [x] (2026-08-12) `FEEDBACK-001`: `production-result` builder、最小manifest bundle exporter、同一result IDの冪等性・改ざん・境界・security検証を実装。Research側clean fixtureとのhandoff受理、result生成、dry-run/apply/再取込`ALREADY_APPLIED`を確認。
- [x] (2026-08-12) `EVAL-001`: `run_evaluation.py`で`COMPLETE`・`COMPLETE_WITH_GAPS`・`BLOCKED`を結果生成まで通し、offline E2E、traceability、determinism/idempotency、resume/effect、approval、security、chaos/recoveryを固定。評価matrixと決定性テスト、CLIを追加。
- [x] (2026-08-12) `DOCS-001`: `agent-startup.md`、`operations-runbook.md`、`schema-reference.md`と文書契約テストを追加。実装済みCLI、canonical/projection境界、diagnostic、lease/approval/effect、UNKNOWN、result/export、schema registryを会話履歴なしで再開できる形に整理。
- [x] (2026-08-12) `RELEASE-001`: `run_release_gate.py`、3回連続gate、Git外evidence、v1.0.0候補の人間承認境界を実装。clean main commitでvalidator・全test・EVALを3回連続PASSし、公開・tag・通知は人間承認待ち。

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
- 2026-08-12: Google Drive/macOSの同期ディレクトリには空の`Icon\\r` sidecarが自動生成され、manifest外fileとして受理を妨げた。wire payloadには含めず、受理時だけ明示的なfilesystem metadataとして無視するテストを追加した。
- 2026-08-12: `harmony-study`には金額・見積・会場日程が入力されていないため、予算をゼロと偽装せず金額nullのestimate gap、日程を絶対日時と偽装せずrelative scheduleとして計画化した。
- 2026-08-12: `AGENT_RECOMMENDED`の選択は`PROVISIONAL`のまま保持し、物理prototype taskは`AR001 REQUIRED`・`BLOCKED`にした。plan生成は実行承認や外部effectを意味しない。
- 2026-08-12: prototype controlは物理実行の代わりに`PRT001 BLOCKED`、`PTR001 NOT_RUN`、`RV001 NOT_STARTED`、`ITD001 WAITING_FOR_RUN`を生成する。結果がない状態をPASSへ補正しない。
- 2026-08-12: `FAIL`のprototypeには`REVISE`または`BLOCK` decisionを要求し、`REVISE`にはchange requestを要求する。MAJOR変更の適用にはresearch reviewとhuman approvalを要求し、CRITICAL変更はprototype control層で適用不可とする。
- 2026-08-12: runtime bootstrapでは、既存projectionが`HANDOFF_VALIDATED`ならeventを捏造せずrevision 0のbaselineを維持し、plan生成済みで`PLANNING`なら`EVT000001`の受理済みhandoff→planning transitionを記録する。
- 2026-08-12: execution registerは空状態を正規化し、output・quality・installationを同じappend-only logから再構成する。`harmony-study`へは空台帳だけを初期化し、生成物本体や外部実施結果は追加しない。
- 2026-08-12: production resultはprojectのhandoff、plan、prototype、runtime、execution projectionを再読込して構成し、`NOT_RUN`をPASSへ補正しない。result schemaが要求する`executed_at`は、未実施時には実行時刻ではなくresult生成時刻とし、opaque evidence URIと明示的なlimitationsを付ける。
- 2026-08-12: result bundleは`production-result.yaml`と`manifest.yaml`だけに限定し、asset bodyを含めない。既存のGit外`harmony-study`では`PR001`を生成・exportし、再実行で同一hashのno-opになることを確認した。
- 2026-08-12: Research側一時fixtureをdirtyのまま受理しようとすると`PROVENANCE_DIRTY_SOURCE`で拒否された。fixtureを一時Git commitして`source_tree_clean=true`にした正式経路では、Production受理からResearch importerのdry-run/apply/冪等再取込まで成功した。
- 2026-08-12: `build_plan.py`はplan生成時にruntimeを`PLANNING`へ進めるため、評価ハーネスは同じ遷移を二重記録せず、bootstrap後の状態からterminal scenarioを開始する。生成済み状態を前提にした再開契約を評価へ反映した。
- 2026-08-12: terminal scenarioは固定時刻・固定synthetic commit・opaque evidenceだけで再現でき、`COMPLETE`・`COMPLETE_WITH_GAPS`・`BLOCKED`を同じ結果builderで比較できる。chaos操作は一時project内だけで行い、元のlog/projection/bundleを復元してから評価を完了する。
- 2026-08-12: nested schemaのローカル定義はschema-ID付き絶対参照にする。外部schema参照後のresolver scopeに依存せず、networkなしの検証を安定させる。
- 2026-08-12: `production-state.json`の直接編集やevent logのpartial line・hash mismatch・state divergenceは自動repairせず拒否する。同じidempotency keyと同じ内容の再実行だけをno-opとして扱う。
- 2026-08-12: onboarding文書では、実装済みCLIを起点にproject生成、plan/prototype、runtime bootstrap/task graph、execution、result/exportまでを固定した。旧設計に残っていた未実装のproject一括runnerは最小smoke手順から除外し、実際のCLI列へ置き換えた。
- 2026-08-12: 運用復旧はcanonical logとprojectionを分け、partial line、hash divergence、expired lease、UNKNOWN effect、approval不一致、result/export境界を自動repairせず停止する契約として文書化した。
- 2026-08-12: 既存CIはvalidator・全test・EVALを個別に実行していたため、RELEASE-001では同じclean commitに対する3回連続判定を`run_release_gate.py`へ集約する。evidenceはcommit SHAと各stdout/stderr hashだけを持ち、Git外へ保存する。
- 2026-08-12: clean main commit `5d87da1d5450fcd6e07a85a0ed823f4992ed4c63`でRELEASE-001 gateを3回連続実行し、全runがPASSした。evidenceはGit外のrelease output rootへ保存し、repoにはtemporary outputを追加しない。
- 2026-08-11: handoff本体はacceptance testやPrototype PlanをID参照するため、handoff YAMLとschemaだけではoffline self-containedにならない。manifestにhypothesis、requirement、test、prototype、source indexのsnapshotを必須化する。
- 2026-08-11: `production-state.json`を単独正本にするとkill-and-resumeとreplay acceptanceが曖昧になる。hash chain付きevent logを正本、stateを検証済みprojectionに分離する。
- 2026-08-11: Bootstrap前のlocal PythonにはPyYAMLがなく、`tests/`と`tools/validate.py`も未作成である。DESIGN-002はMarkdown、Ruby標準YAML parser、dependency check、`git diff --check`で検証し、Python依存と正式validatorはBOOTSTRAP-001で作成する。
- 2026-08-11: システムPythonへPyYAML/jsonschemaを追加できる前提はないため、依存を`requirements.txt`とCIへ固定し、実行確認は一時virtual environmentで行った。repository内の`.venv`もtracked対象外とした。
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
| 2026-08-11 | minimal fixtureは受理機構の検証に限定し、research互換性の証明には使わない | dirty working treeを正本として受理 | clean source commitとimmutable exportをentry gateとして守る |
| 2026-08-12 | research consumerの外部待ちを解消するため、production result schemaの契約部分だけをM6本体から前倒しする | 未公開schemaをresearch側で仮定義する | result schemaの所有権をProductionに保ち、実結果の捏造なしにsnapshot入口を公開するため |
| 2026-08-12 | research側のschema/consumer release完了後も`CONTRACT-001`はREADY handoff/export bundleが揃うまでBLOCKEDに保つ | test-time fixtureを実運用handoffとして受理する | 上流の実制作判断とmanifest provenanceを捏造せず、Productionの受理境界を守るため |
| 2026-08-12 | bundle内のcommon schemaを優先し、宣言されたschema `$id`をoffline resolverへ登録する | production common schemaだけを固定し、research `$ref`をnetworkへ解決 | self-contained bundleの所有権とoffline受理を守るため |
| 2026-08-12 | `Icon\\r`はwire payloadに含めず、同期filesystem metadataとして受理走査から除外する | output rootをprotocol repo内へ移す、またはmetadataをpayloadとして宣言する | Git外の指定output rootを維持しつつ、manifestの完全性と実環境の再現性を両立するため |
| 2026-08-12 | planningの未入力金額・日程はnull/relative gapとして保持する | 0円・仮の日付を計画に埋める | 推定と実支出、相対期間と締切を混同しないため |
| 2026-08-12 | provisional selectionとphysical task approvalを分離する | `AGENT_RECOMMENDED`を本制作承認として扱う | 計画生成を許可しつつ、物理・外部行為の承認境界を守るため |
| 2026-08-12 | prototype結果は未実施を`NOT_RUN`として保存し、外部検証が必要なPASSを拒否する | 計画時点の予測をPASSとして保存 | AIが物理結果や観客反応を捏造しないため |
| 2026-08-12 | FAIL→iteration decision→change requestの順序を必須化する | FAIL結果を上書きして再試作 | 失敗の可視性とbaseline変更の追跡性を維持するため |
| 2026-08-12 | append-only JSONL event logをcanonical source、stateをreplay projectionとする | stateだけを更新する | 中断・改ざん・projection divergenceを検出するため |
| 2026-08-12 | runtime bootstrapはbaselineに不要なイベントを追加しない | すべての起動をevent化 | handoff受理とplanning生成の事実を混同しないため |
| 2026-08-12 | output/quality/installationは単一のexecution logからYAML projectionを再構成する | 各registerを個別に直接更新する | 冪等性、hash chain、改ざん検出、crash recoveryを同じ契約で扱うため |
| 2026-08-12 | `AVAILABLE`はlinked PASS quality、`SUCCEEDED`はsafety PASSとevidenceを要求する | statusだけで成果物や設営完了とみなす | 未実施・外部検証待ちを実施済みと誤認しないため |
| 2026-08-12 | result exportは結果YAMLとmanifestだけの最小bundleにする | project全体やasset bodyをbundleへ複製する | Research consumerの受理単位を明確にし、private data・大容量asset・credentialの境界外流出を防ぐため |
| 2026-08-12 | `result_id`の同一内容再実行はno-op、内容差分は拒否する | 既存resultを上書きする | downstream evidenceの参照を安定させ、結果の改変をfail closedにするため |
| 2026-08-12 | EVAL-001は一時Git外projectを使う決定的CLIに集約する | 個別テストを人手で順番に実行する | terminal scenario、security、chaos、resumeの評価を同じrelease gateで再現し、パス/失敗の根拠をJSONで取得するため |
| 2026-08-12 | approval/chaos評価はruntime記録と破損検出だけを行い、外部effectは実行しない | 実サービスや物理adapterへ接続する | protocol repoの安全境界を維持しながら、期限・hash・revoke・改ざん時のfail-closedを検証するため |
| 2026-08-12 | onboarding、運用復旧、schema referenceを独立文書にし、文書契約テストでCLI名・registry path・安全境界を固定する | READMEへ手順を集約し、文書をtest対象にしない | 新規エージェントが会話履歴なしで再開でき、存在しないCLIやmachine固有pathの再導入を検出するため |
| 2026-08-12 | v1.0.0候補のgateは同一clean commitで3回連続実行し、evidenceをGit外に保存する | gateを手動で一度だけ実行し、結果をrepoへ生成物としてcommitする | 再現可能な判定とcommitの自己参照循環を避け、protocol repoへtemporary/generated evidenceやmachine pathを混入させないため |

## Outcomes & Retrospective

設計段階では、researchの芸術判断を保ったまま実制作に必要な運用情報を独立管理する構造に加え、実装者へ残っていたwire format、hash、共通型、runtime、承認、安全上限を確定した。`BOOTSTRAP-001`では、設定・schema・安全検査・canonical serializer・bundle loader・project generator・CI・正常/失敗/再実行テストを追加した。minimal fixtureによる機構確認は完了したが、handoff互換性の最終確定はresearch側clean commitのschemaとexpected bundleを外部entry gateとして残している。

research側のProduction result schema/consumer連携は、Production commit `fb15f32`のschema snapshotを`agent/handoff-build`へ適用し、consumer E2Eと`--require-schema-snapshot` gate 3回を完了した。続いて`harmony-study`の実出力をPRODUCTION_HANDOFFへ拡張し、`HO001 READY`のclean bundleを生成した。Productionはbundleをnetworkなしで検証し、`RC001 ACCEPTED`、`HANDOFF_VALIDATED`のproduction projectをGit外output rootへmaterializeした。計画とprototype controlまで完了し、次の開始点は`RUNTIME-001`である。

`PLANNING-SCHEMA-001`では、scope baseline、selection、assumption、deliverable、technical specification、acceptance-test fixture、material、resource、WBS/task、schedule、budget、risk、approval requirement、coverageのcanonical schemaを追加した。`PLANNING-BUILD-001`では、受理済み`harmony-study`から`PL001`を決定的に生成し、`RQ001`のcoverage 100%、DAG、critical path、human brief、task-minimal contextをGit外output rootへ書き出した。選択は`PROVISIONAL`、`TK001/TK002`は`AR001`待ちでBLOCKED、金額は未入力のためestimate gap、日程はrelativeであり、実行・購入・契約・公開・物理作業は行っていない。

### DESIGN-002 handoff

```text
Task: DESIGN-002
Status: DONE
Changed canonical files: AGENTS.md, README.md, system design, implementation contract, execution plan, task queue
Generated files: none
Commands executed: git diff --check; Markdown fence check; task queue YAML/dependency check; project ID regex reference test; unittest and validate entrypoint probes
Results: document structure and queue dependencies pass; BOOTSTRAP-001 is the sole READY task; unittest and validate cannot start because tests/ and tools/validate.py do not yet exist
New validation rules: normative bundle, hash, scalar, lifecycle, runtime, approval, diagnostic, URI, archive, and input-limit contracts fixed for BOOTSTRAP implementation
Approvals simulated: none
Surprises and decisions: research handoff implementation is uncommitted; self-contained export snapshots are required; event log is canonical and state is a projection
Remaining risks: CONTRACT-001 requires a clean research commit containing the schema and expected self-contained export bundle
Next READY task: BOOTSTRAP-001
Exact restart command: git status --short
```

### CONTRACT-001 resume audit

```text
Task: CONTRACT-001
Status: DONE
Changed canonical files: tools/lib/schema.py, tools/lib/bundle.py, tests/test_bootstrap.py, execution/task-queue.yaml, execution plan, README.md
Generated files: Git-external `Agentic-Art-Output/harmony-study/production-handoff` bundle and `Agentic-Art-Output/production/harmony-study` accepted project
Commands executed: research handoff source preparation; `build_handoff.py` for `HO001`; `export_handoff.py` from clean staging source; production `new_production.py`; production validator; 14-test unittest suite; `git diff --check`
Results: `HO001` is READY with `source_tree_clean: true`; production accepted the self-contained bundle as `RC001 ACCEPTED`; materialized project state is `HANDOFF_VALIDATED`; production validator and 14 tests pass; research and production repositories remain free of project output
New validation rules: registry-local schema snapshots are Draft 2020-12 checked; same handoff ID/revision/hash reaccepts an intact project; mismatched or invalid existing projects fail with PROJECT_IDEMPOTENCY_MISMATCH
Approvals simulated: none; no publication, purchase, contract, payment, deletion, network schema fetch, or physical external effect was performed
Surprises and decisions: the first real-bundle attempt exposed the research common-schema `$ref` resolver mismatch, schema vocabulary being scanned as payload, and synchronized `Icon\\r` sidecars. These were fixed conservatively and covered offline before acceptance.
Remaining risks: agent selection remains provisional (`AGENT_RECOMMENDED`); runtime, physical production, and external approval gates remain unimplemented or pending
Next READY task: `RUNTIME-001`
Exact restart command: git status --short
```

### PLANNING-SCHEMA-001 / PLANNING-BUILD-001 handoff

```text
Task: PLANNING-SCHEMA-001 / PLANNING-BUILD-001
Status: DONE
Changed canonical files: schemas/, config/schema-registry.yaml, tools/lib/schema.py, tools/lib/planning.py, tools/build_plan.py, tools/validate.py, tests/test_bootstrap.py, README.md, schemas/README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/01_scope/`, `02_specification/`, `03_plan/`, `07_governance/`, and `08_runtime/` planning projections
Commands executed: `python tools/build_plan.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --check`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: `PL001` is schema-valid and deterministic; `RQ001` coverage is 100%; task DAG and critical path are valid; human brief and three task-minimal contexts are generated; project state is `PLANNING`
New validation rules: domain entry schemas, aggregate plan integrity, requirement coverage, reference existence, deterministic topological order, cycle rejection, and external-effect READY rejection
Approvals simulated: none; `AR001` is recorded as REQUIRED only; no purchase, contract, publication, deletion, network fetch, or physical external effect was performed
Surprises and decisions: absent budget and calendar inputs remain explicit gaps (`null` estimate amounts and relative schedule); `AGENT_RECOMMENDED` remains `PROVISIONAL` and physical tasks remain `BLOCKED`
Remaining risks: venue lighting gap `GP001`, material safety review, missing quote/supplier/calendar inputs, and runtime gates
Next READY task: `RUNTIME-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

### PROTOTYPE-001 handoff

```text
Task: PROTOTYPE-001
Status: DONE
Changed canonical files: schemas/prototype.schema.json, schemas/prototype-*.schema.json, schemas/iteration-decision.schema.json, schemas/change-request.schema.json, config/schema-registry.yaml, tools/lib/prototype.py, tools/build_prototype.py, tools/validate.py, tests/test_bootstrap.py, README.md, schemas/README.md, execution plan, task queue
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/04_prototype/` and `07_governance/change-requests.yaml`
Commands executed: `python tools/build_prototype.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --project-root <output-root>/production/harmony-study`; `python tools/validate.py --check`; `python -m unittest discover -s tests -v`; `git diff --check`
Results: `PC001` is schema-valid and deterministic; `PRT001` is `BLOCKED` by `AR001`; `PTR001` is `NOT_RUN`; `RV001` is `NOT_STARTED`; `ITD001` is `WAITING_FOR_RUN`; no physical or external effect was performed
New validation rules: NOT_RUN cannot carry execution time; PASS requires execution and cannot bypass external validation; FAIL requires a visible REVISE/BLOCK decision; REVISE requires a change request; MAJOR applied changes require research and human approval; CRITICAL changes cannot be applied by this layer
Approvals simulated: none; `AR001` remains REQUIRED only
Surprises and decisions: the current handoff contains one prototype plan (`PP001`) but no external evidence, so control remains in `PLANNING` and the run is blocked rather than promoted to `PROTOTYPING`
Remaining risks: venue lighting gap `GP001`, material safety review, missing quote/supplier/calendar inputs, and runtime event/approval enforcement
Next READY task: `RUNTIME-001`
Exact restart command: `git status --short && python tools/validate.py --check`
```

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

### EXECUTION-001 handoff

```text
Task: EXECUTION-001
Status: DONE
Changed canonical files: schemas/output-version.schema.json, schemas/output-versions.schema.json, schemas/quality-result.schema.json, schemas/quality-results.schema.json, schemas/installation-plan.schema.json, schemas/installation-result.schema.json, schemas/installation-results.schema.json, schemas/execution-event.schema.json, tools/lib/execution.py, tools/run_execution.py, tools/validate.py, config/schema-registry.yaml, tests/test_execution.py, README.md, schemas/README.md, execution/task-queue.yaml, execution plan
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/05_execution/production-log.jsonl`, `output-versions.yaml`, `quality-results.yaml`, `06_installation/installation-plan.yaml`, and `installation-results.yaml`; all registers are revision 0 and contain no asset body
Commands executed: `.venv/bin/python tools/validate.py --check`; `.venv/bin/python -m unittest tests.test_execution -v`; `.venv/bin/python -m unittest discover -s tests -v`; `.venv/bin/python tools/run_execution.py --project-root <output-root>/production/harmony-study init --occurred-at 2026-08-12T17:30:00+09:00 --actor-kind SYSTEM --actor-id execution/local`; `.venv/bin/python tools/run_execution.py --project-root <output-root>/production/harmony-study replay`; `.venv/bin/python tools/validate.py --project-root <output-root>/production/harmony-study`; `git diff --check`
Results: execution fixture, repository validation, project validation, and replay pass; synthetic tests cover empty initialization, linked PASS quality, AVAILABLE output gating, external-validation-pending installation, idempotency mismatch, projection tamper, and missing installation plan references
New validation rules: asset URI scheme/credential/query/fragment checks, output deliverable/task references, PASS-before-AVAILABLE, explicit NOT_RUN and EXTERNAL_VALIDATION_REQUIRED states, approval/effect target gate, safety/evidence-before-SUCCEEDED, contiguous execution event hash chain, atomic projection integrity, and no automatic repair
Approvals simulated: none; no physical work, purchase, contract, publication, deletion, network fetch, credential handling, or external effect was performed
Surprises and decisions: output availability links a PASS result by opaque quality ID; the output may be a later immutable revision that supersedes the checked candidate, so quality evidence is preserved without overwriting a record. Local schema definitions use schema-ID-qualified references so offline resolution remains stable across nested external refs
Remaining risks: `harmony-study` has no output, quality, approval, or installation result yet; physical tasks remain gated by resource/material/approval conditions. `FEEDBACK-001` must build the production-result export from these projections without copying asset bodies
Next READY task: `FEEDBACK-001`
Exact restart command: `git status --short && .venv/bin/python tools/run_execution.py --project-root <output-root>/production/harmony-study replay`
```

### EVAL-001 handoff

```text
Task: EVAL-001
Status: DONE
Changed canonical files: tools/lib/evaluation.py, tools/run_evaluation.py, tests/test_evaluation.py, README.md, execution/task-queue.yaml, execution plan
Generated files: none; every evaluation project, result bundle, and mutation is temporary Git-external data
Commands executed: `.venv/bin/python -m unittest tests.test_evaluation -v`; `.venv/bin/python tools/run_evaluation.py --format json`; full repository validation and test gate; the same evaluation matrix twice for determinism
Results: `EVAL-001: PASS`; terminal scenarios `COMPLETE`, `COMPLETE_WITH_GAPS`, `BLOCKED`; offline E2E and traceability pass; resume reaches `TK004` attempt 3 and effect duplicate is a no-op; approval rules `RUNTIME_APPROVAL_EXPIRED`, `RUNTIME_APPROVAL_HASH_MISMATCH`, `RUNTIME_APPROVAL_REVOKED`, `RUNTIME_APPROVAL_WILDCARD` pass; security/chaos rules `PRIVATE_MARKER`, `RUNTIME_PARTIAL_LINE`, `RUNTIME_STATE_DIVERGENCE`, `RESULT_EXPORT_IDEMPOTENCY_MISMATCH` pass
New validation rules: evaluation reports use fixed injected timestamp/commit, contain no temporary absolute paths, exercise all declared terminal states, and fail if a scenario or category is missing
Approvals simulated: synthetic HUMAN approval records only; no external effect, physical work, purchase, contract, publication, deletion, credential handling, or network fetch was executed
Remaining risks: onboarding and operational recovery documentation are still the next task; release gate must run three consecutive times after documentation is complete
Next READY task: `DOCS-001`
Exact restart command: `git status --short && .venv/bin/python tools/run_evaluation.py --format text`
```

### DOCS-001 handoff

```text
Task: DOCS-001
Status: DONE
Changed canonical files: docs/agent-startup.md, docs/operations-runbook.md, docs/schema-reference.md, tests/test_documentation.py, AGENTS.md, README.md, execution/task-queue.yaml, execution plan
Generated files: none; all project and result examples use temporary Git-external output roots
Commands executed: `.venv/bin/python tools/validate.py --check --format json`; `.venv/bin/python -m unittest tests.test_documentation -v`; `.venv/bin/python -m unittest discover -s tests -v`; `.venv/bin/python tools/run_evaluation.py --format json`; `git diff --check`
Results: startup, operations/recovery, schema reference, CLI inventory, registry paths, safety boundary, and stale-command contract tests pass; repository validator, full test suite, and EVAL-001 pass
New validation rules: documentation must not refer to the unimplemented legacy project runner or machine-specific absolute paths; documented CLI names must exist; schema registry paths and IDs must be unique and readable
Approvals simulated: none; no external effect, physical work, purchase, contract, publication, deletion, credential handling, or network fetch was executed
Surprises and decisions: canonical logs remain the recovery source and projections are never auto-repaired. The startup guide uses the minimal handoff fixture and explicit timestamps so a new agent can reproduce the lifecycle without conversation history or external service access
Remaining risks: release gate and three consecutive evidence runs remain. Actual `harmony-study` is still `PLANNING`; `PR001` has no outputs, `AT001` is `NOT_RUN`, and `GP001` remains open
Next READY task: `RELEASE-001`
Exact restart command: `git status --short && .venv/bin/python tools/run_evaluation.py --format json`
```

### RELEASE-001 handoff

```text
Task: RELEASE-001
Status: DONE
Changed canonical files: tools/lib/release.py, tools/run_release_gate.py, tests/test_release.py, tests/test_documentation.py, docs/release-gate.md, AGENTS.md, README.md, execution/task-queue.yaml, execution plan
Generated files: Git-external release evidence only; no release evidence, tag, asset body, credential, or publication record was added to the protocol repository
Commands executed: `.venv/bin/python tools/validate.py --check --format json`; `.venv/bin/python -m unittest discover -s tests -v`; `.venv/bin/python tools/run_evaluation.py --format json`; `.venv/bin/python tools/run_release_gate.py --runs 3 --evidence <output-root>/release/agentic-art-production/v1.0.0/release-gate-<verified-commit-prefix>.yaml --format text`; `git diff --check`
Results: `RELEASE-001 PASS`; clean main commit `5d87da1d5450fcd6e07a85a0ed823f4992ed4c63` passed repository validation, full tests, and EVAL in runs 1, 2, and 3. Evidence records the verified commit, PASS statuses, exit codes, and stdout/stderr hashes.
New validation rules: release evidence requires candidate `v1.0.0`, a clean working tree, one verified commit across all runs, PASS validator output, PASS evaluation output, and consecutive run success. Evidence paths inside the protocol repository are rejected.
Approvals simulated: none; publication, tag creation, release notification, external effect, physical work, purchase, contract, deletion, credential handling, and network fetch were not performed
Surprises and decisions: release evidence is deliberately Git-external to avoid a commit/evidence self-reference cycle. `publication_status` remains `HUMAN_APPROVAL_REQUIRED`.
Remaining risks: v1.0.0 is a verified candidate only; explicit human approval is still required before tag, publication, distribution, or external notification. Actual `harmony-study` remains `PLANNING` with no physical output.
Next READY task: none
Exact restart command: `git status --short && .venv/bin/python tools/run_release_gate.py --runs 3 --format text`
```

### FEEDBACK-001 handoff

```text
Task: FEEDBACK-001
Status: DONE
Changed canonical files: tools/lib/result.py, tools/build_result.py, tools/export_result.py, tests/test_result.py, README.md, schemas/README.md, schemas/external/README.md, execution/task-queue.yaml, execution plan
Generated files: Git-external `Agentic-Art-Output/production/harmony-study/08_runtime/production-result.yaml` and `Agentic-Art-Output/feedback/harmony-study/PR001/{production-result.yaml,manifest.yaml}`; no asset body or external effect was added
Commands executed: `.venv/bin/python tools/validate.py --check --format json`; `.venv/bin/python -m unittest discover -s tests -v`; result builder twice with fixed `PR001`, generated_at, and production commit; result exporter twice; `tools/validate.py --project-root <output-root>/production/harmony-study`; Research↔Production temporary clean-fixture E2E through handoff acceptance, plan/prototype/execution initialization, result generation, Research dry-run, apply, and repeated apply
Results: 34 Production tests pass; repository/project validation pass; actual `harmony-study` result hash is `sha256:6a0630bc594f40cff8e7782b4f47e1aa3f466ccb0902eb0ec456c60fc95aa74e`; external PR001 bundle contains exactly `manifest.yaml` and `production-result.yaml`; Research consumer returns `DRY_RUN`, `APPLIED`, then `ALREADY_APPLIED`
New validation rules: canonical result hash, result ID idempotency, output/test URI checks, private/signed URL/path security scan, result bundle boundary, raw file hashes, canonical file-set hash, and minimal external bundle contents
Approvals simulated: none; no physical work, purchase, contract, publication, deletion, credential handling, network fetch, or external effect was executed
Remaining risks: actual `harmony-study` remains `PLANNING`; `PR001` contains no outputs, `AT001` is `NOT_RUN`, and `GP001` remains open. `EVAL-001` must add representative security/chaos/recovery coverage before release work
Next READY task: `EVAL-001`
Exact restart command: `git status --short && .venv/bin/python -m unittest discover -s tests -v && .venv/bin/python tools/validate.py --check`
```

### BOOTSTRAP-001 handoff

```text
Task: BOOTSTRAP-001
Status: DONE
Changed canonical files: requirements.txt, .gitignore, config/, schemas/, templates/project/, tools/, tests/, .github/workflows/validate.yml, README.md, execution plan, task queue
Generated files: none in the repository; materialized smoke project was written to a temporary Git-external output root
Commands executed: `<venv>/bin/python -m py_compile tools/lib/*.py tools/validate.py tools/new_production.py`; `<venv>/bin/python tools/validate.py --check --format json`; `<venv>/bin/python -m unittest discover -s tests -v`; `<venv>/bin/python tools/new_production.py smoke-final --handoff tests/fixtures/handoff/minimal --output-root <temporary-output-root> --format json`; `<venv>/bin/python tools/validate.py --project-root <temporary-output-root>/production/smoke-final --format json`; `git diff --check` (the checks used a temporary venv because the system Python had no project dependencies)
Results: syntax check exit 0; repository validator exit 0 with []; 10 unit tests exit 0; directory and ZIP bundle tests pass; tampered bundle, duplicate YAML, duplicate project, and repository output-root rejection tests pass; generated project validator exit 0 with []
New validation rules: canonical JSON rejects float and non-JSON scalar values; duplicate YAML/JSON keys, invalid JSONL, schema references, path traversal, symlink/special files, forbidden extensions, private markers, signed URLs, archive limits, manifest raw hashes, file-set hashes, handoff canonical hash, provenance cleanliness, and output-root boundary are enforced
Approvals simulated: none; no external, physical, publication, purchase, contract, deletion, or network effect was executed
Surprises and decisions: system Python lacked PyYAML/jsonschema, so pinned dependencies were installed only in a temporary virtual environment; the minimal fixture validates the mechanism but is not a research compatibility proof
Remaining risks: `CONTRACT-001` cannot be completed until the research repository provides a clean immutable 40-character source commit, schema snapshot raw hash, and expected self-contained export bundle
Next READY task: none; `CONTRACT-001` is BLOCKED by the external handoff gate
Exact restart command: git status --short
```

## Context and Orientation

### 正本

- system design: `docs/20260811-agentic-art-production-system-design-specification.md`
- implementation contract: `docs/20260811-agentic-art-production-implementation-contract-specification.md`
- execution plan: 本書
- plan format: `PLANS.md`
- machine queue: `execution/task-queue.yaml`
- configuration: `config/`（BOOTSTRAP-001で作成）
- schemas: `schemas/`（BOOTSTRAP-001で作成、external handoff snapshotはCONTRACT-001で登録）
- canonical projects: Git外の明示output rootにある`production/<project-slug>/`
- generated outputs: `data/`

### 上流

- repository: `masa-san-jp/agentic-art-research`
- extension design: `docs/20260811-agentic-art-research-production-handoff-extension-specification.md`
- request schema owner: research
- result schema consumer: research

### 用語

- **handoff**: researchが生成する版固定の制作入力。
- **scope baseline**: 採択済み仮説、要件、除外、仮定の承認済み基準線。
- **deliverable**: 完成・検証の対象となる成果単位。
- **Work Package**: 一つのreview可能な出力を作るtask集合。
- **effect**: taskがrepo、外部system、物理世界へ与える一意な作用。
- **production result**: 制作条件、出力、試験、逸脱、観察をresearchへ返す契約。

## Milestone M0: Bootstrap

### Goal

別セッションのエージェントが、実装順序、安全境界、完了条件をrepoだけから判断できる。

### Work

1. `config/`、`schemas/`、`templates/project/`、`tools/`、`tests/`、`data/`の骨格を追加する。
2. Python 3.11、PyYAML、jsonschemaの依存を固定する。
3. common ID、status、timestamp、money、quantity、URI、hash schemaを追加する。
4. YAML duplicate key、JSON/JSONL、schema registryを検証する基礎validatorを作る。
5. secret、forbidden extension、symlink、path traversalの基礎検査を作る。
6. unit testとGitHub Actionsを接続する。
7. empty project templateと`new_production.py`を追加する。
8. 実装契約仕様のmoney、quantity、timestamp、URI、diagnostic、event、approval共通型をconfig/schemaへ落とす。
9. 実projectが明示output root以外へ生成されないrepository boundary testを追加する。

### Acceptance

```bash
AAP_BOOTSTRAP_ROOT="$(mktemp -d /tmp/agentic-art-production-bootstrap.XXXXXX)"
python3 tools/new_production.py smoke --handoff tests/fixtures/handoff/minimal --output-root "$AAP_BOOTSTRAP_ROOT"
python3 tools/validate.py --check
python3 tools/validate.py --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
python3 -m unittest discover -s tests -v
```

正常fixtureはexit 0、不正fixtureは一つのnamed ruleでexit 1となる。

加えて、canonical JSON reference vector、Decimal round-trip、unit vocabulary、URI policy、diagnostic JSON、project ID、output-root boundaryが正常・失敗fixtureで固定される。

## Milestone M1: Handoff Contract

### Goal

researchから受け取ったbundleを、改変、非互換、機密漏洩なしに受理または拒否できる。

### Work

1. research handoff schemaのimmutable snapshotとprovenance manifestを追加する。
2. `handoff-receipt.schema.json`を追加する。
3. archiveを展開前検査し、manifest宣言pathだけを安全な一時rootへ解決する。
4. schema version、source commit、canonical hash、必須要件、test接続を検証する。
5. 同一handoff ID/revisionの再受理を冪等化し、異内容を拒否する。
6. `new_production.py --handoff`でreceiptとproject manifestを生成する。
7. rejectionにrule、location、reason、remediationを含める。
8. hypothesis、comparison、requirement、acceptance test、Prototype Plan、source-ref indexがbundle内で解決することを検証する。
9. directory/ZIP、file set、raw-byte hash、canonical payload hash、input limitを実装契約仕様どおり検査する。

### Acceptance

- 正常handoffを二回受理してもprojectが重複しない。
- hash改変、schema mismatch、path traversal、秘密、署名付きURLを拒否する。
- 外部ネットワークなしでsnapshot検証できる。
- 隣接research working treeを参照せず、clean commit、schema raw hash、expected bundle fixtureで互換性を証明する。
- research側schema/export fixtureがclean commitで未固定なら、draft fixture testは許可しても`CONTRACT-001`をDONEにしない。

## Milestone M2: Planning Domain

### Goal

採択済みhandoffから、依存と根拠を持つ実行可能な制作計画を生成できる。

### Work

1. scope baseline、assumption、selection schemaを追加する。
2. deliverable、technical spec、material、resource、asset ref schemaを追加する。
3. Work Package、Task DAG、milestone、schedule schemaを追加する。
4. budget、quote ref、contingency、actual variance schemaを追加する。
5. risk、approval、procurement candidate schemaを追加する。
6. 全handoff requirementが一つ以上のdeliverable/testへ接続するcross-reference検証を作る。
7. DAG cycle、missing dependency、unit/currency欠落、未根拠actualを拒否する。
8. `build_plan.py`、dependency graph、critical path、coverage reportを実装する。
9. human briefとtask-minimal agent contextを生成する。

### Acceptance

```text
HO001 → SB001 → DL001 → TS001 → WP001 → TK001
                    └────────────→ AT001
TK001 → MT001 / RS001 / BI001 / MS001 / RK001 / AP001
```

がgraphで確認でき、RQ001のplanning coverageが100%になる。

## Milestone M3: Prototype and Change Control

### Goal

重大な不確実性を小さな試作で検証し、失敗をbaselineへ安全に反映できる。

### Work

1. prototype run、test result、review、iteration decision schemaを追加する。
2. technical/artistic/requirement/rights/feasibility reviewを区別する。
3. `EXTERNAL_VALIDATION_REQUIRED`と解除条件を実装する。
4. change request、影響分類、代替案、承認を実装する。
5. baseline revisionとsupersede関係を追加する。
6. 未実施testをPASSにできないvalidatorを追加する。
7. prototype fail→change request→再試作のfixtureを追加する。

### Acceptance

- FAILが失敗として保存され、結果を消さずにrevision 2へ進める。
- MAJOR changeはresearch reviewなしにbaselineへ適用できない。
- CRITICAL changeはstateをBLOCKEDへ遷移させる。

## Milestone M4: Runtime and Approvals

### Goal

taskを依存順に有限実行し、中断、再試行、外部待ち、承認待ちから安全に再開できる。

### Work

1. production state schemaと合法遷移設定を追加する。
2. append-only run logとstate replay検査を追加する。
3. Task DAG claim、期限付きlease、heartbeat、expiry recoveryを実装する。
4. failure classificationとbounded retryを実装する。
5. effect type、effect key、duplicate preventionを実装する。
6. approval scope、target hash、expiry、revocationを実装する。
7. task-minimal context packを実装する。
8. stopping policyとiteration/budget/task上限を実装する。
9. kill-and-resume、expired lease、duplicate effect、approval revocationをテストする。

### Acceptance

- 同一fixtureを途中kill後に再開して同じterminal outputになる。
- repo writeが重複せず、external/physical effectはfake adapter記録だけで検証する。
- 承認対象hashが変わると古い承認を使用できない。

## Milestone M5: Production and Installation

### Goal

asset本体をGitへ置かず、制作版、品質結果、会場・設営結果を追跡できる。

### Work

1. output version、asset register、quality result schemaを追加する。
2. opaque asset URI、content hash、version、rights statusを検証する。
3. production logをtask/effect/outputへ接続する。
4. venue constraint、installation plan/result schemaを追加する。
5. 素材lot、会場、技術仕様変更の再検証triggerを追加する。
6. fake asset store adapterでoffline E2Eを作る。
7. forbidden binary、credential URI、unsafe external pathを拒否する。

### Acceptance

- fixture outputがasset URIとhashだけで追跡できる。
- asset URIの認証情報、ローカル外path、hash不一致を拒否する。
- installation対象外projectは理由付きで工程をskipできる。

## Milestone M6: Result and Research Feedback

### Goal

制作結果をresearchが検証・取込できる決定的bundleとして出力する。

### Work

1. `production-result.schema.json`を確定する。
2. output、test、observation、deviation、incident、change request、gapを集約する。
3. handoff ID/hash、両repo commit、schema version、payload hashを固定する。
4. `build_result.py`と`export_result.py`を実装する。
5. research側互換schema snapshotとexpected fixtureを照合する。
6. 同じ正本からbyte-identical resultを生成する。
7. resultのraw asset、PRIVATE_RAW、secret混入を検査する。

### Acceptance

- research fixtureのdry-run importがexit 0。
- FAIL、DEVIATION、CRITICALを成功結果へ補正しない。
- result bundleはmanifest宣言外ファイルを含まない。

## Milestone M7: Representative System and Release

### Goal

合成データだけでhandoff受理からresult還流まで完走し、release可能性を客観判定する。

### Work

1. `harmony-production` fixtureを作る。
2. normal、COMPLETE_WITH_GAPS、BLOCKEDの各終端を再現する。
3. schema、reference、coverage、determinism、resume、approval、安全のevalを追加する。
4. API停止、破損JSONL、expired lease、duplicate effect、tampered archiveのchaos testを追加する。
5. onboarding、通常運用、障害対応、model startup prompt、schema referenceを追加する。
6. local release gateとCIを実装する。
7. CI相当gateを3回連続で実行し、commit SHA付きevidenceを記録する。

### Acceptance

- 全unit、contract、integration、E2E、security、chaos testが合格する。
- research↔production fixtureのschema/hashが一致する。
- release gateが3回連続exit 0。
- 公開は人間の明示承認後だけ実行する。

## Concrete Steps

各task開始前:

```bash
git status --short
sed -n '1,420p' docs/20260811-agentic-art-production-system-design-specification.md
sed -n '421,900p' docs/20260811-agentic-art-production-system-design-specification.md
sed -n '1,280p' docs/20260811-agentic-art-production-implementation-contract-specification.md
sed -n '281,620p' docs/20260811-agentic-art-production-implementation-contract-specification.md
sed -n '1,430p' docs/20260811-agentic-art-production-repository-execution-plan.md
sed -n '1,240p' execution/task-queue.yaml
```

各task完了前:

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate.py --check
git diff --check
git status --short
```

未実装でコマンドが存在しない段階は、該当task内で作成した直後から必須にする。検査を黙ってskipせず、Progressへ理由を記録する。

## Validation and Acceptance

最低gate:

1. **Unit** — 正常・境界・失敗。
2. **Schema** — 全canonical fileとinvalid fixture。
3. **Contract** — handoff receiptとproduction result。
4. **Traceability** — handoff requirementからoutput/testまで。
5. **Planning** — DAG、coverage、resource、budget、schedule。
6. **Runtime** — transition、lease、retry、resume、effect。
7. **Approval** — scope、hash、expiry、revocation。
8. **Safety** — private data、secret、path、archive、asset URI。
9. **Determinism** — 同一入力・同一注入時刻で同一出力。
10. **E2E** — offline handoffからterminal resultまで。

## Idempotence and Recovery

- `new_production.py`は既存projectを上書きしない。
- 生成系はtemp fileへ出力し、検証後にatomic replaceする。
- 同じhandoff ID/hashの再受理は成功し、projectを複製しない。
- 同じeffect keyは二重適用しない。
- 同じresult ID/hashは同じbundleを返し、異内容なら失敗する。
- lease expiry後は正本stateとeffect recordから再取得する。
- 外部systemの成功不明時は自動retryせず、reconciliation taskを作る。
- rollbackでユーザーasset、外部記録、承認recordを削除しない。
- generated data破損時はcanonical filesから再生成する。

## Interfaces and Dependencies

### Public CLI

- `tools/new_production.py`
- `tools/validate.py`
- `tools/run_runtime.py`
- `tools/run_execution.py`
- `tools/build_plan.py`
- `tools/build_prototype.py`
- `tools/build_result.py`
- `tools/export_result.py`
- `tools/run_evaluation.py`
- `tools/run_release_gate.py`

### Exit codes

- `0`: success
- `1`: validation or acceptance failure
- `2`: usage or local configuration error
- `3`: external dependency blocked
- `4`: human approval required

### Dependencies

- Python 3.11以上
- PyYAML 6.x
- jsonschema 4.x
- hash、decimal、datetime、archive検査は標準libraryを優先
- production service、asset store、calendar、procurementはadapter interfaceの後ろへ置く
- 初期releaseでnetwork、DB、Web UIを必須にしない

## Task Handoff Template

各task終了時に、本計画とtask queueを更新し、次を残す。

```text
Task:
Status:
Changed canonical files:
Generated files:
Commands executed:
Results:
New validation rules:
Approvals simulated:
Surprises and decisions:
Remaining risks:
Next READY task:
Exact restart command:
```
