# Agentic Art Production 実装契約仕様書

- 作成日: 2026-08-11
- 版: 1.0.0
- 状態: Bootstrap前確定仕様
- 対象リポジトリ: `masa-san-jp/agentic-art-production`
- 上位仕様: `docs/20260811-agentic-art-production-system-design-specification.md`
- 実行計画: `docs/20260811-agentic-art-production-repository-execution-plan.md`

## 0. 目的と優先順位

本書は、システム設計仕様の概念を、schema、validator、CLI、fixtureへ一意に落とすための規範的な実装契約である。Bootstrap以降の実装者は、本書に定義された表現、順序、上限、失敗規則を推測で変更しない。

文書の優先順位は次とする。

1. `AGENTS.md`の安全・承認境界
2. システム設計仕様の責任分界とdomain semantics
3. 本書のwire format、正規化、状態遷移、runtime規則
4. リポジトリ実行計画の実装順序
5. schema、config、test

上位二文書と本書が矛盾する場合は安全側で停止し、設計変更taskを作る。本書とschema、config、testが矛盾する場合はvalidatorを成功させるために文書を無視せず、すべてを同じ変更で修正する。

## 1. v1不変条件

1. Productionはcommit固定されたhandoff bundleだけを入力とし、隣接research working treeを直接読まない。
2. bundle受理は契約上の受領であり、制作仮説の最終採択ではない。
3. research由来の必須要件、禁止事項、受入試験の意味をproductionが直接変更しない。
4. 正本recordは追記または明示revisionで変更し、過去の意味を上書きしない。
5. 生成物、state projection、index、briefを手編集しない。
6. external/physical effectをエージェントが実施済みと記録するには、承認recordと外部evidenceの両方を要求する。
7. 実project、asset本体、PRIVATE_RAW、RESTRICTED、credentialをprotocol repositoryへ保存しない。
8. 同じ入力、明示timestamp、config versionから同じcanonical bytesを生成する。
9. 失敗を補正せず、named ruleとremediationを返す。
10. network、DB、Web UIなしで全主要経路をfixture実行できる。

## 2. Repositoryとproject outputの境界

本リポジトリはprotocol、config、schema、template、validator、test、合成fixtureの正本である。実projectの正本は、Git外の明示的に設定されたoutput rootへ保存する。

```text
<output-root>/
└── production/
    └── <project-slug>/
        ├── manifest.yaml
        ├── 00_handoff/
        ├── 01_scope/
        ├── 02_specification/
        ├── 03_plan/
        ├── 04_prototype/
        ├── 05_execution/
        ├── 06_installation/
        ├── 07_governance/
        └── 08_runtime/
```

- output rootはCLI引数またはGit管理外のlocal configで指定する。
- tracked configにmachine固有absolute pathを保存しない。
- testは一時directoryだけをoutput rootに使う。
- repository内の`templates/`と`tests/fixtures/`を実projectとして更新しない。
- `data/`は再生成可能な評価・schema index・release evidenceだけに使い、実projectを置かない。

## 3. ID、revision、参照

### 3.1 Project ID

Production project IDは`production/<slug>`とする。`slug`は小文字英数字で始まり、小文字英数字と単一hyphenだけを使い、3文字以上64文字以下とする。

```regex
^production/[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,62}[a-z0-9]$
```

### 3.2 Record ID

- domain record IDはschemaが定める大文字prefixと3桁以上の10進連番を使う。
- canonical record keyは`{origin, namespace, id}`とする。production生成IDはproduction project namespace全体で一意とし、異なるrecord kindで再利用しない。
- upstream IDは改名せず、`origin: research`、handoff key、source IDを持つstructured referenceとして保存する。local IDと同じ文字列でも同一recordとして扱わない。
- 自動生成IDはsource IDのbytewise昇順で初回割当し、その後はprefixごとの永続counterから採番する。
- 削除、棄却、supersedeされたIDを再利用しない。

### 3.3 Revision

- 意味を固定するrecordは`revision`を1から始める。
- 同じ`id`と`revision`の内容はimmutableとする。
- 変更版はrevisionを増やし、`supersedes`に直前の`{id, revision, content_sha256}`を記録する。
- baseline、approval target、handoff、resultへの参照はrevisionとhashを固定する。
- handoffの一意keyは`(handoff_id, revision)`である。同じkeyと同じhashの再受理は成功し、異なるhashは`HANDOFF_IDENTITY_CONFLICT`で拒否する。

