from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core.auth import Account, AccountPool
from core.errors import AllAccountsLimitedError, TokenExpiredError


class FakeClient:
    def __init__(self) -> None:
        self.register_calls = 0
        self.login_calls = 0
        self.profile_calls = 0
        self.expire_profile = False

    async def register(self, username: str, password: str) -> None:
        self.register_calls += 1

    async def login(self, username: str, password: str) -> str:
        self.login_calls += 1
        return f"token-{username}"

    async def get_user_profile(self, token: str) -> dict:
        self.profile_calls += 1
        if self.expire_profile:
            raise TokenExpiredError("expired")
        return {"token": token}


class AccountPoolTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_register_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            client = FakeClient()
            pool = AccountPool(client, data_dir, max_accounts=2)

            account = await pool.acquire()
            self.assertTrue(account.username)
            self.assertTrue(account.token)
            self.assertEqual(client.register_calls, 1)
            self.assertTrue((data_dir / "accounts.json").exists())

            loaded = AccountPool(FakeClient(), data_dir, max_accounts=2)
            self.assertEqual(len(loaded.accounts), 1)
            self.assertEqual(loaded.accounts[0].username, account.username)

    async def test_limited_account_registers_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = FakeClient()
            pool = AccountPool(client, Path(temp), max_accounts=2)
            pool.accounts.append(
                Account(
                    username="old",
                    password="password",
                    token="old-token",
                    limited_at=time.time(),
                    last_check_token_at=time.time(),
                )
            )

            account = await pool.acquire()
            self.assertNotEqual(account.username, "old")
            self.assertEqual(len(pool.accounts), 2)
            self.assertEqual(client.register_calls, 1)

    async def test_all_accounts_limited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = FakeClient()
            pool = AccountPool(client, Path(temp), max_accounts=1)
            pool.accounts.append(
                Account(
                    username="old",
                    password="password",
                    token="old-token",
                    limited_at=time.time(),
                    last_check_token_at=time.time(),
                )
            )

            with self.assertRaises(AllAccountsLimitedError):
                await pool.acquire()
            self.assertEqual(client.register_calls, 0)

    async def test_expired_token_relogin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = FakeClient()
            pool = AccountPool(client, Path(temp), max_accounts=1)
            pool.accounts.append(
                Account(
                    username="user",
                    password="password",
                    token="old-token",
                    last_check_token_at=0,
                )
            )
            client.expire_profile = True

            account = await pool.acquire()
            self.assertEqual(account.token, "token-user")
            self.assertEqual(client.login_calls, 1)
            self.assertEqual(client.profile_calls, 1)


if __name__ == "__main__":
    unittest.main()
