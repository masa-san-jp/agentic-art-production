# Repository instructions

## Mission

設計仕様に従い、`agentic-art-research`の制作引き渡しから、制作計画、試作、本制作、設営、受入、結果還流まで追跡可能なAgentic Art Productionを完成させる。

## Read order

1. `docs/20260811-agentic-art-production-system-design-specification.md`
2. `docs/20260811-agentic-art-production-implementation-contract-specification.md`
3. `docs/20260811-agentic-art-production-repository-execution-plan.md`
4. `PLANS.md`
5. `execution/task-queue.yaml`
6. `docs/agent-startup.md`
7. `docs/operations-runbook.md`
8. `docs/schema-reference.md`
9. `docs/release-gate.md`
10. 変更対象に最も近い文書とテスト

## Repository/output boundary

- 本repoはprotocol、config、schema、template、validator、test、合成fixtureの正本とする。AAK-11の限定追加として、正規validatorを通したcurated knowledgeのみを明示owner Gitの`knowledge/production/`へ保存する。codeとknowledge commitを分離し、実project/runtime/raw/作品本文を知識storeへ移さない。保存・再利用手順は`docs/production-memory.md`を正とする。
- 実projectはGit外の明示output rootへ生成し、本repoの`projects/`や`data/`へ常設しない。
- 実projectの出力先は実行時に明示するリポジトリ外の`<external-output-root>/<project-id>/`とし、machine固有absolute pathをtracked fileへ保存しない。
- output rootはCLI引数またはGit管理外local configで指定する。
- handoffはcommit・schema hash・manifestで固定されたbundleだけを受理し、隣接research working treeを直接読まない。

## Fresh-clone startup

fresh cloneには依存関係が入っていない前提で、最初に次の条件付きpreflightを行う。`.venv/bin/python` が無い、または`yaml`と`jsonschema`のimportに失敗した場合だけrepository-localの環境を準備する。

```bash
if ! test -x .venv/bin/python || ! .venv/bin/python -c 'import yaml, jsonschema' >/dev/null 2>&1; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
```

このpreflight以外の通常task実行ではinstallやnetwork accessを暗黙に行わない。`.venv/`は環境生成物でcommitしない。セットアップ後の宣言済みcheckは必ず`.venv/bin/python`で実行する。

```bash
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_evaluation.py --format json
```

## Work protocol

- 複雑な変更は`PLANS.md`に従うExecPlanとして実行する。現在の正本は上記実行計画。
- 依存関係が完了した最小IDの`READY` taskを選び、原則一件ずつ完了させる。
- 実装中に実行計画のProgress、Surprises & Discoveries、Decision Log、Outcomesを更新する。
- セッション記憶を前提にせず、別エージェントがrepoだけで再開できる状態を残す。
- 曖昧さは設計仕様、実装契約仕様、テスト、保守的な拒否の順で解決する。仕様にない既定値を追加しない。
- 人間確認は設計仕様の承認境界に該当する場合だけ行う。
- 実装後は`.venv/bin/python -m unittest discover -s tests -v`と`.venv/bin/python tools/validate.py --check`を実行する。
- 完了時にtask状態、実行command、結果、残課題、次の開始点を更新する。

## Completion evidence

- acceptanceの全commandが成功してからだけtaskを`DONE`にする。失敗は成功扱いにせず、named failureまたは未解決gapとして残す。
- 完了時はtask ID、対象project、変更path、acceptance commandと結果、commit/source commit、残課題、次の開始点、Git外project/resultの場所、機微情報と外部artifactの有無を実行計画またはtask handoffへ記録する。
- 物理作業、外部effect、公開、購入、契約、支払い、削除、外部provider writeはhuman approvalなしに完了扱いにしない。未実施は`EXTERNAL_VALIDATION_REQUIRED`、承認待ちは`HUMAN_APPROVAL_REQUIRED`として保持する。

## Engineering rules

- Python 3.11以上。初期依存を最小化する。
- ID、状態、語彙、単位、approval、pathをhard-codeで分散させず`config/`と`schemas/`を参照する。
- 正本と生成物を分け、`data/`を手編集しない。
- 失敗を黙って補正しない。入力file、field/line、rule、reason、remediationを返す。
- 新機能に正常系、失敗系、再実行系testを追加する。
- 既存仕様を破る場合、設計、schema、migration、test、versionを更新する。
- 物理作業や外部effectを実施したと偽らず、fake adapterまたは`EXTERNAL_VALIDATION_REQUIRED`を使う。

## Safety

- `PRIVATE_RAW`、`RESTRICTED`、credential、個人連絡先、契約書原本、signed URLをGitへ入れない。
- 完成作品、RAW、動画、音声、3D、大容量assetはURI、版、hash、権利区分だけを保存する。
- 外部送信、公開、応募、購入、契約、支払い、削除、危険な物理作業を人間承認なしに実行しない。
- 権利不明素材は採用せず、理由付き棄却またはgapとして残す。
- research要件を黙って変更せず、change requestと影響分析を作る。
