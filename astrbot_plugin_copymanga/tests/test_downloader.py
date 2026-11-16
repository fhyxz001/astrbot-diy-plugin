from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.downloader import ChapterDownloader, extract_pages
from core.errors import ApiError, IncompleteDownloadError
from core.models import ChapterMeta, ComicMeta


class FakeClient:
    def __init__(self, fail_pages: set[int] | None = None) -> None:
        self.fail_pages = fail_pages or set()
        self.calls = 0

    async def get_image(self, url: str) -> tuple[bytes, str]:
        self.calls += 1
        page = int(url.rsplit("/", 1)[-1].split(".")[0])
        if page in self.fail_pages:
            raise ApiError("模拟图片下载失败")
        return b"\x89PNG\r\n\x1a\n" + b"data", "png"


def make_chapter_data() -> dict:
    return {
        "chapter": {
            "name": "第1话",
            "ordered": 10,
            "group_path_word": "default",
            "contents": [
                {"url": "https://img.test/1.c800x.webp"},
                {"url": "https://img.test/2.c800x.webp"},
            ],
            "words": [0, 1],
        }
    }


class DownloaderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.comic = ComicMeta(
            path_word="abc",
            title="漫画A",
            group_names={"default": "默认"},
            default_group="default",
        )
        self.chapter = ChapterMeta(uuid="uuid-1", title="第1话", order=1.0)

    async def test_extract_pages_upgrades_url(self) -> None:
        pages = extract_pages(make_chapter_data()["chapter"])
        self.assertEqual(
            pages,
            [
                (1, "https://img.test/1.c1500x.webp"),
                (2, "https://img.test/2.c1500x.webp"),
            ],
        )

    async def test_download_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = FakeClient()
            downloader = ChapterDownloader(client, None, img_concurrency=2)

            result = await downloader.download(
                self.comic,
                self.chapter,
                make_chapter_data(),
                root,
            )
            self.assertEqual(result.pages, 2)
            self.assertFalse(result.existed)
            self.assertEqual(result.skipped, 0)
            self.assertTrue((result.path / "001.png").exists())
            self.assertTrue((result.path / "002.png").exists())

            calls_after_first = client.calls
            again = await downloader.download(
                self.comic,
                self.chapter,
                make_chapter_data(),
                root,
            )
            self.assertTrue(again.existed)
            self.assertEqual(client.calls, calls_after_first)

            (result.path / "002.png").unlink()
            resumed = await downloader.download(
                self.comic,
                self.chapter,
                make_chapter_data(),
                root,
            )
            self.assertFalse(resumed.existed)
            self.assertEqual(resumed.skipped, 1)
            self.assertTrue((result.path / "002.png").exists())

    async def test_incomplete_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = FakeClient(fail_pages={2})
            downloader = ChapterDownloader(client, None, img_concurrency=2)
            with self.assertRaises(IncompleteDownloadError):
                await downloader.download(
                    self.comic,
                    self.chapter,
                    make_chapter_data(),
                    root,
                )

            chapter_dir = root / "漫画A" / "0001 第1话"
            self.assertTrue((chapter_dir / "001.png").exists())
            self.assertFalse((chapter_dir / "002.png").exists())

    async def test_progress_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            values: list[tuple[int, int]] = []

            async def progress(done: int, total: int) -> None:
                values.append((done, total))

            downloader = ChapterDownloader(FakeClient(), None)
            await downloader.download(
                self.comic,
                self.chapter,
                make_chapter_data(),
                Path(temp),
                progress,
            )
            self.assertEqual(values[-1], (2, 2))


if __name__ == "__main__":
    unittest.main()