## 4. Handoff bundle wire contract

### 4.1 受理形式

v1はdirectoryまたはZIP archiveを受理する。tar、7z、disk image、暗号化archiveは受理しない。archiveは一時rootへ安全に展開し、検証完了前にproject rootへ書き込まない。

bundleは次の構造を持つ。

```text
handoff-bundle/
├── manifest.yaml
├── production-handoff.yaml
├── provenance.yaml
├── schemas/
│   └── production-handoff.schema.json
└── artifacts/
    ├── production-hypotheses.yaml
    ├── hypothesis-comparison.yaml
    ├── production-requirements.yaml
    ├── acceptance-tests.yaml
    ├── prototype-plans.yaml
    ├── source-ref-index.yaml
    ├── production-brief.yaml
    └── creative-direction.md
```

`prototype-plans.yaml`は`prototype_plan_ids`が空でも空配列を持つfileとして必要である。`creative-direction.md`はhandoffの`creative_direction_ref`が指すsnapshotであり、参照がある限り必須とする。

### 4.2 Manifest

`manifest.yaml`は最低限次を持つ。

```yaml
bundle_schema_version: 1.0.0
bundle_id: HB-HO001-R1
entrypoint: production-handoff.yaml
handoff_key:
  handoff_id: HO001
  revision: 1
files:
  - path: production-handoff.yaml
    role: HANDOFF
    media_type: application/yaml
    size_bytes: 4096
    sha256: "sha256:<64-lowercase-hex>"
integrity:
  file_set_sha256: "sha256:<64-lowercase-hex>"
```

- `files`はmanifest自身を除く全fileをpath bytewise昇順で一度ずつ列挙する。
- 宣言外file、欠落file、duplicate path、case-fold衝突を拒否する。
- file hashは変換前のraw bytesに対するSHA-256である。
- `file_set_sha256`は`files`配列から`path`、`size_bytes`、`sha256`だけを取り出し、§5のcanonical JSONでhashする。
- pathはPOSIX相対pathとし、`.`、`..`、空segment、backslash、NUL、leading slashを禁止する。

### 4.3 Provenance

`provenance.yaml`は次を必須とする。

```yaml
source_repository: masa-san-jp/agentic-art-research
source_commit: "<40-lowercase-hex>"
source_tree_clean: true
source_schema:
  path: schemas/production-handoff.schema.json
  version: 1.0.0
  sha256: "sha256:<64-lowercase-hex>"
source_project:
  id: project/harmony-study
  version: 1.1.0
generator:
  name: agentic-art-research/export_handoff
  version: 1.0.0
canonicalization: json-sort-keys-compact-utf8-v1
generated_at: "2026-08-11T21:00:00+09:00"
```

`source_commit`はgeneratorとschemaを含むresearch code commitを意味する。Git外projectの内容同一性は、handoff hash、manifest file hash、`source-ref-index.yaml`のsource reference hashで保証する。`source_tree_clean: false`のbundleは開発fixture以外で受理しない。source-ref indexのwire contractは、top-level `references`、Researchがcanonical recordから計算した`record_hash`、および任意の`reference_categories`/恒久HTTPS `access_url`である。原record本文はbundleに含まれないためProductionはhashを再計算せず、形式・非ゼロ値・manifest境界を検証する。

### 4.4 Self-contained reference resolution

- selected/alternative hypothesis IDは`production-hypotheses.yaml`で解決する。
- requirement snapshotはhandoffと`production-requirements.yaml`でstatement、priority、test接続が一致する。
- acceptance test IDは`acceptance-tests.yaml`でmethod、precondition、pass condition、evidence requirementまで解決する。
- prototype plan IDは`prototype-plans.yaml`で解決し、task DAGが閉じる。
- decision、insight、evidence IDは`source-ref-index.yaml`の`references`でkind、source project-relative path、Research計算済み`record_hash`、安全な短いsummaryへ解決する。制作担当者が参照するreferenceは`reference_categories`と、query・credential・fragmentを含まない恒久HTTPS `access_url`を持てる。原証拠本文は含めない。欠落またはゼロhashは補正せず、受理・計画生成をfail closedにする。
- productionはbundle外のresearch fileを暗黙参照して不足を補わない。

## 5. Canonicalizationとintegrity

### 5.1 Canonical JSON v1

handoffとproduction resultのsemantic hashは、次の手順で計算する。

