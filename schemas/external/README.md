# External schema snapshots

このdirectoryには、researchまたはproductionの別repoが所有するschemaを、source repository、40桁commit、取得日時、raw SHA-256付きでimmutable snapshotとして保存する。

`production-handoff.v1.schema.json`は、researchのclean commit
`aba5f1738cc0066c994433d91c333b3cfe5210da`から取得したraw snapshotである。
bundle本体とREADY handoffが未提供のため、schema登録だけでは`CONTRACT-001`の受理完了とはしない。

Production-ownedの`production-result` schemaは`schemas/production-result.schema.json`に追加済みである。research側のconsumerはclean commitからsnapshotを取得して互換性を検証するまで、feedback importをfail-closedのまま維持する。
