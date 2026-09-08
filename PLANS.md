# Execution Plans

ExecPlanは、複数file、schema、state、CLI、migration、外部contractにまたがる変更を、別セッションのエージェントが引き継いで完了できる自己完結型計画である。

## 使用条件

次のいずれかならExecPlanを使用する。

- 三file以上を変更する。
- 新しいschema、state、CLI、migration、外部contractを追加する。
- 複数sessionになる可能性がある。
- 設計判断、prototype、外部依存、approval境界の確認が必要である。

## 必須セクション

- Purpose / Big Picture
- Progress
- Surprises & Discoveries
- Decision Log
- Outcomes & Retrospective
- Context and Orientation
- Plan of WorkまたはMilestones
- Concrete Steps
- Validation and Acceptance
- Idempotence and Recovery
- Interfaces and Dependencies

## 実行規則

1. 計画全体と設計仕様を読む。
2. Progressと`execution/task-queue.yaml`の整合を確認する。
3. 依存完了済みの最小ID `READY` taskだけを`IN_PROGRESS`にする。
4. 小さな観察可能動作ごとにtestする。
5. 発見と決定を直ちに計画へ戻す。
6. 正常・失敗・再実行の受入条件を満たすまで`DONE`にしない。
7. 終了時に次の正確な開始点とcommandを残す。

計画は会話履歴、暗黙の判断、特定agentの記憶に依存してはならない。外部effect、approval、asset、private dataの境界を省略しない。

## AAK02 native reference actionability

### Progress
- [x] Actual LLM-produced Research handoff exposed unconditional URL demand on internal decisions/insights.
- [ ] Match existing required-category and nonblocking-gap policy; preserve hash/URI/native validation.
- [ ] Run owner focused/full/evaluation gates and publish separate draft PR.

### Decision Log
Do not fabricate external URLs for canonical internal decisions. Required CONCEPT/VISUAL/METHOD remain externally available; every nonblocking gap still needs an explicit method uncertainty/check. No execution or public authority changes.

### Outcomes
Pending qualification; base75e793c8ec968da5cd5bec7bfa6aa988dcfeaf6f; branchagent/aak-02-reference-actionability; parent197 owns live acceptance.
