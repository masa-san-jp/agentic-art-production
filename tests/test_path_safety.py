from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.path_safety import external_path


class PathSafetyTests(unittest.TestCase):
    def test_os_temporary_alias_is_canonicalized(self):
        with tempfile.TemporaryDirectory(prefix="production-path-") as temporary:
            path = Path(temporary)
            self.assertEqual(path.resolve(), external_path(path))

    def test_caller_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="production-path-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "STORE_PATH_INVALID"):
                external_path(link)


if __name__ == "__main__":
    unittest.main()
