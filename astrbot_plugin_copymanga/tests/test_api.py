from __future__ import annotations

import unittest

import httpx

from core.api import CopyMangaClient
from core.errors import RiskControlError


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/v3/search/comic":
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "results": {
                            "list": [
                                {
                                    "name": "漫画A",
                                    "path_word": "abc",
                                    "author": [{"name": "作者A"}],
                                }
                            ],
                            "total": 1,
                        },
                    },
                )
            if path == "/api/v3/comic2/abc":
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "results": {
                            "comic": {"name": "漫画A"},
                            "groups": {
                                "default": {
                                    "name": "默认",
                                    "path_word": "default",
                                }
                            },
                        },
                    },
                )
            if path == "/api/v3/comic/abc/group/default/chapters":
                offset = int(request.url.params.get("offset", "0"))
                if offset == 0:
                    return httpx.Response(
                        200,
                        json={
                            "code": 200,
                            "results": {
                                "total": 150,
                                "list": [
                                    {
                                        "uuid": "chapter-1",
                                        "name": "第1话",
                                        "ordered": 10,
                                    }
                                ],
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "results": {
                            "total": 150,
                            "list": [
                                {
                                    "uuid": "chapter-2",
                                    "name": "第2话",
                                    "ordered": 20,
                                }
                            ],
                        },
                    },
                )
            if path == "/api/v3/comic/abc/chapter2/uuid-1":
                self.assertEqual(
                    request.headers.get("authorization"), "Token test-token"
                )
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "results": {
                            "chapter": {
                                "name": "第1话",
                                "ordered": 10,
                                "group_path_word": "default",
                                "contents": [
                                    {
                                        "url": "https://img.test/1.c800x.webp"
                                    }
                                ],
                                "words": [0],
                            }
                        },
                    },
                )
            if path == "/api/v3/comic/abc/chapter2/risk":
                return httpx.Response(210, json={"code": 210})
            if path == "/api/v3/register":
                return httpx.Response(
                    200, json={"code": 200, "results": None}
                )
            if path == "/api/v3/login":
                return httpx.Response(
                    200,
                    json={"code": 200, "results": {"token": "login-token"}},
                )
            if path == "/api/v3/member/info":
                return httpx.Response(
                    200, json={"code": 200, "results": {"name": "test"}}
                )
            if request.url.host == "img.test":
                return httpx.Response(
                    200,
                    content=b"RIFF0000WEBP",
                    headers={"content-type": "image/webp"},
                )
            return httpx.Response(404, text="not found")

        self.client = CopyMangaClient(
            "test.local", transport=httpx.MockTransport(handler)
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_search(self) -> None:
        data = await self.client.search("漫画")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["list"][0]["path_word"], "abc")

    async def test_get_comic(self) -> None:
        data = await self.client.get_comic("abc")
        self.assertEqual(data["comic"]["name"], "漫画A")
        self.assertIn("default", data["groups"])

    async def test_get_group_chapters_pagination(self) -> None:
        chapters = await self.client.get_group_chapters("abc", "default")
        self.assertEqual(
            [chapter["uuid"] for chapter in chapters],
            ["chapter-1", "chapter-2"],
        )

    async def test_get_chapter(self) -> None:
        data = await self.client.get_chapter(
            "abc", "uuid-1", "test-token"
        )
        self.assertEqual(data["chapter"]["name"], "第1话")

    async def test_risk_control(self) -> None:
        with self.assertRaises(RiskControlError):
            await self.client.get_chapter("abc", "risk", "test-token")

    async def test_register_login_profile(self) -> None:
        await self.client.register("user", "password")
        token = await self.client.login("user", "password")
        self.assertEqual(token, "login-token")
        profile = await self.client.get_user_profile(token)
        self.assertEqual(profile["name"], "test")

    async def test_get_image(self) -> None:
        data, ext = await self.client.get_image(
            "https://img.test/1.c800x.webp"
        )
        self.assertEqual(data, b"RIFF0000WEBP")
        self.assertEqual(ext, "webp")


if __name__ == "__main__":
    unittest.main()
