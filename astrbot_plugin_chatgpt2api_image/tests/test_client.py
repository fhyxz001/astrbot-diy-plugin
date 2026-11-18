from __future__ import annotations

import base64
import json
import unittest

import httpx

from core.client import ChatGpt2ApiClient
from core.errors import ApiError, ConfigError

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
BASE_URL = "http://api.local:4444"


class ChatGpt2ApiClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[httpx.Request] = []

    async def test_generate_b64_and_request_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [{"b64_json": PNG_B64, "revised_prompt": "cat"}],
                },
            )

        client = ChatGpt2ApiClient(
            BASE_URL,
            "secret",
            "gpt-image-2.6",
            transport=httpx.MockTransport(handler),
        )
        try:
            images = await client.generate("一只猫", n=2)
        finally:
            await client.close()

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data, PNG_BYTES)
        self.assertEqual(images[0].mime_type, "image/png")

        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/v1/images/generations")
        self.assertEqual(
            request.headers["authorization"], "Bearer secret"
        )
        self.assertEqual(
            json.loads(request.content),
            {"model": "gpt-image-2.6", "prompt": "一只猫", "n": 2},
        )

    async def test_multiple_images_are_returned(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"b64_json": PNG_B64},
                        {"b64_json": PNG_B64},
                    ]
                },
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报", n=2)
        finally:
            await client.close()

        self.assertEqual(len(images), 2)
        self.assertEqual([image.data for image in images], [PNG_BYTES, PNG_BYTES])

    async def test_size_and_quality_are_optional(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})

        client = ChatGpt2ApiClient(
            BASE_URL,
            "",
            size="1024x1024",
            quality="high",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.generate("海报")
        finally:
            await client.close()

        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["size"], "1024x1024")
        self.assertEqual(payload["quality"], "high")

    async def test_url_response_is_downloaded_with_auth(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"data": [{"url": "/files/a.png"}]},
                )
            self.assertEqual(request.url.path, "/files/a.png")
            self.assertEqual(
                request.headers.get("authorization"), "Bearer secret"
            )
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, "secret", transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报")
        finally:
            await client.close()

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data, PNG_BYTES)
        self.assertEqual(len(self.requests), 2)

    async def test_external_url_does_not_leak_api_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"data": [{"url": "https://cdn.example.com/a.png"}]},
                )
            self.assertEqual(request.url.host, "cdn.example.com")
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, "secret", transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报")
        finally:
            await client.close()

        self.assertEqual(len(images), 1)
        self.assertEqual(len(self.requests), 2)

    async def test_relative_url_without_leading_slash(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"data": [{"url": "files/a.png"}]},
                )
            self.assertEqual(request.url.path, "/files/a.png")
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报")
        finally:
            await client.close()

        self.assertEqual(len(images), 1)
        self.assertEqual(len(self.requests), 2)

    async def test_data_uri_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"url": f"data:image/png;base64,{PNG_B64}"}
                    ]
                },
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报")
        finally:
            await client.close()

        self.assertEqual(images[0].data, PNG_BYTES)

    async def test_binary_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.generate("海报")
        finally:
            await client.close()

        self.assertEqual(images[0].data, PNG_BYTES)
        self.assertEqual(images[0].mime_type, "image/png")

    async def test_http_error_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"message": "invalid api key"}},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, "bad", transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ApiError) as context:
                await client.generate("海报")
        finally:
            await client.close()

        self.assertIn("鉴权失败", str(context.exception))
        self.assertIn("invalid api key", str(context.exception))

    async def test_timeout_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = ChatGpt2ApiClient(
            BASE_URL, timeout=30, transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ApiError) as context:
                await client.generate("海报")
        finally:
            await client.close()

        self.assertIn("超时", str(context.exception))

    async def test_no_image_in_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"revised_prompt": "a poster"}]},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ApiError) as context:
                await client.generate("海报")
        finally:
            await client.close()

        self.assertIn("没有返回可用图片", str(context.exception))

    async def test_edit_builds_multipart_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})

        client = ChatGpt2ApiClient(
            BASE_URL, "secret", transport=httpx.MockTransport(handler)
        )
        try:
            images = await client.edit("改成赛博朋克夜景", PNG_BYTES)
        finally:
            await client.close()

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data, PNG_BYTES)

        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/v1/images/edits")
        self.assertEqual(
            request.headers["authorization"], "Bearer secret"
        )
        self.assertTrue(
            request.headers["content-type"].startswith(
                "multipart/form-data; boundary="
            )
        )

        body = request.content
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gpt-image-2.6", body)
        self.assertIn(b'name="prompt"', body)
        self.assertIn("改成赛博朋克夜景".encode(), body)
        self.assertIn(b'name="image"; filename="input.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertIn(PNG_BYTES, body)
        self.assertNotIn(b'name="n"', body)

    async def test_edit_uses_size_quality_and_count(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})

        client = ChatGpt2ApiClient(
            BASE_URL,
            "",
            size="1024x1024",
            quality="high",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.edit("改成水彩风格", b"\xff\xd8\xff\xe0jpeg", n=2)
        finally:
            await client.close()

        body = self.requests[0].content
        self.assertIn(b'name="size"', body)
        self.assertIn(b"1024x1024", body)
        self.assertIn(b'name="quality"', body)
        self.assertIn(b"high", body)
        self.assertIn(b'name="n"', body)
        self.assertIn(b'name="image"; filename="input.jpg"', body)

    async def test_edit_requires_image(self) -> None:
        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(
                lambda request: httpx.Response(200)
            )
        )
        try:
            with self.assertRaises(ConfigError):
                await client.edit("改图", b"")
        finally:
            await client.close()

    async def test_edit_http_error_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                json={"error": {"message": "upstream failed"}},
            )

        client = ChatGpt2ApiClient(
            BASE_URL, transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ApiError) as context:
                await client.edit("改图", PNG_BYTES)
        finally:
            await client.close()

        self.assertIn("图片编辑服务异常", str(context.exception))
        self.assertIn("upstream failed", str(context.exception))

    async def test_not_configured(self) -> None:
        client = ChatGpt2ApiClient("", transport=httpx.MockTransport(
            lambda request: httpx.Response(200)
        ))
        try:
            with self.assertRaises(ConfigError):
                await client.generate("海报")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
