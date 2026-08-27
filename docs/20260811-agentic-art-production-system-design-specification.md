# Agentic Art Production システム設計仕様書

- 作成日: 2026-08-11
- 版: 1.1.0
- 状態: Bootstrap前確定仕様
- 対象リポジトリ: `masa-san-jp/agentic-art-production`
- 上流: `masa-san-jp/agentic-art-research`
- 実装契約: `docs/20260811-agentic-art-production-implementation-contract-specification.md`
- 実行計画: `docs/20260811-agentic-art-production-repository-execution-plan.md`

## 0. 結論

本システムは、`agentic-art-research`から版固定された制作引き渡しを受け取り、採択された制作仮説を、実行可能な制作計画、試作、本制作、展示・設営、受入結果へ変換する制作基盤である。

単なるTODOリストや作品ファイル保管庫ではない。次の連鎖を、相互参照可能な正本として管理する。

```text
Production Handoff
  → Handoff Validation
  → Selection / Scope Baseline
  → Deliverables / Technical Specification
  → Work Breakdown / Dependencies
  → Resources / Materials / Budget / Schedule
  → Prototype / Review / Change Control
  → Production / Installation / Acceptance
  → Production Result
  → agentic-art-researchへの学習還流
```

完了時には、次が成立する。

- 人間は、何を、なぜ、誰が、いつ、何を使い、どの予算と制約で作るか確認できる。
- 実行エージェントは、依存完了済みの次タスクと必要最小コンテキストだけを取得できる。
- 全成果物、作業、変更、試験結果を、受理したhandoffの制作仮説・要件まで遡れる。
- 要件逸脱、失敗、代替案、未解決事項を消さずに保持する。
- 購入、契約、公開、外部送信、危険作業は人間承認なしに実行されない。
- 完成作品や大容量assetはGitへ保存せず、URI、版、hash、権利区分で参照する。

本書は責任分界、domain semantics、安全・承認境界の正本である。wire format、canonical hash、共通scalar、状態遷移guard、runtime transaction、入力上限は実装契約仕様を正本とし、実装時に再解釈しない。

## 1. 目的

### 1.1 達成すること

1. research handoffのschema、hash、source commit、権利・安全境界を検証する。
2. 採択権限を確認し、選択された制作仮説と必須要件をscope baselineとして凍結する。
3. 制作成果物、技術仕様、素材、資源、工程、依存、予算、日程、会場条件を構造化する。
4. 試作とreview gateによって重大な不確実性を本制作前に減らす。
5. 中断、再試行、計画変更、外部依存、部分失敗から再開できる。
6. 制作結果と要件適合性をproduction resultとして上流へ返す。
7. 物理作業を行えないエージェントが、実施したと偽らず外部検証待ちで停止できる。

### 1.2 成功指標

| 指標 | 合格条件 |
|---|---|
| Handoff整合性 | 受理済みhandoffのschema、hash、source commit、必須参照が解決する |
| 計画完全性 | 全必須deliverableに仕様、owner capability、工程、試験、期限またはgapがある |
| 依存整合性 | Work PackageとTask DAGに欠落・循環がない |
| 費用統制 | 全費用が見積・予約・確定・実績のいずれかで、通貨と根拠を持つ |
| 資源統制 | 必須素材・設備・技能・会場の可用性が確定またはblocker化される |
| 変更統制 | baseline変更がchange request、影響、権限、decisionを持つ |
| 検証可能性 | 全必須要件にproduction test resultまたは外部検証理由がある |
| 再開性 | kill-and-resumeでtask effectが重複しない |
| 追跡性 | deliverableからhandoff requirementとresearch decisionまで遡れる |
| 安全性 | 禁止データと秘密がGitに入らず、外部行為が承認境界を越えない |
| 還流性 | 完了・失敗・逸脱をresearchが検証可能なresult bundleで返す |

## 2. スコープ

### 2.1 対象

- research handoffの受領、互換性検査、受理・拒否
- 制作仮説の人間採択記録
- 制作scope baselineと変更管理
- deliverable、technical specification、quality target
- Work Breakdown Structure、Task DAG、milestone、critical path
- owner capability、担当、設備、場所、素材、部材表
- 見積、予算基準線、予備費、実績、差異
- 調達候補、発注前承認、受領検査
- Prototype、technical test、expression review、iteration
- 本制作、版管理、asset参照、品質検査
- 搬入、設営、展示条件、撤去計画
- safety、rights、privacy、venue risk
- completion report、production result、research feedback