1. UTF-8 YAMLまたはJSONをduplicate key、alias、merge key、custom tagを拒否してparseする。
2. 値をJSON互換のobject、array、string、integer、boolean、nullに限定する。NaN、Infinity、binary、timestamp object、非整数floatを拒否する。
3. top-levelの`integrity` field全体をpayloadから除外する。
4. object keyをUnicode code point順にsortする。array順序は変更しない。
5. JSONをUTF-8、`ensure_ascii=false`、空白なし、末尾newlineなしでserializeする。
6. Unicode code pointは入力どおり保持し、暗黙のUnicode normalizationを行わない。
7. SHA-256を計算し、`sha256:<64-lowercase-hex>`で表す。

Python referenceは次と同値でなければならない。

```python
json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

algorithm IDは`json-sort-keys-compact-utf8-v1`とする。algorithm変更は新IDと互換性testを要求する。

### 5.2 Serializationとgeneration

- YAMLの見た目、comment、key順序はsemantic hashへ影響しない。
- file hashはraw bytesへ作用するため、export後の改行・整形変更を検知する。
- generatorはrecord配列をID bytewise昇順にする。意味を持つ順序は明示的な`order` fieldを使う。
- 現在時刻を内部で取得して決定的成果物へ混ぜない。timestampはCLI引数、既存event、またはfixture clockから注入する。
- 同じcanonical source、schema/config version、注入timestampでbyte-identicalな出力を要求する。

## 6. 共通scalar contract

### 6.1 Money

```yaml
money:
  amount: "120000"
  currency: JPY
  basis: supplier-quote
  confidence: MEDIUM
  status: ESTIMATED
  tax:
    mode: UNKNOWN
    amount: null
  source_ref: quote/QT001
```

- `amount`とtax amountは10進文字列とし、binary floatを使用しない。
- 書式は`0`または非zero開始整数部と任意の小数部とし、指数表記、桁区切り、leading plusを禁止する。
- 計算はPython `Decimal`相当で行う。
- 通貨はconfigで許可したISO 4217 codeを使う。
- taxは`INCLUDED`、`EXCLUDED`、`NOT_APPLICABLE`、`UNKNOWN`を区別する。
- taxや通貨換算を自動推定しない。計算する場合はrate、rounding mode、scale、source、calculated_atを記録する。
- v1既定の丸めは存在しない。policy未指定で丸めが必要ならblocking errorにする。

### 6.2 Quantityとunit

```yaml
quantity:
  value: "300"
  unit: mm
```

- `value`はMoneyと同じ10進文字列とする。
- unitは`config/units.yaml`の閉じた語彙を使い、dimensionを持つ。
- v1の最低語彙は`mm`、`cm`、`m`、`g`、`kg`、`mL`、`L`、`s`、`min`、`h`、`item`、`set`、`sheet`とする。
- 異dimension間を変換しない。換算はconfigにexact factorがある組合せだけに限定する。
- duration estimateもquantityを使い、deadlineのtimestampと混同しない。

### 6.3 Time

- timestampはRFC 3339文字列、timezone必須、leap second禁止とする。
- date-only、duration、timestampは別schemaにする。
- 比較時はUTC instantへ変換するが、recordには入力offsetを保持してよい。
- expiry判定のclockはruntimeへ注入し、testで固定する。

### 6.4 URI

- asset URIの既定許可schemeは`urn`と`https`とする。
- scheme追加は`config/asset-policy.yaml`の明示変更とsecurity testを要求する。
- `file`、userinfo、credential、signed query、fragment、local absolute pathを禁止する。
- `https` URIはstable identifierだけに使い、queryを既定で禁止する。
- asset refはURIだけでなくversion、content SHA-256、media type、rights statusを必須とする。

## 7. Receipt、selection、project lifecycle

### 7.1 Receiptとproject materialization

- bundleは一時rootで検証し、receiptを生成する。
- `REJECTED` receiptではcanonical project rootを作らない。診断とreceiptは明示output先へだけ保存できる。
- `ACCEPTED` receiptはschema、integrity、compatibility、安全検査の合格を意味し、芸術的採択や外部行為の承認を意味しない。
- project rootはACCEPTED後にtemp directoryからatomic renameして初めて可視化する。
- 最初に永続化されるproject stateは`HANDOFF_VALIDATED`とする。`DRAFT`はtemplate生成中の一時状態であり、受理済みprojectの通常状態に使わない。

### 7.2 Selection

- `AGENT_RECOMMENDED` handoffはplanningと非破壊simulationの入力にできる。
- `READY_FOR_PRODUCTION`へ進むには、既定policyではHUMAN authorityのselection approvalを要求する。
- research handoffが既にHUMAN selectionと対象hash付きapproval refを持つ場合、productionはauthority、scope、hash、expiryを検証して再利用できる。
- productionが作者の最終芸術判断をagent recommendationだけで確定しない。
- selection未確定でも、明示的に`PROVISIONAL`なscopeでprototype計画を作成できる。

### 7.3 State transition

標準遷移は次とする。

```text
HANDOFF_VALIDATED
  → PLANNING
  → READY_FOR_PROTOTYPE → PROTOTYPING → REVIEWING ─┐
          │                                        ├→ PLANNING
          └─ prototype not applicable ─────────────┘
