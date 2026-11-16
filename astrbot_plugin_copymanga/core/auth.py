"""自动注册与账号池。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .api import CopyMangaClient
from .errors import (
    AllAccountsLimitedError,
    ApiError,
    RiskControlError,
    TokenExpiredError,
)

logger = logging.getLogger(__name__)

TOKEN_CHECK_INTERVAL = 24 * 60 * 60
ACCOUNT_LIMIT_SECONDS = 60

_FIRST_NAMES = [
    "Alex",
    "Avery",
    "Blake",
    "Casey",
    "Drew",
    "Elliot",
    "Emery",
    "Finley",
    "Harper",
    "Hayden",
    "Jordan",
    "Kai",
    "Logan",
    "Mason",
    "Parker",
    "Quinn",
    "Riley",
    "Rowan",
    "Sawyer",
    "Taylor",
]
_LAST_NAMES = [
    "Adams",
    "Baker",
    "Carter",
    "Davis",
    "Evans",
    "Foster",
    "Green",
    "Harris",
    "Irwin",
    "Jones",
    "Kelly",
    "Lewis",
    "Miller",
    "Nelson",
    "Owens",
    "Price",
    "Reed",
    "Smith",
    "Turner",
    "Walker",
]


@dataclass
class Account:
    username: str
    password: str
    token: str = ""
    limited_at: float = 0.0
    last_check_token_at: float = 0.0

    def is_limited(self) -> bool:
        return (
            self.limited_at > 0
            and time.time() - self.limited_at < ACCOUNT_LIMIT_SECONDS
        )

    def should_check_token(self) -> bool:
        return (
            self.last_check_token_at <= 0
            or time.time() - self.last_check_token_at > TOKEN_CHECK_INTERVAL
        )


class AccountPool:
    """持久化的拷贝漫画账号池。"""

    def __init__(
        self,
        client: CopyMangaClient,
        data_dir: Path,
        max_accounts: int = 5,
    ) -> None:
        self.client = client
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "accounts.json"
        self.max_accounts = max(1, int(max_accounts))
        self.accounts: list[Account] = self._load()
        self._lock = asyncio.Lock()

    async def acquire(self) -> Account:
        """获取一个可用账号，必要时自动注册新账号。"""
        async with self._lock:
            for account in list(self.accounts):
                if account.is_limited():
                    continue

                try:
                    if not account.token:
                        await self._login(account)
                    elif account.should_check_token():
                        try:
                            await self.client.get_user_profile(account.token)
                        except TokenExpiredError:
                            await self._login(account)
                        else:
                            account.last_check_token_at = time.time()
                            self._save()
                    return account
                except RiskControlError:
                    self.mark_limited(account)
                except TokenExpiredError:
                    self.invalidate_token(account)
                except ApiError as exc:
                    logger.warning("账号 %s 不可用：%s", account.username, exc)

            if len(self.accounts) >= self.max_accounts:
                raise AllAccountsLimitedError(self._retry_after())

            return await self._register_new()

    async def _login(self, account: Account) -> None:
        token = await self.client.login(account.username, account.password)
        account.token = token
        account.last_check_token_at = time.time()
        self._save()

    async def _register_new(self) -> Account:
        last_error: Exception | None = None
        for _ in range(3):
            username = self._random_username()
            password = secrets.token_hex(10)
            try:
                await self.client.register(username, password)
                token = await self.client.login(username, password)
            except RiskControlError as exc:
                raise AllAccountsLimitedError(ACCOUNT_LIMIT_SECONDS) from exc
            except ApiError as exc:
                # 用户名冲突或网络错误时换一个用户名重试
                last_error = exc
                continue

            account = Account(
                username=username,
                password=password,
                token=token,
                last_check_token_at=time.time(),
            )
            self.accounts.append(account)
            self._save()
            logger.info("已自动注册拷贝漫画账号：%s", username)
            return account

        raise ApiError(f"自动注册账号失败：{last_error}") from last_error

    def mark_limited(self, account: Account) -> None:
        account.limited_at = time.time()
        self._save()

    def invalidate_token(self, account: Account) -> None:
        account.token = ""
        account.last_check_token_at = 0.0
        self._save()

    def _retry_after(self) -> int:
        limited = [account for account in self.accounts if account.is_limited()]
        if not limited:
            return ACCOUNT_LIMIT_SECONDS
        remaining = [
            ACCOUNT_LIMIT_SECONDS - (time.time() - account.limited_at)
            for account in limited
        ]
        return max(1, int(min(remaining)))

    def _random_username(self) -> str:
        first = secrets.choice(_FIRST_NAMES)
        last = secrets.choice(_LAST_NAMES)
        number = secrets.randbelow(100000)
        return f"{first}{last}{number:05d}"

    def _load(self) -> list[Account]:
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("读取账号池失败，将重新开始：%s", exc)
            self._backup_broken_file()
            return []

        if not isinstance(raw, list):
            self._backup_broken_file()
            return []

        accounts: list[Account] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "")
            password = str(item.get("password") or "")
            if not username or not password:
                continue
            accounts.append(
                Account(
                    username=username,
                    password=password,
                    token=str(item.get("token") or ""),
                    limited_at=self._as_float(item.get("limited_at")),
                    last_check_token_at=self._as_float(
                        item.get("last_check_token_at")
                    ),
                )
            )
        return accounts

    @staticmethod
    def _as_float(value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _backup_broken_file(self) -> None:
        try:
            backup = self.path.with_name(
                f"{self.path.name}.broken-{int(time.time())}"
            )
            os.replace(self.path, backup)
            logger.warning("损坏的账号池已备份到 %s", backup)
        except OSError:
            pass

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        payload = json.dumps(
            [asdict(account) for account in self.accounts],
            ensure_ascii=False,
            indent=2,
        )
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, self.path)
