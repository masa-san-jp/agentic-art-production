from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lib.provenance import CommitResolutionError, resolve_commit


ROOT = Path(__file__).resolve().parents[1]


class ArchiveProvenanceTests(unittest.TestCase):
    def test_git_checkout_commit_is_preferred(self) -> None:
        self.assertRegex(resolve_commit(ROOT), r"^[0-9a-f]{40}$")

    def test_valid_archive_marker_is_used_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = "a" * 40
            (root / ".archive-commit").write_text(expected + "\n", encoding="utf-8")
            self.assertEqual(expected, resolve_commit(root))

    def test_unexpanded_archive_marker_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".archive-commit").write_text("$Format:%H$\n", encoding="utf-8")
            with self.assertRaisesRegex(CommitResolutionError, r"\.archive-commit must contain"):
                resolve_commit(root)

    def test_missing_commit_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CommitResolutionError, r"could not resolve commit"):
                resolve_commit(Path(temporary))


if __name__ == "__main__":
    unittest.main()