PLANNING / REVIEWING
  → READY_FOR_PRODUCTION → PRODUCING
  → READY_FOR_INSTALLATION → INSTALLING → VALIDATING
  → COMPLETE | COMPLETE_WITH_GAPS

各active state → BLOCKED | CANCELLED
BLOCKED → recorded resume_state
COMPLETE* → PLANNING（明示reopenのみ）
```

遷移guardを次に固定する。

| From | To | 必須guard |
|---|---|---|
| `HANDOFF_VALIDATED` | `PLANNING` | ACCEPTED receipt、handoff key/hash、schema snapshotが固定済み |
| `PLANNING` | `READY_FOR_PROTOTYPE` | provisional scope、prototype task/test/resource/riskが解決済み |
| `READY_FOR_PROTOTYPE` | `PROTOTYPING` | 実行可能task、必要approval、停止上限が固定済み |
| `PROTOTYPING` | `REVIEWING` | 全prototype taskがterminalで、外部実施はevidence付き |
| `REVIEWING` | `PLANNING` | FAIL/deviation/change requestと影響が記録済み |
| `PLANNING`/`REVIEWING` | `READY_FOR_PRODUCTION` | HUMAN selection、scope/spec/WBS/budget/schedule/risk baseline、必須prototype判定済み |
| `READY_FOR_PRODUCTION` | `PRODUCING` | eligible production taskと必要approvalがある |
| `PRODUCING` | `READY_FOR_INSTALLATION` | output検査合格、installation対象、会場再確認が有効 |
| `PRODUCING` | `VALIDATING` | installation非対象の理由付きskip decisionがある |
| `READY_FOR_INSTALLATION` | `INSTALLING` | 搬入・設営・安全approvalと外部実施計画がある |
| `INSTALLING` | `VALIDATING` | installation resultとevidenceがある |
| `VALIDATING` | `COMPLETE` | システム設計仕様§17をすべて満たす |
| `VALIDATING` | `COMPLETE_WITH_GAPS` | non-blocking gapだけが残り、owner、期限、resume conditionがある |
| active | `BLOCKED` | blocker、impact、owner、resume_state、resolution conditionがある |
| `BLOCKED` | `resume_state` | 全blocking conditionがevidence付きで解消済み |
| active | `CANCELLED` | HUMAN authority、reason、retention decisionがある |
| `COMPLETE*` | `PLANNING` | reopen reason、change request、authority、対象hashがある |

prototypeまたはinstallationをskipする場合、`NOT_APPLICABLE`という文字列だけで済ませず、対象、理由、根拠、承認要否を持つskip decisionを保存する。

## 8. Runtime、event、task selection

### 8.1 Canonical event logとstate projection

- `08_runtime/run-log.jsonl`を状態遷移とtask/effectのappend-only正本とする。
- `08_runtime/production-state.json`はevent logから生成するmaterialized projectionであり、手編集しない。
- runtime開始時に必ず全eventをreplayし、projectionの`revision`、`last_event_id`、`last_event_hash`、`state_sha256`と一致することを確認する。
- `state_sha256`はprojection top-levelの`state_sha256`自身を除外し、§5のcanonical JSONで計算する。
- 不一致を黙ってrepairせず、`RUNTIME_STATE_DIVERGENCE`で停止する。

eventは最低限次を持つ。

```yaml
event_id: EVT000001
sequence: 1
occurred_at: "2026-08-11T21:00:00+09:00"
type: PROJECT_STATE_TRANSITIONED
actor:
  kind: AGENT
  id: runtime/local
