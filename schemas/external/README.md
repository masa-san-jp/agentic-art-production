# External schema snapshots

このdirectoryには、researchまたはproductionの別repoが所有するschemaを、source repository、40桁commit、取得日時、raw SHA-256付きでimmutable snapshotとして保存する。

`production-handoff.v1.schema.json`は、researchのclean commit
`aba5f1738cc0066c994433d91c333b3cfe5210da`から取得したraw snapshotである。
bundle本体とREADY handoffが未提供のため、schema登録だけでは`CONTRACT-001`の受理完了とはしない。

Production-ownedの`production-result` schemaは`schemas/production-result.schema.json`に追加済みである。research側`agent/handoff-build`のclean commit `9d162b17394fd121ab7f986321b24a152684a9a5`でsnapshot適用、consumer互換性、release gate 3回を確認済みである。Production側のresult builder/exporterとResearch importerのdry-run/apply/冪等再取込も一時clean fixtureで確認済みであり、実`harmony-study`の出力bundleはGit外output rootへ保存する。
