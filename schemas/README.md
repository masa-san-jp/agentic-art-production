# Schemas

このdirectoryはProductionのcanonical schemaを置く。共通定義は`common.schema.json`を参照し、外部repo所有のhandoff/result schemaはcommit固定snapshotとして`external/`へ追加する。

schemaを置き換える場合は、schema registry、migration、fixture、validator、versionを同じ変更で更新する。

`production-result.schema.json`はProductionが所有する`production-result` v1の正本である。research側のconsumerは、Productionのclean commitからこのfileをimmutable snapshotとして取得し、source commit、取得日時、raw SHA-256を記録してからfeedback importを有効化する。schema単体の追加は、実際のresult生成・export・相互fixture検証の完了を意味しない。