idempotency_key: transition/PLANNING/1
previous_event_sha256: null
payload: {}
event_sha256: "sha256:<canonical-event-without-event_sha256>"
```

- sequenceは1から連続し、gap、duplicate、並べ替えを拒否する。
- event hash chainを検証する。
- single-writer lockの下でeventを完全な1行としてappendし、flush/fsync後にstateをtemp fileからatomic replaceする。
- crashでeventだけが先行した場合はreplayでprojectionを更新する。
- JSONL末尾のpartial line、hash不一致、stateがlogより先行する状態は自動切捨てせずBLOCKEDにする。

### 8.2 Task eligibilityと決定順

taskは次をすべて満たす場合だけeligibleである。

- statusが`READY`
- 全dependencyが`DONE`または許可された`SKIPPED`
- blockerがない
- 必須resource/materialがavailableまたは予約済み
- 必須approvalが有効
- retry、budget、iteration、deadlineの停止上限内
- active leaseがない、または期限切れrecovery済み

eligible taskは次のtupleの昇順で一意に選ぶ。

```text
(priority, earliest_start_or_max, task_id)
```

- `priority`は0から999の整数で、小さい値を先にする。既定値は100。
- `earliest_start`未指定は最大値として扱う。
- `task_id`はUTF-8 bytewise昇順とする。
- resource conflictのあるtaskはeligibleではないため、次候補を評価する。
- v1 canonical writerは一processとする。workerはlease経由で作業し、canonical fileを直接更新しない。

### 8.3 Lease、retry、effect

- leaseはtask ID、owner、acquired_at、expires_at、attempt、lease tokenを持つ。
- heartbeatは同じlease tokenだけが更新できる。
- expiry後はeffect recordを照合してから再claimする。外部成功不明なら自動retryしない。
- retryは`TRANSIENT`分類だけに許可し、config上限を超えない。
- effect typeは`READ_ONLY`、`REPOSITORY_WRITE`、`EXTERNAL_WRITE`、`PHYSICAL_EXTERNAL`、`PURCHASE`、`CONTRACT`、`PUBLICATION`、`DELETION`を最低語彙とする。
- effect keyとtarget hashが同じ成功済みeffectは再適用せず冪等成功を返す。同じkeyで異なるtarget hashは拒否する。

## 9. Approval contract

approval recordは最低限次を持つ。

```yaml
approval_id: AP001
revision: 1
decision: APPROVED
scope:
  action: PURCHASE
  target_ref: BI003@1
  target_sha256: "sha256:<64-lowercase-hex>"
  max_amount:
    amount: "10000"
    currency: JPY
approver:
  id: human/authorized-producer
  authority: HUMAN
