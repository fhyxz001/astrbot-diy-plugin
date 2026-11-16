from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from core.storage import ImageStore

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


class ImageStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ImageStore(self.root, retention_hours=24)

    async def test_save_png(self) -> None:
        path = await self.store.save(PNG_BYTES, "image/png")
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        self.assertEqual(path.parent.parent, self.root)

    async def test_cleanup_removes_expired_files(self) -> None:
        old_day = self.root / "20200101"
        old_day.mkdir()
        old_file = old_day / "old.png"
        old_file.write_bytes(PNG_BYTES)
        old_time = time.time() - 48 * 3600
        os.utime(old_file, (old_time, old_time))

        recent_day = self.root / time.strftime("%Y%m%d")
        recent_day.mkdir()
        recent_file = recent_day / "recent.png"
        recent_file.write_bytes(PNG_BYTES)

        root_old = self.root / "root-old.png"
        root_old.write_bytes(PNG_BYTES)
        os.utime(root_old, (old_time, old_time))

        await self.store.maybe_cleanup(force=True)

        self.assertFalse(old_file.exists())
        self.assertFalse(old_day.exists())
        self.assertFalse(root_old.exists())
        self.assertTrue(recent_file.exists())


if __name__ == "__main__":
    unittest.main()
