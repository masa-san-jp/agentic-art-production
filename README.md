# Agentic Art Production

`agentic-art-research`が生成した制作仮説・要件・Prototype Planを受け取り、制作仕様、工程、資源、予算、試作、本制作、設営、受入、結果還流までを追跡可能にする制作基盤です。

設計硬化と`BOOTSTRAP-001`が完了しています。現在の実装は、自己完結handoff bundleの検証とGit外output rootへのproduction project生成までです。実装エージェントは次の順で読みます。

1. `AGENTS.md`
2. `docs/20260811-agentic-art-production-system-design-specification.md`
3. `docs/20260811-agentic-art-production-implementation-contract-specification.md`
4. `docs/20260811-agentic-art-production-repository-execution-plan.md`
5. `PLANS.md`
6. `execution/task-queue.yaml`

`CONTRACT-001`は実プロジェクト`harmony-study`のREADY handoff/export bundleを受理し、完了しました。同一handoffの冪等再受理、research handoff schema snapshot、Production-ownedの`schemas/production-result.schema.json` v1、registry hash、bundle内common schemaのoffline参照解決が確定しています。受理済みprojectはGit外output rootの`production/harmony-study`です。次の開始点は`PLANNING-SCHEMA-001`です。

## Local checks

システムPythonへ依存を追加せず、repository-local virtual environmentで実行します。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
```

最小handoff fixtureからの生成確認:

```bash
AAP_BOOTSTRAP_ROOT="$(mktemp -d /tmp/agentic-art-production-bootstrap.XXXXXX)"
.venv/bin/python tools/new_production.py smoke \
  --handoff tests/fixtures/handoff/minimal \
  --output-root "$AAP_BOOTSTRAP_ROOT"
.venv/bin/python tools/validate.py \
  --project-root "$AAP_BOOTSTRAP_ROOT/production/smoke"
```