### 2.2 スコープ外

- 原証拠、歴史的主張、作者の美意識仮説の正本化
- 制作仮説を根拠なく新しい作品へ変更すること
- 人間承認なしの購入、契約、応募、公開、外部送信、削除
- エージェントが物理作業、現地確認、観客反応を実施したと偽ること
- クレジットカード、銀行情報、秘密鍵、署名付きURLのGit保存
- 完成映像、音声、3D、RAW、設計バイナリ、大容量asset本体のGit保存
- 権利不明素材の採用または再配布
- 初期実装における常時稼働DB、Web UI、独自ベクトルDBの必須化

## 3. Researchとの責任分界

| 領域 | 正本 | Productionでの扱い |
|---|---|---|
| 証拠、主張、インサイト | research | handoffのIDと短い要約だけを参照する |
| 制作判断、仮説、棄却案 | research | 採択対象として読み取り専用で扱う |
| 制作要件、禁止事項、受入試験 | research | scope baselineへ固定する |
| 採択結果 | production | authorityとtimestamp付きで記録しresearchへ返す |
| 技術仕様、工程、日程、予算、資源 | production | 本repoの正本とする |
| 試作・制作・設営結果 | production | 条件、結果、限界を正本化する |
| 制作結果から生じる新しい解釈 | research | productionは観察と変更要求として返す |
| 作品asset本体 | asset store | URI、版、hash、権利だけを記録する |

Productionは必須要件を黙って削除、弱体化、別解釈へ変更しない。実現不能な場合は`change-request.yaml`を作り、影響、代替案、費用・日程差、必要権限を記録する。

## 4. 入力契約

### 4.1 Handoff bundle

受理可能なbundleは、handoff本体だけでなく、選択仮説、要件、受入試験、Prototype Plan、source参照をオフラインで解決できるsnapshotを含む。正確なmanifest、provenance、file hash、上限は実装契約仕様§4、§5、§11を正本とする。

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
    └── creative-direction.md
```

manifestに列挙されないファイルは読み込まない。原証拠本文、権利不明素材、asset本体をsnapshotへ含めず、source-ref indexのID、record hash、安全な短いsummaryで追跡する。

### 4.2 受理検査

1. manifestの全pathがbundle内に閉じている。
2. symlink、path traversal、special file、過大archiveがない。
3. handoff schema versionが互換範囲内である。
4. research commitが完全SHAである。
5. canonical payloadのSHA-256が一致する。
6. 必須要件と受入試験が対応する。
7. 選択状態と採択権限が矛盾しない。
8. blocking gapが明記される。
9. `PRIVATE_RAW`、`RESTRICTED`、秘密、絶対path、署名付きURLがない。
10. 同じhandoff ID/revisionを異なる内容で再受理していない。
11. manifest宣言fileが過不足なくraw-byte hashと一致する。
12. hypothesis、requirement、acceptance test、prototype、source refがbundle内で解決する。
13. provenanceのschema hash、source commit、clean source表明が互換性registryと一致する。

受理できない場合、黙って補正せず次を返す。

```yaml
receipt_status: REJECTED
rules:
  - rule: HANDOFF_HASH_MISMATCH
    location: production-handoff.yaml#/integrity/content_sha256
    reason: canonical payload hash does not match
    remediation: rebuild and re-export the handoff from its research source commit
