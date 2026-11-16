from __future__ import annotations

import unittest

import httpx

from core.errors import QbittorrentError
from core.qbittorrent import QbittorrentClient

TORRENT_BYTES = b"d8:announce3:url4:infod4:name4:testee"


class QbittorrentClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.add_status = 200
        self.add_text = "Ok."
        self.client = QbittorrentClient(
            "http://127.0.0.1:8080",
            "secret-key",
            timeout=5,
            transport=httpx.MockTransport(self._handler),
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/v2/torrents/add":
            return httpx.Response(self.add_status, text=self.add_text)
        return httpx.Response(404)

    async def test_add_with_api_key(self) -> None:
        await self.client.add_torrent(
            "10.torrent",
            TORRENT_BYTES,
            savepath="/downloads",
            category="movies",
            paused=False,
        )

        self.assertEqual(len(self.requests), 1)
        add = self.requests[0]

        self.assertEqual(add.url.path, "/api/v2/torrents/add")
        self.assertEqual(
            add.headers.get("authorization"), "Bearer secret-key"
        )
        content_type = add.headers.get("content-type", "")
        self.assertTrue(content_type.startswith("multipart/form-data"))

        body = add.content
        self.assertIn(b'name="savepath"', body)
        self.assertIn(b"/downloads", body)
        self.assertIn(b'name="autoTMM"', body)
        self.assertIn(b'name="category"', body)
        self.assertIn(b"movies", body)
        self.assertIn(b'name="paused"\r\n\r\nfalse', body)
        self.assertIn(b'filename="10.torrent"', body)
        self.assertIn(TORRENT_BYTES, body)

    async def test_paused_add(self) -> None:
        await self.client.add_torrent(
            "10.torrent", TORRENT_BYTES, paused=True
        )
        body = self.requests[-1].content
        self.assertIn(b'name="paused"\r\n\r\ntrue', body)
        self.assertNotIn(b'name="savepath"', body)

    async def test_auth_failure(self) -> None:
        self.add_status = 401
        with self.assertRaises(QbittorrentError) as ctx:
            await self.client.add_torrent("10.torrent", TORRENT_BYTES)
        self.assertIn("API Key", str(ctx.exception))

    async def test_add_json_success(self) -> None:
        self.add_text = (
            '{"added_torrent_ids":["8c4f7e3900becc3f70e437e3ac702ccaef42ff21"],'
            '"failure_count":0,"pending_count":0,"success_count":1}'
        )
        await self.client.add_torrent("10.torrent", TORRENT_BYTES)
        self.assertEqual(len(self.requests), 1)

    async def test_add_json_failure(self) -> None:
        self.add_text = (
            '{"added_torrent_ids":[],"failure_count":1,'
            '"pending_count":0,"success_count":0}'
        )
        with self.assertRaises(QbittorrentError):
            await self.client.add_torrent("10.torrent", TORRENT_BYTES)

    async def test_add_failure(self) -> None:
        self.add_text = "Fails."
        with self.assertRaises(QbittorrentError):
            await self.client.add_torrent("10.torrent", TORRENT_BYTES)

    async def test_not_configured(self) -> None:
        client = QbittorrentClient(
            "", "", timeout=5, transport=httpx.MockTransport(self._handler)
        )
        try:
            with self.assertRaises(QbittorrentError):
                await client.add_torrent("10.torrent", TORRENT_BYTES)
        finally:
            await client.close()

    async def test_default_scheme_is_http(self) -> None:
        client = QbittorrentClient("127.0.0.1:8080", "secret-key")
        try:
            self.assertEqual(
                client.base_url, "http://127.0.0.1:8080"
            )
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
