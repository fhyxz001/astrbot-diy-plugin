from __future__ import annotations

import unittest

from core.utils import (
    EDIT_COMMANDS,
    build_endpoint,
    extract_prompt,
    guess_image_extension,
    normalize_api_key,
    normalize_base_url,
    sniff_image_mime,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


class UtilsTest(unittest.TestCase):
    def test_normalize_base_url(self) -> None:
        self.assertEqual(
            normalize_base_url("192.168.1.13:4444"),
            "http://192.168.1.13:4444",
        )
        self.assertEqual(
            normalize_base_url("http://192.168.1.13:4444/"),
            "http://192.168.1.13:4444",
        )
        self.assertEqual(
            normalize_base_url("https://example.com/base/?x=1#top"),
            "https://example.com/base",
        )
        self.assertEqual(normalize_base_url(""), "")
        self.assertEqual(normalize_base_url("ftp://example.com"), "")

    def test_build_endpoint(self) -> None:
        self.assertEqual(
            build_endpoint("192.168.1.13:4444"),
            "http://192.168.1.13:4444/v1/images/generations",
        )
        self.assertEqual(
            build_endpoint("http://host:4444/v1/"),
            "http://host:4444/v1/images/generations",
        )
        self.assertEqual(
            build_endpoint("http://host:4444/v1/images/generations"),
            "http://host:4444/v1/images/generations",
        )
        self.assertEqual(build_endpoint(""), "")

    def test_build_edit_endpoint(self) -> None:
        self.assertEqual(
            build_endpoint("192.168.1.13:4444", "edits"),
            "http://192.168.1.13:4444/v1/images/edits",
        )
        self.assertEqual(
            build_endpoint("http://host:4444/v1/", "edits"),
            "http://host:4444/v1/images/edits",
        )
        self.assertEqual(
            build_endpoint("http://host:4444/v1/images/generations", "edits"),
            "http://host:4444/v1/images/edits",
        )
        self.assertEqual(
            build_endpoint("http://host:4444/v1/images/edits"),
            "http://host:4444/v1/images/generations",
        )
        self.assertEqual(build_endpoint("", "edits"), "")

    def test_extract_prompt(self) -> None:
        cases = {
            "/st 一只猫": "一只猫",
            "st 一只猫": "一只猫",
            "  /ST   一只猫  ": "一只猫",
            "/st": "",
            "一只猫": "一只猫",
            "/生图 海报": "海报",
            "/绘图 赛博朋克": "赛博朋克",
            '/st "一只戴帽子的猫"': "一只戴帽子的猫",
            "/st “中文引号”": "中文引号",
            "/st 第一行\n第二行": "第一行\n第二行",
            "/st\u3000全角空格": "全角空格",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(extract_prompt(raw), expected)

    def test_extract_edit_prompt(self) -> None:
        cases = {
            "/改图 改成赛博朋克夜景": "改成赛博朋克夜景",
            "改图 换成水彩风格": "换成水彩风格",
            "/修图  去掉背景": "去掉背景",
            "/图片编辑 加一顶帽子": "加一顶帽子",
            "/edit make it cyberpunk": "make it cyberpunk",
            "/改图": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    extract_prompt(raw, EDIT_COMMANDS), expected
                )

    def test_image_sniffing(self) -> None:
        self.assertEqual(sniff_image_mime(PNG_BYTES), "image/png")
        self.assertEqual(guess_image_extension(PNG_BYTES), "png")
        self.assertEqual(
            guess_image_extension(b"not an image", "image/jpeg"), "jpg"
        )
        self.assertEqual(
            guess_image_extension(b"not an image", "", "https://x/a.webp?x=1"),
            "webp",
        )
        self.assertEqual(guess_image_extension(b"not an image"), "png")

    def test_normalize_api_key(self) -> None:
        self.assertEqual(normalize_api_key(" abc "), "abc")
        self.assertEqual(normalize_api_key("Bearer abc"), "abc")
        self.assertEqual(normalize_api_key("bearer abc"), "abc")
        self.assertEqual(normalize_api_key(None), "")


if __name__ == "__main__":
    unittest.main()