```

## 5. リポジトリ構造

```text
agentic-art-production/
├── README.md
├── AGENTS.md
├── PLANS.md
├── config/
│   ├── vocabularies.yaml
│   ├── approval-policy.yaml
│   ├── budget-policy.yaml
│   ├── safety-policy.yaml
│   ├── retention-policy.yaml
│   └── stopping-policy.yaml
├── schemas/
│   ├── production-project.schema.json
│   ├── handoff-receipt.schema.json
│   ├── scope-baseline.schema.json
│   ├── deliverable.schema.json
│   ├── technical-spec.schema.json
│   ├── work-package.schema.json
│   ├── resource.schema.json
│   ├── material.schema.json
│   ├── budget.schema.json
│   ├── schedule.schema.json
│   ├── risk.schema.json
│   ├── review.schema.json
│   ├── change-request.schema.json
│   ├── production-result.schema.json
│   └── external/
│       └── production-handoff.v1.schema.json
├── docs/
├── templates/project/
├── tools/
├── tests/
├── execution/
└── data/                         # protocol由来生成物。手編集禁止
```

本repositoryはprotocol、config、schema、template、validator、test、合成fixtureだけを正本とする。実運用projectは、実装契約仕様§2に従ってGit外の明示output rootへ生成し、本repositoryの`projects/`または`data/`へ常設しない。

## 6. プロジェクト成果物

```text
<output-root>/production/<project-slug>/
├── manifest.yaml
├── 00_handoff/
│   ├── source-bundle-manifest.yaml
│   ├── production-handoff.yaml
│   └── handoff-receipt.yaml
├── 01_scope/
│   ├── selection-record.yaml
│   ├── scope-baseline.yaml
│   └── assumptions-register.yaml
├── 02_specification/
│   ├── deliverables.yaml
│   ├── technical-specifications.yaml
│   ├── material-register.yaml
│   └── asset-register.yaml
├── 03_plan/
│   ├── work-packages.yaml
│   ├── task-plan.yaml
│   ├── schedule.yaml
│   ├── budget.yaml
│   ├── resource-plan.yaml
│   └── procurement-plan.yaml
├── 04_prototype/
│   ├── prototype-runs.yaml
│   ├── test-results.yaml
│   └── iteration-decisions.yaml
├── 05_execution/
│   ├── production-log.jsonl
│   ├── output-versions.yaml
│   └── quality-results.yaml
├── 06_installation/
│   ├── venue-constraints.yaml
│   ├── installation-plan.yaml
│   └── installation-results.yaml
├── 07_governance/
│   ├── approval-register.yaml
│   ├── change-requests.yaml
│   ├── risk-register.yaml
│   ├── rights-register.yaml
│   └── safety-register.yaml
└── 08_runtime/
    ├── production-state.json
    ├── run-log.jsonl
    ├── dependency-index.json
    ├── completion-report.json
    └── production-result.yaml
```

## 7. ドメインモデル

### 7.1 Scope baseline

```yaml
baseline_id: SB001
handoff_id: HO001
handoff_hash: "<sha256>"
selected_hypothesis_id: PH001
selection_authority: HUMAN
selected_at: 2026-08-11T22:00:00+09:00
mandatory_requirement_ids: [RQ001]
prototype_plan_ids: [PP001]
excluded_scope:
  - 屋外展示
assumptions:
  - id: AS001
    statement: 展示面積は10平方メートル以上
    validation_due: BEFORE_TECHNICAL_BASELINE
status: BASELINED
```

baseline後の意味変更はchange requestを必須とする。

### 7.2 Deliverable

```yaml
id: DL001
title: 反復要素による室内インスタレーション
type: physical-installation
source_requirement_ids: [RQ001]
technical_spec_ids: [TS001]
acceptance_test_ids: [AT001]
asset_refs: []
owner_capability: installation-production
due_milestone_id: MS004
status: PLANNED
```

### 7.3 Technical specification

仕様値は、要求値、許容差、測定方法、sourceを分ける。

```yaml
id: TS001
deliverable_id: DL001
parameter: repeated-element-spacing
target:
  value: "300"
  unit: mm
tolerance:
  minus:
    value: "3"
    unit: mm
  plus:
    value: "3"
    unit: mm
measurement_method: calibrated-tape
source_requirement_ids: [RQ001]
status: PROVISIONAL
```

会場、素材、prototype結果に依存する値は、確定前に`PROVISIONAL`とする。

### 7.4 Work PackageとTask

```yaml
work_package:
  id: WP001
  title: 実寸prototype検証
  deliverable_ids: [DL001]
  input_ids: [TS001]
  output_ids: [PRT001]
  depends_on: []
  owner_capability: fabrication
  review_gate_id: RV001

task:
  id: TK001
  work_package_id: WP001
  title: 実寸要素を三個製作する
  depends_on: []
  required_resource_ids: [RS001]
  required_material_ids: [MT001]
  acceptance_condition: 三個の寸法がTS001の許容差内
  effect_type: PHYSICAL_EXTERNAL
  approval_id: AP001
  status: READY
