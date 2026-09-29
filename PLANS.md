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

## HANDOFF-REFERENCE-URL-REASONS-001 / Issue #83

### Progress

- [x] Accepted handoff source references now carry explicit missing-URL reason codes through the Research source-ref contract.
- [x] Production planning applies configured blocking rules by reason code and required-category dependency; legacy handoffs remain blocking.
- [x] Repository-wide production quality gates passed after commit: 191 unittest, validator, and evaluation.
- [x] (2026-09-28 JST) Review follow-up: category-level reasons are retained in planning gaps, all reasons are evaluated for actionability, old handoffs synthesize `LEGACY_UNSPECIFIED`, and `SOURCE_HAS_NO_PUBLIC_URL` is blocking unless an alternate permanent URL covers the same category. Commit `97f2e3d`; clean full 193 tests PASS.

### Decision Log

`SOURCE_HAS_NO_PUBLIC_URL`, `CATEGORY_NOT_MAPPED`, `URL_NOT_PERMANENT`, and legacy missing reason codes are blocking for required categories. `INTERNAL_RECORD` is relaxed only when another permanent URL covers the same category. This preserves fail-closed behavior where the accepted handoff cannot establish a usable source.

### Outcomes

Qualified locally at commit `97f2e3d`; no public catalog, URL, production project, or external artifact was modified. Next start point is orchestrator review; no push or PR was performed.

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

## Issue #82 — archive-safe quality gates

### Purpose / Big Picture

`.git`を持たない`git archive`展開先でも、品質ゲートがリポジトリ自身の可変Git状態に依存せず、archiveに埋め込まれたcommitへ決定的にフォールバックできるようにする。

### Progress

- [x] `.archive-commit` と `export-subst` 属性を追加した。
- [x] Git HEAD → archive marker → 明示エラーのcommit解決を共通化した。
- [x] production result、public-plan attestation、関連テストをarchive-safeな解決へ移行した。
- [ ] 親側の`pin_adopt.py --dry-run`はmerge後にオーケストレーターが確認する。

### Surprises & Discoveries

品質ゲートでリポジトリ自身の`.git`を直接読む箇所は、result builder、public-plan attestation、およびattestation関連テストに限定されていた。テスト中に作成する一時Git repositoryの操作はarchive展開とは独立しているため変更していない。

### Decision Log

共通helperはGitの`rev-parse HEAD`を先に試し、失敗時だけ`.archive-commit`を読む。未置換の`$Format:%H$`、欠落、非hex値は空値や固定値へ補正せず、呼び出し側の明示エラーにする。archiveはimmutableであるため、public attestationのclean-tree確認だけは`.git`不在時にスキップする。

### Outcomes & Retrospective

通常cloneとarchiveの双方で同一commit provenanceを使える実装と回帰テストを追加した。変更はcommit provenanceの取得経路だけで、外部効果、公開、購入、物理作業は実施しない。

### Validation and Acceptance

```bash
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format json
git diff --check
git archive --format=tar HEAD | tar -x -C <temporary-directory>
```

archive展開先でも上記3 quality gateを実行し、通常cloneと同じ成功結果を確認する。

### Idempotence and Recovery

同一commitから生成したarchive markerは同じ40桁SHAを解決する。markerが壊れている、未置換、欠落している場合は再生成元のclean commitを明示して停止する。自動修復や推測値の注入は行わない。

### Context and Orientation

commit解決の正本は`tools/lib/provenance.py`、archive metadataは`.archive-commit`と`.gitattributes`、関連回帰は`tests/test_archive_provenance.py`である。

### Plan of Work / Milestones

1. commit解決helperとarchive metadataを追加する。
2. production resultとpublic-plan attestation、および自身のcommitを読むテストを移行する。
3. commit後の通常clone・archive quality gateを実行する。

### Concrete Steps

作業treeをcleanにcommitした後、`git archive`で一時directoryへ展開し、`.archive-commit`が40桁SHAへ置換されたことを確認して3つのquality gateを実行する。

### Interfaces and Dependencies

`git archive --format=tar`の`export-subst`、Python標準libraryの`subprocess`、および既存のproduction result/public attestation APIだけに依存する。外部ネットワークや外部providerは不要である。
