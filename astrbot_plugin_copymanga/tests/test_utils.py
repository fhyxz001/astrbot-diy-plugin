from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.utils import (
    build_chapter_dir,
    detect_image_ext,
    extract_command_arg,
    extract_from_url,
    filename_filter,
    format_order,
    looks_like_chapter_selector,
    page_numbers,
    parse_chapter_selector,
)


class UtilsTest(unittest.TestCase):
    def test_filename_filter(self) -> None:
        raw = 'a/b\\c:d*e?f"g<h>i|j'
        expected = "a b c：d⭐e？f'g《h》i丨j"
        self.assertEqual(filename_filter(raw), expected)
        self.assertEqual(filename_filter(".."), "__")
        self.assertEqual(filename_filter(""), "未命名")

    def test_format_order(self) -> None:
        self.assertEqual(format_order(1), "0001")
        self.assertEqual(format_order(1.5), "0001.5")
        self.assertEqual(format_order("12.05"), "0012.05")
        self.assertEqual(format_order("bad"), "0000")

    def test_extract_from_url(self) -> None:
        self.assertEqual(
            extract_from_url(
                "https://www.mangacopy.com/comic/abc_def/chapter/uuid-123"
            ),
            ("abc_def", "uuid-123"),
        )
        self.assertEqual(
            extract_from_url("https://www.mangacopy.com/comic/abc_def/"),
            ("abc_def", None),
        )
        self.assertEqual(extract_from_url("not a url"), ("", None))

    def test_extract_command_arg(self) -> None:
        self.assertEqual(
            extract_command_arg("/cp 搜索 迷宫饭", "搜索"), "迷宫饭"
        )
        self.assertEqual(
            extract_command_arg("cp 搜索 迷宫 饭", "搜索"), "迷宫 饭"
        )
        self.assertEqual(extract_command_arg("搜索 迷宫饭", "搜索"), "迷宫饭")
        self.assertEqual(extract_command_arg("/cp 下载", "下载"), "")

    def test_looks_like_chapter_selector(self) -> None:
        self.assertTrue(looks_like_chapter_selector("latest"))
        self.assertTrue(looks_like_chapter_selector("3"))
        self.assertTrue(looks_like_chapter_selector("3-5"))
        self.assertTrue(looks_like_chapter_selector("1,3,5-7"))
        self.assertFalse(looks_like_chapter_selector("迷宫饭"))

    def test_parse_chapter_selector(self) -> None:
        self.assertEqual(parse_chapter_selector("latest", 5), [5])
        self.assertEqual(parse_chapter_selector("3-5", 5), [3, 4, 5])
        self.assertEqual(parse_chapter_selector("1,3", 5), [1, 3])
        with self.assertRaises(ValueError):
            parse_chapter_selector("6", 5)
        self.assertEqual(parse_chapter_selector("all", 5), [1, 2, 3, 4, 5])
        with self.assertRaises(ValueError):
            parse_chapter_selector("abc", 5)

    def test_page_numbers(self) -> None:
        self.assertEqual(page_numbers([0, 1, 2], 3), [1, 2, 3])
        self.assertEqual(page_numbers([1, 2, 3], 3), [1, 2, 3])
        self.assertEqual(page_numbers([], 3), [1, 2, 3])
        self.assertEqual(page_numbers([0, 0, 0], 3), [1, 2, 3])

    def test_detect_image_ext(self) -> None:
        self.assertEqual(detect_image_ext(b"RIFF0000WEBP", ""), "webp")
        self.assertEqual(detect_image_ext(b"\xff\xd8data", ""), "jpg")
        self.assertEqual(
            detect_image_ext(b"\x89PNG\r\n\x1a\n", "image/png"), "png"
        )
        self.assertEqual(detect_image_ext(b"unknown", ""), "img")

    def test_build_chapter_dir_stays_inside(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = build_chapter_dir(root, "漫画/名字", 1.5, "第1话")
            self.assertEqual(
                path,
                root.resolve()
                / "漫画 名字"
                / "0001.5 第1话",
            )
            escaped = build_chapter_dir(root, "..", 1, "..")
            self.assertTrue(
                escaped == root.resolve()
                or root.resolve() in escaped.parents
            )


if __name__ == "__main__":
    unittest.main()
