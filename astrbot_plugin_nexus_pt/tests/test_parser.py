from __future__ import annotations

import unittest

from core.parser import (
    extract_size,
    find_cover_url,
    find_download_url,
    is_login_page,
    parse_torrent_detail,
    parse_torrent_list,
    strip_tags,
)

BASE_URL = "https://pt.example.com"

LIST_HTML = """
<html><body>
<table class="torrents">
<tr class="colhead"><td>标题</td><td>大小</td></tr>
<tr>
  <td class="rowfollow">
    <a href="download.php?id=1001&amp;passkey=abc"><img src="dl.gif" alt="下载"></a>
  </td>
  <td class="rowfollow">
    <a href="torrents.php?cat=401"><img src="cat.gif" alt="电影"></a>
  </td>
  <td class="rowfollow">
    <img class="poster" src="/upload/poster1.jpg">
  </td>
  <td class="rowfollow">
    <a href="details.php?id=1001&amp;hit=1"><b>Some.Movie.2024.1080p.BluRay</b></a>
    <br><span class="small">Movie / 2024</span>
  </td>
  <td class="rowfollow">2.15<br />GB</td>
  <td class="rowfollow"><b>12</b></td>
</tr>
<tr>
  <td class="rowfollow"><a href="download.php?id=1002&amp;passkey=abc"><img src="dl.gif" alt="下载"></a></td>
  <td class="rowfollow"><a href="torrents.php?cat=402"><img src="cat.gif" alt="剧集"></a></td>
  <td class="rowfollow">
    <img class="lazyload" data-src="https://img.example.com/poster2.webp" src="data:image/gif;base64,xxx">
  </td>
  <td class="rowfollow"><a href="details.php?id=1002&amp;hit=1">Some.Show.S01.2160p.WEB-DL</a></td>
  <td class="rowfollow">850<br />MB</td>
</tr>
</table>
</body></html>
"""

DETAIL_HTML = """
<html><head><title>Some.Movie.2024 :: PT Site</title></head><body>
<h1>Some.Movie.2024</h1>
<table>
<tr>
  <td class="rowhead nowrap">下载</td>
  <td class="rowfollow"><a href="download.php?id=1001&amp;passkey=abc"><img src="dl.gif" alt="下载"></a></td>
</tr>
<tr><td class="rowhead nowrap">副标题</td><td class="rowfollow">A Movie</td></tr>
<tr><td class="rowhead nowrap">海报</td><td class="rowfollow"><img id="poster" src="/upload/poster1001.jpg"></td></tr>
<tr><td class="rowhead nowrap">类型</td><td class="rowfollow"><a href="torrents.php?cat=401">电影</a></td></tr>
<tr><td class="rowhead nowrap">大小</td><td class="rowfollow">2.15 GB</td></tr>
<tr><td class="rowhead nowrap">添加时间</td><td class="rowfollow">2026-11-01 12:30:00</td></tr>
<tr><td class="rowhead nowrap">种子</td><td class="rowfollow">12</td></tr>
<tr><td class="rowhead nowrap">下载</td><td class="rowfollow">3</td></tr>
<tr><td class="rowhead nowrap">完成</td><td class="rowfollow">5</td></tr>
</table>
<div id="kdescr">这是<b>简介</b>内容 &amp; 更多说明。</div>
</body></html>
"""

LOGIN_HTML = """
<html><body>
<form action="takelogin.php" method="post">
  <input type="text" name="username">
  <input type="password" name="password">
</form>
</body></html>
"""