```

`PHYSICAL_EXTERNAL`、`PURCHASE`、`CONTRACT`、`PUBLICATION`、`DELETION`は承認なしに実行しない。

### 7.5 MaterialとResource

- Materialは仕様、数量、単位、lot、supplier候補、権利・安全情報を持つ。
- Resourceは人、技能、設備、ソフトウェア、場所、時間枠を区別する。
- 個人名が不要な計画段階では`owner_capability`を使う。
- 担当者を記録する場合は最小限の安定IDを使い、私的連絡先をGitへ置かない。
- 素材lotまたは仕様変更時は安全性と関連testを再評価する。

### 7.6 Budget

金額は`amount`、`currency`、`basis`、`confidence`、`status`を必須にする。

```yaml
budget:
  currency: JPY
  baseline_total: "120000"
  contingency: "20000"
  approval_threshold: "10000"
  items:
    - id: BI001
      category: material
      description: prototype用材料
      amount: "8000"
      basis: supplier-quote
      source_ref: quote/QT001
      confidence: MEDIUM
      status: ESTIMATED
```

状態は`ESTIMATED`、`QUOTED`、`RESERVED`、`COMMITTED`、`ACTUAL`を区別する。エージェントは`COMMITTED`へ変更する購入・契約を実行しない。

### 7.7 Schedule

- milestone、task duration、依存、calendar constraint、bufferを分離する。
- 未確定外部日程を偽の確定日時で埋めない。
- `earliest_start`、`latest_finish`、`duration_estimate`、`confidence`を使う。
- critical pathは生成物とし、手編集しない。
- venue、納品、審査など動かせない期限はsourceと再確認日を持つ。

### 7.8 Risk

Riskは最低限、category、likelihood、impact、trigger、mitigation、contingency、owner capability、statusを持つ。

カテゴリ:

- artistic
- technical
- schedule
- budget
- supply
- venue
- safety
- rights
- privacy
- operational

重大なsafety、rights、privacy riskは数値スコアに関係なく人間承認へ送る。

## 8. 状態機械

```text
DRAFT
  → HANDOFF_VALIDATED
  → PLANNING
  → READY_FOR_PROTOTYPE
  → PROTOTYPING
  → REVIEWING
  → READY_FOR_PRODUCTION
  → PRODUCING
  → READY_FOR_INSTALLATION
  → INSTALLING
  → VALIDATING
  → COMPLETE | COMPLETE_WITH_GAPS

