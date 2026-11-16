"""Minimal stubs of astrbot.api.star."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path


class Context:
    def __init__(self):
        self.messages = []

    async def send_message(self, umo: str, chain) -> bool:
        self.messages.append((umo, chain))
        return True


class Star:
    def __init__(self, context: Context, config=None):
        self.context = context
        self.config = config
        self.logger = logging.getLogger("astrbot.stub")


class StarTools:
    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        base = Path(
            os.environ.get("ASTRBOT_STUB_DATA_DIR")
            or (Path(tempfile.gettempdir()) / "astrbot_stub_data")
        )
        path = base / str(plugin_name or "plugin")
        path.mkdir(parents=True, exist_ok=True)
        return path


def register(*args, **kwargs):
    def decorator(cls):
        return cls

    return decorator
