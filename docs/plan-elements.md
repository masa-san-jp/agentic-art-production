# 段階 D の要素 CLI（制作プラン）

Issue #94。research の段階 C（`agentic-art-research` `docs/work-elements.md`）から受け取った handoff を受理した制作 project で、
制作の手順（`02_specification/production-method.yaml`）を要素単位で作る。仕組みは段階 B・C と同じで、
`element-request/v1` を1件ずつ出し、`element-answer/v1` を1件受け取り、プログラムで確かめ、落ちた要素だけを
既定5回までやり直す。設計の正本は orchestration の `docs/20261007-element-harness-design.md` 7章。

```bash
.venv/bin/python tools/plan_elements.py next --project-root <external-production-project> --run-id plan-001 --now <RFC3339>
.venv/bin/python tools/plan_elements.py answer --project-root <external-production-project> --now <RFC3339> < answer.json
```

答え手はファイルを書かない。CLI はモデルもネットワークも呼ばず、物の制作・購入・公開・依頼を承認しない
（`external_effects_authorized: false`）。

## プログラム（推論を使わない）

- 開始時に、受理済み handoff の hash と、method なしで組んだ計画（作業・材料・資源・要件）を checkpoint
  （`08_runtime/plan-elements.json`）に固定する。handoff 本文の正規 hash と受理済み bundle 全ファイルの相対パス・SHA-256 を毎回照合し、本文・artifact の変更や symlink があれば再開を拒否する。method ファイルが
  既にある project では始めない。
- 工程・日程・予算・結線・試作の描画は `build_plan.py` / `build_prototype.py`、判定は `plan_actionability` を使う。
- 仕様の理由・確認方法・担当・条件、手順の担当・開始条件（依存作業の完了と、外部作業の承認）・材料/資源/要件/仕様の
  結線、gap の担当は計画の記録から決める。
- 物理作業ごとに答えた材料と道具は、method の `materials` / `resources` に提案として書く。`build_plan.py` は
  それを `CANDIDATE` の材料・`REQUIRES_CONFIRMATION` の資源として登録する（購入・在庫・入手の証拠ではない）。
- 寸法の値に含まれる「数 単位（mm / cm / m）」は、試作の描画が使う `QUANTITY` の技術仕様になる。
- 全要素が通ったら method を書き、`build_plan.py` → `build_prototype.py` → `plan_actionability` の順に実行する。

## 推論（1要素1推論）

1. `medium`: physical / digital / hybrid から1つ。
2. `environment.location`: 制作・設置の場所と環境を1文。
3. `spec.<name>`: `config/plan-actionability.yaml` が媒体ごとに求める仕様を1つずつ（寸法・解像度は数値を含む）。
4. 作業ごと: `step.<TK>.instruction`（手順を1文）、`step.<TK>.completion`（完了の確かめ方を1文）。
   物理作業（`PHYSICAL_EXTERNAL`）はさらに `step.<TK>.material`、`step.<TK>.material-quantity`
   （「数 単位」。単位は `config/units.yaml`）、`step.<TK>.tool`。
5. 計画の gap ごと（承認台帳で扱う `PLANNING_EXTERNAL_READY` を除く）: `gap.<ID>.method`（確かめ方・解消の仕方）と
   `gap.<ID>.condition`（解消したとみなす条件）。gap の一覧は、手順まで答えた method で組んだ計画から
   プログラムが取る。

## 確認とやり直し

- 答えた時点で、空・プレースホルダ・文字数・1文・数値・数量の形をプログラムで確かめる。プレースホルダと数値の
  規則は `config/plan-actionability.yaml`（`placeholder_fragments`、`placeholder_values`、
  `numeric_specifications`、`specification_patterns`）で、`plan_actionability` の判定と同じもの。
  寸法は数値と mm / cm / m、解像度は縦横の数値またはピクセル数を求める。NFKC 正規化と寸法抽出は描画の QUANTITY 変換と共有し、全角数字や日本語に接する単位も同じように扱う。
- 組み立て後の判定で、要素に結びつく指摘（`PLACEHOLDER_VALUE: <場所>`、`SPECIFICATION_NOT_CONCRETE`、
  `GAP_RESOLUTION_REQUIRED`、`PROCEDURE_REQUIRED`）が出たら、その要素だけを `previous_failure` 付きで再依頼する。
  同じ要素の上限は答えた時点の失敗と合わせて5回。
- 飛ばせる要素: 外部への影響がない作業（READ_ONLY / REPOSITORY_WRITE）の手順と、gap の確かめ方。
  上限に達したら `abandoned` に記録して進み、計画は `INCOMPLETE` のまま指摘を残す。
- それ以外（媒体、環境、仕様、外部作業の手順・材料・道具）が上限に達したら `BLOCKED` で止まり、要素名と最後の
  検査を返す。`BLOCKED` の run は再開できないので、新しい制作 project でやり直す。

## 完了の意味

`status: COMPLETED` は段階 D の要素依頼が終わったことを表す。`completion.plan_status` が
`plan_actionability` の判定（`PLAN_READY` / `INCOMPLETE`）で、`findings` に残りの指摘がある。
`PLAN_READY` は内容の充足であり、制作・購入・設営の実施や承認ではない。

`plan_actionability` はプレースホルダを method の全項目で拒否する（`PLACEHOLDER_VALUE`）。物理作業を含む
研究側の豊富な handoff では、method が各物理作業に材料と道具を与え、制作環境に場所があるときだけ、研究から
制作への対応（`production-inspiration-alignment/v1`）の道具・材料・場所が結ばれる。method が無い計画は
従来どおり blocking gap になる。

fake の資格確認は `tests/test_plan_elements.py`（研究側の fake で段階 B → C を回して export した合成 bundle
`tests/fixtures/handoff/element-route` から、受理 → 段階 D → `PLAN_READY` まで）。fake の合格は実 run の
確認の代わりにならない。