各非終端状態 → BLOCKED | CANCELLED
BLOCKED → 記録済みresume_state（解除evidence必須）
COMPLETE* → PLANNING（明示reopenのみ）
```

`DRAFT`はprojectを一時rootで組み立てている間だけの状態とし、canonical project rootへ最初に永続化するstateは`HANDOFF_VALIDATED`とする。

| 状態 | 次へ進む条件 |
|---|---|
| `DRAFT` | 一時rootでproject ID、入力bundle、利用先を組立中。canonical rootへ永続化しない |
| `HANDOFF_VALIDATED` | receiptがACCEPTED、hash・互換性・安全検査済み |
| `PLANNING` | 採択権限、scope baseline、計画対象が確定 |
| `READY_FOR_PROTOTYPE` | 必須prototypeのtask、資源、試験、承認が解決 |
| `PROTOTYPING` | 実行中taskがleaseまたは外部待ち状態を持つ |
| `REVIEWING` | prototype resultとdeviationが記録済み |
| `READY_FOR_PRODUCTION` | 本制作仕様、WBS、予算、日程、risk、承認が基準線化 |
| `PRODUCING` | 本制作taskを実行または外部追跡中 |
| `READY_FOR_INSTALLATION` | 成果物検査、会場、搬入、設営、安全条件が解決 |
| `INSTALLING` | 設営taskを実行または外部追跡中 |
| `VALIDATING` | 必須testと最終deviationを判定中 |
| `COMPLETE` | 必須要件合格、成果物参照、result、完了報告が有効 |
| `COMPLETE_WITH_GAPS` | 利用可能だが外部検証gapと再開条件がある |
| `BLOCKED` | blocker、影響、解除条件、ownerがある |
| `CANCELLED` | 権限ある中止理由と保持方針がある |

状態遷移の正本はhash chain付き`run-log.jsonl`とし、`production-state.json`は検証済みmaterialized projectionとする。遷移guard、BLOCKEDからのresume、prototype/installation skip、reopen、crash recoveryは実装契約仕様§7、§8を正本とする。

## 9. 標準実行手順

1. **受領** — bundleを隔離された一時領域へ取得する。
2. **検証** — schema、hash、path、秘密、権利、互換性を検査する。
3. **採択** — authorityを確認し、仮説を選択または人間待ちにする。
4. **Scope固定** — 必須要件、除外、仮定、gapをbaseline化する。
5. **仕様化** — deliverable、技術値、許容差、測定方法を定義する。
6. **分解** — Work Package、Task、依存、review gateへ分解する。
7. **資源計画** — 技能、担当、設備、素材、場所、調達候補を割り当てる。
8. **予算・日程** — 根拠とconfidence付きでbaselineを作る。
9. **Risk review** — mitigation、contingency、承認境界を確認する。
10. **Prototype** — 最小実験を実行または外部実行依頼として記録する。
11. **Review** — 表現、技術、要件適合性を別々に評価する。
12. **変更統制** — baseline差分を承認、棄却、research差戻しにする。
13. **本制作** — 依存順にtaskを実行・追跡し、版とeffectを記録する。
14. **設営** — 会場再確認、搬入、安全検査、設営結果を記録する。
15. **受入** — 必須要件、技術仕様、deviation、権利を検証する。
16. **還流** — production result bundleを決定的に生成する。
17. **終了** — terminal statusと再開条件を固定する。

## 10. PrototypeとReview Gate

Reviewは次を混同しない。

| Review | 問い | 主な判定者 |
|---|---|---|
| Technical | 動くか、測れるか、安全か | technical validator |
| Artistic | 意図した経験が成立するか | artist / authorized reviewer |
| Requirement | handoff要件に適合するか | production validator |
| Rights/Privacy | 使用・展示・記録が許可されるか | authorized human |
| Feasibility | 予算・日程・資源内で完成できるか | production planner |

AIは観客反応や物理結果を捏造しない。実施できないtestは`EXTERNAL_VALIDATION_REQUIRED`とし、必要な手順、記録形式、解除条件を出す。

## 11. 変更管理

変更要求は次を必須とする。

```yaml
id: CR001
trigger: prototype-test-failed
requested_change: 要素間隔を300mmから240mmへ変更する
affected_ids: [TS001, TK004, BI003, MS004, RQ001]
impact:
  artistic: 中断の知覚が強くなる可能性
  technical: 治具を再製作
  budget_delta: "12000"
  schedule_delta_days: 2
  rights_safety: NONE
alternatives:
  - 現仕様を維持し照明だけ変更
