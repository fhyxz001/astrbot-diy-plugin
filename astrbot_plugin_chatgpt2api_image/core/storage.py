"""生成图片的本地落盘与过期清理。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

from .utils import MAX_IMAGE_BYTES, guess_image_extension

logger = logging.getLogger(__name__)

_DAY_DIR_RE = re.compile(r"^\d{8}$")
_CLEANUP_INTERVAL = 60 * 60


class ImageStore:
    """把图片保存到插件数据目录，并按保留时长清理旧文件。"""

    def __init__(
        self,
        root: Path,
        retention_hours: int = 24,
    ) -> None:
        self.root = Path(root)
        self.retention_hours = max(1, int(retention_hours))
        self._last_cleanup = 0.0
        self._cleanup_lock = asyncio.Lock()

    async def save(self, data: bytes, mime_type: str = "") -> Path:
        if not data:
            raise ValueError("图片数据为空。")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片体积过大。")

        extension = guess_image_extension(data, mime_type)
        day_dir = self.root / time.strftime("%Y%m%d")
        await asyncio.to_thread(day_dir.mkdir, parents=True, exist_ok=True)

        filename = (
            f"{time.strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.{extension}"
        )
        path = day_dir / filename
        await asyncio.to_thread(path.write_bytes, data)
        return path

    async def maybe_cleanup(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_cleanup < _CLEANUP_INTERVAL:
            return

        async with self._cleanup_lock:
            now = time.monotonic()
            if not force and now - self._last_cleanup < _CLEANUP_INTERVAL:
                return
            self._last_cleanup = now
            try:
                await asyncio.to_thread(self._cleanup_sync)
            except OSError as exc:
                logger.warning("清理过期生图文件失败：%s", exc)

    def _cleanup_sync(self) -> None:
        if not self.root.is_dir():
            return

        cutoff = time.time() - self.retention_hours * 3600
        self._remove_old_files(self.root, cutoff)

        for day_dir in self.root.iterdir():
            if (
                not day_dir.is_dir()
                or day_dir.is_symlink()
                or not _DAY_DIR_RE.match(day_dir.name)
            ):
                continue
            self._remove_old_files(day_dir, cutoff)
            try:
                day_dir.rmdir()
            except OSError:
                pass

    @staticmethod
    def _remove_old_files(directory: Path, cutoff: float) -> None:
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for path in entries:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
