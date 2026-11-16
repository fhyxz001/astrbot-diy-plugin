from __future__ import annotations

import unittest

import httpx

from core.errors import CookieInvalidError, TorrentFileError
from core.nexus import NexusClient

BASE_URL = "https://pt.example.com"
COOKIE = "c_secure_uid=1; c_secure_pass=abc"

LIST_HTML = """
<html><body><table>
<tr>
  <td><a href="download.php?id=10&amp;passkey=pk"><img alt="下载"></a></td>
  <td><a href="torrents.php?cat=1"><img alt="电影"></a></td>
  <td><a href="details.php?id=10&amp;hit=1">Alpha.Movie.2024.1080p</a></td>
  <td>1.5<br />GB</td>
</tr>
</table></body></html>
"""

DETAIL_HTML = """
<html><head><title>Alpha.Movie.2024 :: Demo Site</title></head><body>
<table>
<tr>
  <td class="rowhead">下载</td>
  <td class="rowfollow"><a href="download.php?id=10&amp;passkey=pk">下载</a></td>
</tr>
<tr><td class="rowhead">海报</td><td class="rowfollow"><img id="poster" src="/upload/poster10.jpg"></td></tr>
<tr><td class="rowhead">大小</td><td class="rowfollow">1.5 GB</td></tr>
<tr><td class="rowhead">种子</td><td class="rowfollow">8</td></tr>
</table>
<div id="kdescr">Demo description</div>
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

TORRENT_BYTES = b"d8:announce3:url4:infod4:name4:testee"


class NexusClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.client = NexusClient(
            BASE_URL, COOKIE, timeout=5, transport=httpx.MockTransport(self._handler)
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "c_secure_uid=1" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="forbidden")

        path = request.url.path
        if path == "/login.php":
            return httpx.Response(200, text=LOGIN_HTML)
        if path == "/torrents.php":
            if request.url.params.get("search") == "expired":
                return httpx.Response(
                    302,
                    headers={"Location": f"{BASE_URL}/login.php"},
                )
            return httpx.Response(200, text=LIST_HTML)
        if path == "/details.php":
            if request.url.params.get("id") == "999":
                return httpx.Response(200, text=LOGIN_HTML)
            return httpx.Response(200, text=DETAIL_HTML)
        if path == "/download.php":
            return httpx.Response(200, content=TORRENT_BYTES)
        return httpx.Response(404, text="not found")

    async def test_latest_sends_cookie_and_parses(self) -> None:
        items = await self.client.latest()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "10")
        self.assertEqual(items[0].title, "Alpha.Movie.2024.1080p")
        self.assertEqual(items[0].size, "1.5 GB")

        request = self.requests[-1]
        self.assertEqual(request.url.path, "/torrents.php")
        self.assertEqual(request.url.params.get("page"), "0")
        self.assertEqual(
            request.headers.get("cookie"),
            "c_secure_uid=1; c_secure_pass=abc",
        )

    async def test_latest_page_mapping(self) -> None:
        await self.client.latest(page=3)
        self.assertEqual(self.requests[-1].url.params.get("page"), "2")

    async def test_search_params(self) -> None:
        items = await self.client.search("沙丘")
        self.assertEqual(len(items), 1)

        request = self.requests[-1]
        self.assertEqual(request.url.params.get("search"), "沙丘")
        self.assertEqual(request.url.params.get("search_area"), "0")
        self.assertEqual(request.url.params.get("search_mode"), "0")
        self.assertEqual(request.url.params.get("page"), "0")

    async def test_fill_covers(self) -> None:
        items = await self.client.latest()
        self.assertEqual(items[0].cover, "")

        await self.client.fill_covers(items)
        self.assertEqual(
            items[0].cover,
            "https://pt.example.com/upload/poster10.jpg",
        )

    async def test_detail(self) -> None:
        detail = await self.client.detail("10")
        self.assertEqual(detail.title, "Alpha.Movie.2024")
        self.assertEqual(detail.size, "1.5 GB")
        self.assertEqual(detail.seeders, "8")
        self.assertEqual(detail.description, "Demo description")
        self.assertEqual(
            detail.download_url,
            "https://pt.example.com/download.php?id=10&passkey=pk",
        )

    async def test_fetch_torrent(self) -> None:
        detail = await self.client.detail("10")
        data = await self.client.fetch_torrent(
            detail.download_url, referer=detail.page_url
        )
        self.assertEqual(data, TORRENT_BYTES)

    async def test_redirect_to_login_raises(self) -> None:
        with self.assertRaises(CookieInvalidError):
            await self.client.search("expired")

    async def test_login_page_raises(self) -> None:
        with self.assertRaises(CookieInvalidError):
            await self.client.detail("999")

    async def test_forbidden_raises(self) -> None:
        client = NexusClient(
            BASE_URL,
            "wrong=1",
            timeout=5,
            transport=httpx.MockTransport(self._handler),
        )
        try:
            with self.assertRaises(CookieInvalidError):
                await client.latest()
        finally:
            await client.close()

    async def test_invalid_torrent_content(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"<html>error</html>"
            )
        )
        client = NexusClient(BASE_URL, COOKIE, timeout=5, transport=transport)
        try:
            with self.assertRaises(TorrentFileError):
                await client.fetch_torrent("download.php?id=1")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