research_review_required: true
approval_required: HUMAN
status: PROPOSED
```

影響分類:

- `NONE`: baseline意味に影響しない記録修正
- `MINOR`: production内部で要件意味を変えず処理可能
- `MAJOR`: handoff要件、制作仮説、主要予算・期限へ影響。research reviewを要求
- `CRITICAL`: 中心命題、重大安全、権利、公開可否へ影響。制作停止と人間承認

## 12. 自律実行と承認境界

### 12.1 自律実行してよいこと

- bundleのread-only検証
- 仕様・WBS・日程・予算・riskの案の生成
- 定義済み上限内のsimulation、静的解析、合成fixture試験
- task lease、依存解決、再試行、blocker分類
- 外部実施用の指示書、チェックリスト、記録様式の生成
- completion gap、変更要求、production resultの生成

### 12.2 人間承認が必要なこと

- `HUMAN_SELECTION_REQUIRED`な制作仮説の採択
- 購入、発注、契約、外注、支払い
- 応募、公開、配信、外部送信
- ファイル、asset、記録の削除
- 物理作業、危険工具、電気、吊り物、構造、化学物質、火気
- 肖像権、著作権、第三者プライバシーに重大な不確実性がある利用
- baseline予算・期限の閾値超過
- MAJOR/CRITICALなresearch要件変更
- 最終的な芸術判断を作者に代わって確定すること

承認recordはaction、target revision、target hash、approver authority、timestamp、有効期限、制約を持つ。wildcard承認を禁止し、対象内容が変われば無効とする。承認は外部行為の実施証明ではなく、完了には別のexternal evidenceを要求する。詳細は実装契約仕様§9を正本とする。

## 13. Runtime

### 13.1 Task execution

- eligible taskを`(priority, earliest_start_or_max, task_id)`の昇順で一意に選ぶ。
- task leaseは期限付きで、owner、acquired_at、expires_atを持つ。
- effect keyで重複する外部効果を拒否する。
- read-only、repository write、external write、physical externalを区別する。
- retryは分類済み一時障害だけに限定する。
- 承認拒否、権利不明、安全blockerをretryしない。
- v1はsingle canonical writerとし、workerはlease経由で作業して正本fileを直接更新しない。
- event append、hash chain、state projection更新、partial write検知は実装契約仕様§8を正本とする。

### 13.2 Context pack

workerへ渡す情報は次に限定する。

- taskと完了条件
- 直接依存する仕様・asset refs
- 関連するhandoff requirementと禁止事項
- 必要なresource/material
- 該当riskと承認
- 記録すべきresult schema

project全体、原証拠、無関係な個人情報を無条件に渡さない。

### 13.3 停止規則

開始時に、最大task数、retry回数、予算消費、外部待ち時間、iteration回数を固定する。上限到達時は推測で続けず、`COMPLETE_WITH_GAPS`または`BLOCKED`とする。

## 14. Asset・権利・秘密境界

| 対象 | Git保存 | 扱い |
|---|---:|---|
| Markdown/YAML/JSONの正本 | 可 | 秘密検査後に保存 |
| 小さな権利明確fixture | 可 | 合成または明示ライセンス |
| 完成作品・RAW・動画・音声・3D | 原則禁止 | asset URI、版、hashを保存 |
| 見積書・契約書原本 | 禁止 | 許可済み保管先のopaque URI |
| 個人連絡先・会話本文 | 禁止 | 必要最小限の派生情報だけ |
| API key、credential、signed URL | 禁止 | secret manager参照 |
| 権利不明素材 | 禁止 | 棄却またはgap |

asset registerは、asset ID、URI scheme、content hash、version、media type、rights status、retention、created_by、source taskを持つ。v1既定schemeは`urn`とqueryなし`https`とし、認証情報、userinfo、signed query、fragment、local pathをURIへ埋め込まない。scheme追加はpolicy変更とsecurity testを要求する。

## 15. Production Result契約

### 15.1 所有権

- production result schemaの正本は本リポジトリとする。
- research側は対応版のimmutable snapshotとsource commitを保存する。
- handoff request schemaはresearch側が正本で、本repoはsnapshotを保存する。

### 15.2 最小構造

```yaml
schema_version: 1.0.0
result_id: PR001
production_project_id: production/harmony-study-v1
production_commit: "<40-character-git-sha>"
generated_at: 2026-08-20T18:00:00+09:00

accepted_handoff:
  id: HO001
  content_sha256: "<sha256>"
  research_project_id: project/harmony-study
  research_commit: "<40-character-git-sha>"

selection:
  selected_hypothesis_id: PH001
  authority: HUMAN
  approval_ref: AP001

outputs:
  - id: OUT001
    deliverable_id: DL001
    uri: urn:asset:harmony-study:installation:v1
    version: 1
    sha256: "<sha256>"
    rights_status: PROJECT_INTERNAL

test_results:
  - acceptance_test_id: AT001
    result: PASS
    executed_at: 2026-08-20T15:00:00+09:00
    conditions: 実会場、通常照明
    evidence_ref: urn:asset:harmony-study:test:at001

observations:
  - id: OB001
    statement: 入口方向からは中断の発見まで平均12秒を要した
    method: authorized-observer-record
    limitations: 参加者3名の小規模確認
    related_requirement_ids: [RQ001]

deviations: []
incidents: []
research_change_requests: []
open_gaps: []
integrity:
  content_sha256: "<sha256-of-canonical-payload>"