issued_at: "2026-08-11T21:00:00+09:00"
expires_at: "2026-08-18T21:00:00+09:00"
constraints: []
supersedes: null
```

- decisionは`APPROVED`、`DENIED`、`REVOKED`を使う。
- wildcard target、対象hashなし、actionなしの包括承認を禁止する。
- target content、amount、supplier、venue、methodの承認対象fieldが変われば旧approvalを無効とする。
- expiry、revocation、authority、action、amount、currency、constraintsをeffect直前に再検証する。
- approvalは外部行為の実施証明ではない。完了には外部system receiptまたは人間が記録したevidenceを別途要求する。
- test fixtureではfake approverとfake adapterを使い、実際の購入、契約、公開、物理作業を行わない。

## 10. DiagnosticsとCLI

全blocking errorは次のmachine-readable fieldsを持つ。

```json
{
  "severity": "ERROR",
  "rule": "HANDOFF_HASH_MISMATCH",
  "file": "production-handoff.yaml",
  "location": "/integrity/content_sha256",
  "line": null,
  "reason": "canonical payload hash does not match",
  "remediation": "re-export the handoff from its fixed research source",
  "context": {"handoff_id": "HO001", "revision": 1}
}
```

- YAML/JSON syntax errorは可能ならlineとcolumn、schema/cross-reference errorはJSON Pointerを返す。
- 一回の検査で安全に収集できる独立findingは安定順でまとめて返す。
- finding順は`(file, location, rule)`のbytewise昇順とする。
- CLIは人間向けtextを既定にしてよいが、`--format json`で上記形式を返す。
- exit codeは`0=success`、`1=validation/acceptance failure`、`2=usage/local config error`、`3=external dependency blocked`、`4=human approval required`とする。

## 11. Input limitsとarchive safety

v1既定値を次に固定し、configで狭めることはできるが広げる変更はsecurity reviewを要求する。

| 対象 | 上限 |
|---|---:|
| handoff canonical payload | 262,144 bytes |
| compressed ZIP | 4 MiB |
| total uncompressed bundle | 8 MiB |
| individual file | 1 MiB |
| file count | 64 |
| path UTF-8 bytes | 240 |
| path depth | 8 segments |
| compression ratio | 20:1 |

- UTF-8 textだけを許可し、NUL、BOM、invalid UTF-8を拒否する。
- 許可extensionは`.yaml`、`.json`、`.md`、`.jsonl`に限定する。
- symlink、hardlink、device、FIFO、socket、encrypted entry、duplicate entry、case-fold collisionを拒否する。
- ZIP metadataの展開先pathを信用せず、正規化後に一時root内へ閉じることを確認する。
- limit超過を部分受理せず、rule、actual、limit、remediationを返す。

## 12. Schema compatibilityとmigration

- 外部schema snapshotは`schemas/external/`へimmutable fileとして保存する。
- `config/schema-registry.yaml`にschema version、source repository、source commit、source path、acquired_at、raw file SHA-256、compatibility statusを記録する。
- 通常実行時にnetworkからschemaを取得しない。
- 同じversionのsnapshot contentを置換しない。差分があれば新versionまたは供給元修正の明示decisionを要求する。
- 未登録version、source hash不一致、供給元commit不明はfail closedとする。
- migrationは元fileを上書きせず、入力version、出力version、変換rule、loss reportを持つ新成果物を作る。
- production result schemaにも同じsnapshot規則を適用し、research側expected fixtureと相互検証する。

## 13. Determinism、dirty source、release

- release可能なhandoff/result exportはgenerator codeのclean source treeと40桁commit SHAを要求する。
- dirty sourceでの生成は明示的development modeだけ許可し、statusを`DRAFT`、`source_tree_clean: false`とし、外部受理を禁止する。
- project canonical sourceはGit外でもよいが、全入力record hashとproject revisionをprovenanceへ持つ。
- locale、filesystem iteration order、process ID、temporary pathを成果物へ混ぜない。
- timezone、clock、randomnessは依存として注入する。random IDをcanonical IDに使わない。
- release gateは同一commitでCI相当commandを3回連続実行し、各runのcommit、開始時刻、command、結果hashをevidenceへ記録する。

## 14. Bootstrap前に解決済みの設計判断

| 論点 | v1決定 |
|---|---|
| canonical hash | `json-sort-keys-compact-utf8-v1`、top-level integrity除外 |
| bundle自己完結性 | hypothesis、comparison、requirement、test、prototype、source indexをmanifestで同梱 |
| project保存先 | protocol repo外の明示output root |
| money | ISO currency＋10進文字列、暗黙tax/roundingなし |
| unit | config管理の閉じた語彙＋10進文字列 |
| task順序 | `(priority, earliest_start_or_max, task_id)` |
| concurrency | v1はsingle canonical writer、workerはlease経由 |
| runtime正本 | hash chain付きappend-only event log、stateはprojection |
| approval | action、revision、target hash、expiryを固定しwildcard禁止 |
| asset URI | 既定`urn`とqueryなし`https` |
| archive | directoryまたは安全検査済みZIP |
| input limit | §11の保守的固定値 |

## 15. 外部依存と開始gate

2026-08-11時点で、research側のhandoff schemaとvalidatorは隣接repositoryの未commit作業ツリーで実装中である。この状態をproductionの互換snapshot正本として取り込まない。

- `BOOTSTRAP-001`は本書だけで開始できる。
- `CONTRACT-001`は、research側がhandoff schema、export bundle manifest、expected fixtureをclean commitで固定するまで完了できない。
- upstream待ちの間もfake draft fixtureでreader interfaceをtestしてよいが、`COMPATIBLE`や`ACCEPTED`のrelease evidenceに使わない。
- 上流bundleが§4を満たさない場合、production側で黙って補完せず、cross-repository contract changeとして記録する。

## 16. 改訂規則

- wire format、canonicalization、ID意味、approval semantics、state transitionを変える場合は本書、system design、schema、migration、fixture、versionを同じ変更で更新する。
- 安全上限を緩和する変更はsecurity testとDecision Logを要求する。
- 仕様未記載の値を実装者が新しい既定値として埋めない。保守的に拒否し、設計taskへ戻す。

## 17. 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0.0 | 2026-08-11 | Bootstrap前のwire format、正規化、共通型、lifecycle、runtime、approval、安全上限を確定 |
