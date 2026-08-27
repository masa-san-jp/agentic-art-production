# v1.0.0 release gate

`RELEASE-001`は、protocol repositoryの検証済みcommitをv1.0.0候補として判定する。公開、tag、配布、外部通知はこのgateに含めず、人間の明示承認後にだけ行う。

## Gate内容

`tools/run_release_gate.py`は、同じclean commitに対して次を指定回数連続で実行する。

1. repository contract validation
2. full unit/contract test suite
3. deterministic EVAL-001 matrix
4. representative agent harness E2E, replay, security, and chaos checks

既定の`--runs 3`では、3回すべてがexit 0、validatorが`[]`、evaluationが`PASS`、agent harness専用testが`PASS`であることを要求する。一度でも失敗した場合は候補を`FAIL`とし、成功回数だけで通過扱いにしない。

## 実行

gateはcommit後のclean working treeで実行し、evidenceはprotocol repositoryの外へ保存する。

```bash
RELEASE_EVIDENCE_ROOT="$(mktemp -d /tmp/agentic-art-production-release.XXXXXX)"
.venv/bin/python tools/run_release_gate.py \
  --runs 3 \
  --evidence "$RELEASE_EVIDENCE_ROOT/v1.0.0/release-gate.yaml" \
  --format json
```

evidenceにはcandidate、gate status、UTC生成時刻、検証commit SHA、repository clean status、各runのcheck statusとstdout/stderr hashだけを記録する。library APIで`verified_commit`を指定する場合も、現在の`HEAD`と完全一致しなければ失敗させる。temporary path、asset body、credential、PRIVATE_RAW、signed URL、外部effectの結果は保存しない。

## 判定とhandoff

`PASS`の場合、evidenceの`verified_commit`とGitHub ActionsのCI run URL/commitを、実行計画のRELEASE-001 handoffへ転記する。判定は`HUMAN_APPROVAL_REQUIRED`のままであり、`v1.0.0` tag作成、release publication、外部通知は別の人間承認操作である。

`FAIL`の場合は、evidenceのcheck名とhashを根拠に原因を修正し、同じcommitの再実行ではなく、修正後の新しいclean commitで3回をやり直す。自動retryで失敗履歴を隠さない。
