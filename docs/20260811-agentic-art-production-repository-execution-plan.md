# Agentic Art Production リポジトリ実行計画

- 作成日: 2026-08-11
- 状態: `BOOTSTRAP-001` DONE / `CONTRACT-001` IN_PROGRESS（READY export gate待ち）
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
- 仕様にない方針を黙って導入しない。可逆で保守的な実装を選びDecision Logへ残す。
- 実作品、私的原文、credential、signed URL、契約書原本をfixtureやGitへ入れない。
- 外部接続、購入、契約、公開、削除、物理作業をテストや実装中に実行しない。
- 外部serviceがなくても合成fixtureで全工程を検証できるようにする。

## Progress

- [x] (2026-08-11) `DESIGN-001`: 設計仕様、実行計画、Agent規則、初期task queueを作成。
- [x] (2026-08-11) `DESIGN-002`: Bootstrap前の実装契約、output境界、bundle自己完結性、決定性、runtime、承認、安全上限を確定。
- [x] (2026-08-11) `BOOTSTRAP-001`: repo骨格、設定、共通schema、CI、基礎validator。
- [~] `CONTRACT-001`: handoff受理・provenance・receipt契約。冪等再受理とschema snapshotは実装済み、READY export gate待ち。
- [~] Cross-repo result contract prework: research consumerが待つProduction-owned `production-result` v1 schemaを追加し、registry hashとconsumer-compatible fixtureを固定。clean commitからのsnapshot取得、result builder/exporter、相互E2Eは未完了。
- [ ] `PLANNING-SCHEMA-001`: scope、deliverable、spec、WBS、resource、budget、schedule、risk schema。
- [ ] `PLANNING-BUILD-001`: 制作計画生成、依存graph、human/agent bundle。
- [ ] `PROTOTYPE-001`: prototype、test、review gate、iteration、change request。
- [ ] `RUNTIME-001`: 状態機械、event log、resume。
- [ ] `RUNTIME-002`: task DAG、lease、retry、effect、approval。
- [ ] `EXECUTION-001`: output version、quality、asset reference、installation。
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

## Outcomes & Retrospective

設計段階では、researchの芸術判断を保ったまま実制作に必要な運用情報を独立管理する構造に加え、実装者へ残っていたwire format、hash、共通型、runtime、承認、安全上限を確定した。`BOOTSTRAP-001`では、設定・schema・安全検査・canonical serializer・bundle loader・project generator・CI・正常/失敗/再実行テストを追加した。minimal fixtureによる機構確認は完了したが、handoff互換性の最終確定はresearch側clean commitのschemaとexpected bundleを外部entry gateとして残している。

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
Status: IN_PROGRESS (final acceptance blocked by external READY export gate)
Changed canonical files: tools/new_production.py, tools/validate.py, schemas/external/production-handoff.v1.schema.json, config/schema-registry.yaml, schemas/external/README.md, tests/test_bootstrap.py, execution/task-queue.yaml, execution plan, README.md
Generated files: none; no temporary or dirty-provenance bundle was promoted
Commands executed: research clean-state check; research HEAD inspection; research handoff_release_check.py --root . --require-schema-snapshot; external output harmony-study inspection; local validator, unittest, py_compile, git diff --check
Results: research HEAD aba5f1738cc0066c994433d91c333b3cfe5210da is clean; release gate base/fixture/fail-closed/backward-compatibility checks pass; schema raw snapshot registered; idempotent same-handoff retry succeeds without overwrite; mismatched existing project is rejected; 11 local tests and validator pass; actual harmony-study output lacks 05_production/production-handoff.yaml and no manifest/provenance export bundle exists
New validation rules: registry-local schema snapshots are Draft 2020-12 checked; same handoff ID/revision/hash reaccepts an intact project; mismatched or invalid existing projects fail with PROJECT_IDEMPOTENCY_MISMATCH
Approvals simulated: none; no upstream working-tree modification, commit, publication, or external effect was performed
Surprises and decisions: research release gate intentionally keeps bundle bytes test-only and does not provide a production handoff artifact; the clean schema snapshot can be registered independently, but a test-time dirty fixture must not be converted into a clean accepted bundle
Remaining risks: the canonical output project still needs research-side READY handoff generation and clean export; the external compatibility gate remains pending
Next READY task: none; CONTRACT-001 remains IN_PROGRESS with final acceptance blocked by the missing READY export bundle
Exact restart command: git status --short
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
- `tools/run_project.py`
- `tools/build_plan.py`
- `tools/build_graph.py`
- `tools/impact.py`
- `tools/bundle.py`
- `tools/build_result.py`
- `tools/export_result.py`
- `tools/audit.py`
- `tools/release_check.py`

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
