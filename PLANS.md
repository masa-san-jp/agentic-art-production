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

## AP-03-PRODUCTION owner repair — completed

### Purpose / Big Picture

Issue #241のProduction owner taskとして、通常のautomatic-plan attestationと明示的なmanual/publication reviewの境界を保持したまま、owner品質ゲートをclean immutable checkoutで通過させる。Productionのplan、approval、physical work、publicationの意味は既存owner契約を正本とする。

### Progress

- [x] automatic attestation、manual review、revocation/revision、resumeの既存契約と回帰を確認した。
- [x] macOSの`/var` aliasがcurated production knowledge storeを拒否するpath guardを共通helperへ置換した。
- [x] caller-created symlink拒否の回帰を追加し、clean owner full suiteとevaluationを実行した。
- [ ] default branch merge、release、physical work、external publicationは実施しない。

### Surprises & Discoveries

既存Production full suiteの9件は`production_memory.safe_root`がmacOS `/var`→`/private/var` aliasをcaller symlinkとして扱うことだけで失敗していた。修正中のdirty checkoutではattestationテストが意図的に停止するため、owner commit後のclean checkoutで最終ゲートを再実行した。

### Decision Log

`/var`と`/tmp`のOS-owned aliasだけをcanonicalizeし、その他のparent/final symlinkは拒否する。automatic-plan attestationにhuman consentを追加せず、manual/publication reviewの既存native approval境界も変更しない。Production knowledgeはcode commitと分離し、parentへdomain payloadを複製しない。

### Outcomes & Retrospective

Candidate `308abfe0f90582b5dd47d17cc5776278d32d5bb3`はsource `7bab731acea20aa86b45efc99a0724d5bd569953`からの専用branchに固定した。focused 11 tests、full 132 tests、validator、evaluation、diff checkはPASS。証跡は`execution/ap-03-production-verification.json`に保存した。remote default branch、merge、release、physical work、external publicationは未実施。

### Validation and Acceptance

```bash
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format json
git diff --check
```

最終結果は上記4コマンドすべてPASSで、full suiteは132 tests、既存のnegative-case diagnosticsは期待通り出力された。

### Idempotence and Recovery

同じpathは同じcanonical resultを返し、同じrecord/revisionのknowledge writeは既存idempotencyを使う。symlink、code-store overlap、dirty producer checkout、approval失効、plan変更は引き続きfail closed。次はこのbranchをpushしてdraft PRのheadを親証跡へ記録する。