class ParserTest(unittest.TestCase):
    def test_strip_tags(self) -> None:
        self.assertEqual(
            strip_tags("<b>标题</b>&nbsp;&amp; 内容"), "标题 & 内容"
        )

    def test_extract_size(self) -> None:
        self.assertEqual(extract_size("2.15 GB"), "2.15 GB")
        self.assertEqual(extract_size("850 MB"), "850 MB")
        self.assertEqual(extract_size("大小：1.2TB"), "1.2 TB")
        self.assertEqual(extract_size("没有大小"), "")

    def test_parse_torrent_list(self) -> None:
        items = parse_torrent_list(LIST_HTML, BASE_URL)
        self.assertEqual(len(items), 2)

        first = items[0]
        self.assertEqual(first.id, "1001")
        self.assertEqual(first.title, "Some.Movie.2024.1080p.BluRay")
        self.assertEqual(first.size, "2.15 GB")
        self.assertEqual(first.category, "电影")
        self.assertEqual(
            first.cover, "https://pt.example.com/upload/poster1.jpg"
        )
        self.assertEqual(
            first.page_url, "https://pt.example.com/details.php?id=1001"
        )

        second = items[1]
        self.assertEqual(second.id, "1002")
        self.assertEqual(second.title, "Some.Show.S01.2160p.WEB-DL")
        self.assertEqual(second.size, "850 MB")
        self.assertEqual(second.category, "剧集")
        self.assertEqual(
            second.cover, "https://img.example.com/poster2.webp"
        )

    def test_parse_torrent_list_dedup(self) -> None:
        html = LIST_HTML.replace(
            "</table>",
            '<tr><td><a href="details.php?id=1001">重复</a></td></tr></table>',
        )
        items = parse_torrent_list(html, BASE_URL)
        self.assertEqual([item.id for item in items], ["1001", "1002"])

    def test_parse_torrent_list_without_table(self) -> None:
        html = '<a href="details.php?id=7&#38;hit=1">Single Torrent</a>'
        items = parse_torrent_list(html, BASE_URL)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "7")
        self.assertEqual(items[0].title, "Single Torrent")
        self.assertEqual(items[0].size, "")

    def test_parse_torrent_detail(self) -> None:
        detail = parse_torrent_detail(DETAIL_HTML, BASE_URL, "1001")
        self.assertEqual(detail.id, "1001")
        self.assertEqual(detail.title, "Some.Movie.2024")
        self.assertEqual(detail.subtitle, "A Movie")
        self.assertEqual(detail.category, "电影")
        self.assertEqual(detail.size, "2.15 GB")
        self.assertEqual(detail.added, "2026-11-01 12:30:00")
        self.assertEqual(detail.seeders, "12")
        self.assertEqual(detail.leechers, "3")
        self.assertEqual(detail.snatched, "5")
        self.assertEqual(
            detail.cover, "https://pt.example.com/upload/poster1001.jpg"
        )
        self.assertIn("简介", detail.description)
        self.assertIn("更多说明", detail.description)
        self.assertEqual(
            detail.download_url,
            "https://pt.example.com/download.php?id=1001&passkey=abc",
        )
        self.assertEqual(
            detail.page_url, "https://pt.example.com/details.php?id=1001"
        )

    def test_find_cover_url_og_image(self) -> None:
        html = (
            '<meta property="og:image" content="https://img.example.com/og.jpg">'
            '<img src="https://img.example.com/other.jpg">'
        )
        self.assertEqual(
            find_cover_url(html, BASE_URL),
            "https://img.example.com/og.jpg",
        )

    def test_find_cover_url_skips_icons(self) -> None:
        html = (
            '<img src="pic/download.gif">'
            '<img src="/pic/logo.png">'
            '<img id="poster" src="/upload/poster.jpg">'
        )
        self.assertEqual(
            find_cover_url(html, BASE_URL),
            "https://pt.example.com/upload/poster.jpg",
        )

    def test_find_download_url_fallback(self) -> None:
        self.assertEqual(
            find_download_url("<html></html>", BASE_URL, "99"),
            "https://pt.example.com/download.php?id=99",
        )

    def test_is_login_page(self) -> None:
        self.assertTrue(is_login_page(LOGIN_HTML))
        self.assertFalse(is_login_page(LIST_HTML))
        self.assertFalse(is_login_page(""))

    def test_description_truncated(self) -> None:
        html = f'<div id="kdescr">{"长" * 400}</div>'
        detail = parse_torrent_detail(html, BASE_URL, "1")
        self.assertLessEqual(len(detail.description), 301)
        self.assertTrue(detail.description.endswith("…"))


if __name__ == "__main__":
    unittest.main()
