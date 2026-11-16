from __future__ import annotations

import unittest

from core.utils import (
    extract_command_arg,
    extract_torrent_id,
    normalize_base_url,
    normalize_cookie,
    parse_page,
)


class UtilsTest(unittest.TestCase):
    def test_normalize_base_url(self) -> None:
        self.assertEqual(
            normalize_base_url("pt.example.com/"), "https://pt.example.com"
        )
        self.assertEqual(
            normalize_base_url("http://pt.example.com/"),
            "http://pt.example.com",
        )
        self.assertEqual(normalize_base_url(""), "")
        self.assertEqual(
            normalize_base_url("127.0.0.1:8080", "http"),
            "http://127.0.0.1:8080",
        )

    def test_normalize_cookie(self) -> None:
        raw = (
            "Cookie: c_secure_uid=1; c_secure_pass=abc\n"
            "c_secure_login=1"
        )
        self.assertEqual(
            normalize_cookie(raw),
            "c_secure_uid=1; c_secure_pass=abc; c_secure_login=1",
        )
        self.assertEqual(normalize_cookie("  "), "")
        self.assertEqual(normalize_cookie("a=1;; b=2 "), "a=1; b=2")

    def test_extract_command_arg(self) -> None:
        self.assertEqual(
            extract_command_arg("/pt 搜索 沙丘 2024", "搜索"), "沙丘 2024"
        )
        self.assertEqual(extract_command_arg("/pt 首页 2", "首页"), "2")
        self.assertEqual(
            extract_command_arg("pt 搜索 电影", "搜索"), "电影"
        )
        self.assertEqual(extract_command_arg("/pt 搜索", "搜索"), "")
        self.assertEqual(extract_command_arg("/pt 详情", "详情"), "")

    def test_parse_page(self) -> None:
        self.assertEqual(parse_page("2"), 2)
        self.assertEqual(parse_page(""), 1)
        self.assertEqual(parse_page("0"), 1)
        self.assertEqual(parse_page("abc"), 1)
        self.assertEqual(parse_page("-3"), 1)

    def test_extract_torrent_id(self) -> None:
        self.assertEqual(extract_torrent_id("12345"), "12345")
        self.assertEqual(extract_torrent_id("id:321"), "321")
        self.assertEqual(
            extract_torrent_id(
                "https://pt.example.com/details.php?id=67890&hit=1"
            ),
            "67890",
        )
        self.assertEqual(
            extract_torrent_id("details.php?id=42"), "42"
        )
        self.assertEqual(extract_torrent_id("id:abc"), "")
        self.assertEqual(extract_torrent_id("no-id"), "")


if __name__ == "__main__":
    unittest.main()