```

resultは観察と解釈を混同しない。観客調査は同意、匿名化、目的、人数、限界を持たない限り一般化しない。

## 16. 検証と監査

blocking validation:

- schema不適合
- handoff hash・result hash不一致
- broken ID、duplicate ID、DAG循環
- 必須deliverableに要件またはtest接続がない
- baseline後の未承認直接変更
- actual costに通貨または根拠がない
- READY taskに未解決依存・resource・approvalがある
- 禁止データ、秘密、外部path、unsafe archive
- terminal状態にcompletion reportまたはresultがない
- event logのsequence、hash chain、state projectionが一致しない
- manifest、provenance、bundle snapshotのfile setまたはhashが一致しない

non-blocking audit:

- 見積confidenceが低い
- contingency不足
- single supplier dependency
- stale venue/material/rights information
- critical path buffer不足
- owner capability未割当
- 未実施の任意test
- 繰返しiterationが停止上限へ近い
- requirementに対する過剰制作またはscope creep

## 17. 完了条件

`COMPLETE`には次をすべて要求する。

- accepted handoffとscope baselineが有効。
- 全必須deliverableが完成版asset refを持つ。
- 全必須requirementがPASS、または権限ある承認済みdeviationを持つ。
- technical specificationの必須測定が完了。
- actual budgetとvarianceが記録済み。
- installation対象なら設営・安全結果が記録済み。
- open MAJOR/CRITICAL riskがない。
- rights、privacy、publication statusが明記済み。
- production resultとcompletion reportがschema-valid。
- 全ID、hash、source commitが解決する。

`COMPLETE_WITH_GAPS`は、成果物が利用可能であり、残るgap、影響、owner、期限、再開条件が明記される場合だけ許可する。必須安全検査、権利、重大要件をgapのまま完成扱いにしない。

## 18. 人間向け出力とエージェント向け出力

人間が通常読むもの:

- `production-brief.md`
- `scope-baseline.yaml`の要約
- `schedule.md`
- `budget-summary.md`
- `prototype-review.md`
- `risk-summary.md`
- `completion-report.md`

エージェントが読むもの:

- `manifest.yaml`
- `production-handoff.yaml`
- `scope-baseline.yaml`
- `deliverables.yaml`
- `technical-specifications.yaml`
- `task-plan.yaml`
- `resource-plan.yaml`
- `approval-register.yaml`
- `production-state.json`
- task固有context pack

### 18.1 Canonical planとagent harness

`03_plan/production-plan.yaml`が、handoffから導出された機械可読なproduction planの唯一のcanonical aggregateである。`03_plan/production-plan.md`は同じaggregateから生成する人間向け統合出力であり、別の計画内容を持たない。分割registerとtask contextはcanonical planから再生成する。

agent実行は`08_runtime/agent-harness/`のrun、context、grant、invocation、action、responseとappend-only `agent-run-log.jsonl`で監査する。workerはtask-scoped contextをstdinで受け、schema-valid action proposalだけをstdoutへ返す。brokerだけが提案を検証・記録し、runtimeのcanonical stateを直接変更しない。`READ_ONLY`と`REPOSITORY_WRITE`はmetadata/proposalの範囲で自律実行できるが、external、physical、purchase、contract、publication、deletionは`REQUEST_APPROVAL`または`REQUEST_EFFECT`として停止する。

上限、許可action/tool、adapter protocol、secret環境変数、隔離profileは`config/agent-harness-policy.yaml`が正本である。`tools/run_agent_harness.py`の`start`、`step`、`run`、`resume`、`status`、`cancel`と、context/worker/broker CLIは固定timestamp、lease hash、idempotency、replayを要求する。cancellationはHUMAN authorityと保持判断が必要で、workerの環境にはcredentialやlocal pathを渡さない。

## 19. 初期実装フェーズ

詳細は実行計画を正本とする。

1. **M0 Bootstrap** — repo規則、設定、schema骨格、task queue
2. **M1 Contract** — handoff受理、project、scope、基礎domain schema
3. **M2 Planning** — deliverable、spec、WBS、resource、budget、schedule、risk
4. **M3 Prototype** — test、review gate、iteration、change control
5. **M4 Runtime** — state、lease、retry、approval、resume、context pack
6. **M5 Execution** — output version、quality、installation、asset refs
7. **M6 Feedback** — production result、research compatibility、impact
8. **M7 Release** — representative fixture、security、chaos、docs、release gate

## 20. 代表受入シナリオ

### 20.1 正常系

1. researchのHO001 bundleを受け取る。
2. schema、path、hash、秘密検査を通しreceiptをACCEPTEDにする。
3. PH001を権限あるselection recordで採択する。
4. RQ001からDL001、TS001、WP/TK、budget、schedule、riskを生成する。
5. PP001を実施済みfixtureとして取り込み、AT001を評価する。
6. prototype FAILからCR001を作り、承認後にTS001 revision 2を作る。
7. kill-and-resumeでtask effectを重複させず制作を完了する。
8. asset本体を保存せずOUT001のURI、版、hashを記録する。
9. PR001を生成し、research fixtureが受理できることを検証する。

### 20.2 失敗系

- handoff hash改変を拒否する。
- 非対応handoff versionをremediation付きで拒否する。
- `HUMAN_SELECTION_REQUIRED`を承認なしで進めない。
- task DAG循環を拒否する。
- 承認なしのpurchase effectを拒否する。
- signed URL、credential、private rawを拒否する。
- prototype未実施をPASSと記録できない。
- baseline要件の直接変更を拒否する。
- 同じeffectの再実行を拒否する。
- CRITICAL safety incidentで制作を停止する。
- 同じresult IDの異内容を拒否する。

## 21. 非機能要件

- Python 3.11以上。
- 初期依存はPyYAMLとjsonschemaを中心に最小化する。
- Markdown、YAML、JSONL、JSON、Git、CLIを初期基盤とする。
- 同じ正本と注入timestampからbyte-identicalな生成物を作る。
- canonical hashは実装契約仕様§5の`json-sort-keys-compact-utf8-v1`だけを使う。
- エラーはfile、line/field、rule、reason、remediationを含む。
- 外部serviceなしで全主要経路をfixture実行できる。
- task runtimeは中断、再試行、重複effectに対して決定的である。
- 金額は通貨を必須とし、浮動小数点で暗黙丸めしない。
- 単位は明示し、長さ・重量・時間を無単位numberで保存しない。
- 日時はRFC 3339、timezone必須。期間と締切を混同しない。
- generated dataを手編集しない。

## 22. 設計決定

| 論点 | 決定 | 理由 |
|---|---|---|
| researchとの関係 | pipeline統合、repoと正本は分離 | 認識上の判断と実行上の事実を混同しないため |
| 入力 | versioned handoff bundle | 会話や長文reportに依存しないため |
| requirement変更 | change requestのみ | 上流根拠との追跡を維持するため |
| asset | 外部保管、URI・版・hash参照 | Git肥大化、権利、秘密を避けるため |
| 予算 | estimate/quote/commit/actualを分離 | 推定と支出を混同しないため |
| task実行 | effect typeとapprovalを明示 | 外部行為の越権を防ぐため |
| 物理結果 | external validationを許容 | AIが実施を捏造しないため |
| production result | 本repoがschema正本 | 実施条件と出力意味を生成側が保証するため |
| runtime | file-based state machineから開始 | 監査性、再開性、実装可能性を優先するため |
| Web/API | 後段 | domain contract確定前の二重実装を避けるため |

## 23. 未決事項の扱い

Bootstrapを開始するために必要な次の事項は、実装契約仕様で確定した。

- canonical hashとintegrity除外範囲
- 金額の10進表現、tax、丸めのfail-closed方針
- unitの最低語彙とexact conversion方針
- asset URIの既定scheme
- payload、archive、file count、pathの上限
- task priority、resource contention、single-writer規則
- event logとstate projectionの正本関係
- approval targetと外部evidenceの分離

critical pathは、明示durationとdependency DAGから最長経路を求め、resource levelingを含めないv1基本値とする。resource制約を反映した日程は別のschedule simulationとして区別する。

外部依存として残るのは、research側がhandoff schema、export bundle manifest、expected fixtureをclean commitで固定することである。これが未完了でも`BOOTSTRAP-001`は開始できるが、`CONTRACT-001`を完了扱いにしない。

責任分界、人間承認、安全・権利境界、要件変更手順、実装契約仕様の固定値を実装中に推測で変更しない。変更が必要なら設計、schema、migration、fixture、versionを同時に更新する。

## 24. 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0.0 | 2026-08-11 | 制作システム全体の初期確定仕様 |
| 1.1.0 | 2026-08-11 | 実装契約を分離し、bundle自己完結性、canonicalization、共通型、runtime、承認、安全上限をBootstrap前に確定 |
