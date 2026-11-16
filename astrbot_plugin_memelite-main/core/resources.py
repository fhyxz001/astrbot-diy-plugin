"""meme 资源（表情底图 / 字体）扫描与下载。

meme_generator 0.2.x（Rust 版）把资源放在 ``~/.meme_generator/resources`` 下，
内置的 ``check_resources`` 没有进度回调，插件面板因此自行读取 resources.json，
按哈希校验本地文件，并只下载缺失或损坏的部分。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

from astrbot import logger
from PIL import Image

from .meme import MEME_GENERATOR_AVAILABLE
from .meme import __version__ as MEME_VERSION

MAX_WORKERS = 8
MAX_ERRORS = 30
MAX_MISSING_IN_STATE = 400
ATTEMPTS_PER_MIRROR = 2
RETRY_DELAY = 1.5

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
THUMB_MAX = 220
THUMB_CACHE_LIMIT = 600
PREVIEW_LIMIT_MAX = 60
PREVIEW_BYTES_MAX = 10 * 1024 * 1024

MIRRORS: tuple[dict[str, str], ...] = (
    {
        "label": "jsDelivr",
        "base": "https://cdn.jsdelivr.net/gh/MemeCrafters/meme-generator-rs@",
    },
    {
        "label": "GitHub Raw",
        "base": "https://raw.githubusercontent.com/MemeCrafters/meme-generator-rs/",
    },
    {
        "label": "ghproxy 加速",
        "base": "https://ghproxy.net/https://raw.githubusercontent.com/MemeCrafters/meme-generator-rs/",
    },
)


class _Cancelled(Exception):
    """任务被用户取消。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _idle_state() -> dict[str, Any]:
    return {
        "running": False,
        "phase": "idle",
        "message": "就绪",
        "total": 0,
        "completed": 0,
        "failed": 0,
        "bytes_done": 0,
        "current": "",
        "mirror": "",
        "missing_total": 0,
        "missing_kinds": {"images": 0, "fonts": 0},
        "scanned": False,
        "missing": [],
        "missing_truncated": False,
        "errors": [],
        "started_at": None,
        "finished_at": None,
    }


class ResourceManager:
    def __init__(self, manager: Any, conf: Any):
        self.manager = manager
        self.conf = conf
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state: dict[str, Any] = _idle_state()
        self._base = ""
        self._thumb_cache: dict[tuple[str, int, int], str] = {}

    # ------------------------------------------------------------------ 信息
    @property
    def flavor(self) -> str:
        return "python" if self.manager.is_py_version else "rust"

    def _configured_mirror(self) -> str:
        value = self.conf.get("resource_mirror", "") if self.conf else ""
        return value.strip() if isinstance(value, str) else ""

    def _resource_dirs(self) -> dict[str, Path]:
        if self.flavor == "python":
            try:
                import meme_generator

                root = Path(meme_generator.__file__).resolve().parent / "memes"
            except Exception:
                root = Path(".")
            return {"root": root, "images": root, "fonts": root}

        home = os.environ.get("MEME_HOME", "").strip()
        root = Path(home).expanduser() if home else Path.home() / ".meme_generator"
        resources = root / "resources"
        return {"root": root, "images": resources / "images", "fonts": resources / "fonts"}

    def overview(self) -> dict[str, Any]:
        dirs = self._resource_dirs()
        stats: dict[str, dict[str, Any]] = {}
        for kind in ("images", "fonts"):
            directory = dirs[kind]
            files = 0
            size = 0
            try:
                for path in directory.rglob("*"):
                    if path.is_file():
                        files += 1
                        size += path.stat().st_size
            except OSError:
                pass
            stats[kind] = {"dir": str(directory), "files": files, "size": size}

        return {
            "flavor": self.flavor,
            "version": MEME_VERSION,
            "available": MEME_GENERATOR_AVAILABLE,
            "resource_root": str(dirs["root"]),
            "stats": stats,
            "mirror": self._configured_mirror(),
            "mirrors": list(MIRRORS),
            "check_on_start": bool(self.conf.get("is_check_resources", True)),
            "task": self.snapshot(),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["errors"] = list(self._state["errors"])
            state["missing"] = list(self._state["missing"])
        return state

    # ----------------------------------------------------------------- 预览
    def _image_files(self, keyword: str = "") -> list[Path]:
        """按相对路径排序返回本地全部表情底图。"""
        root = self._resource_dirs()["images"]
        if not root.is_dir():
            return []
        lowered = keyword.strip().lower()
        files: list[Path] = []
        try:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                    continue
                if lowered and lowered not in path.relative_to(root).as_posix().lower():
                    continue
                files.append(path)
        except OSError:
            return []
        files.sort(key=lambda item: item.relative_to(root).as_posix())
        return files

    def list_images(
        self, offset: int = 0, limit: int = 24, keyword: str = ""
    ) -> dict[str, Any]:
        """分页列出底图，并附带缩略图的 data URL。"""
        root = self._resource_dirs()["images"]
        files = self._image_files(keyword)
        total = len(files)
        limit = max(1, min(PREVIEW_LIMIT_MAX, limit))
        offset = max(0, min(offset, max(0, total - 1)))
        page = files[offset : offset + limit]

        items: list[dict[str, Any]] = []
        for path in page:
            try:
                stat = path.stat()
                thumb = self._thumb(path, stat.st_mtime_ns, stat.st_size)
            except Exception:
                # 损坏或无法解码的文件跳过，不影响其余预览
                continue
            items.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "size": stat.st_size,
                    "thumb": thumb,
                }
            )

        return {"total": total, "offset": offset, "limit": limit, "items": items}

    def image_detail(self, name: str) -> dict[str, Any]:
        """返回指定底图的原始内容（data URL），用于点击查看大图。"""
        root = self._resource_dirs()["images"]
        path = self._target(root, name)
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            raise FileNotFoundError(f"底图不存在: {name}")
        size = path.stat().st_size
        if size > PREVIEW_BYTES_MAX:
            raise ValueError("图片过大，无法在页面中预览")
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }[path.suffix.lower()]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"file": name, "size": size, "data": f"data:{mime};base64,{encoded}"}

    def _thumb(self, path: Path, mtime_ns: int, size: int) -> str:
        key = (str(path), mtime_ns, size)
        with self._lock:
            cached = self._thumb_cache.get(key)
        if cached:
            return cached

        with Image.open(path) as raw:
            raw.seek(0)
            has_alpha = raw.mode in ("RGBA", "LA") or (
                raw.mode == "P" and "transparency" in raw.info
            )
            image = raw.convert("RGBA" if has_alpha else "RGB")
            image.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            if has_alpha:
                image.save(buffer, format="PNG", optimize=True)
                mime = "image/png"
            else:
                image.save(buffer, format="JPEG", quality=82)
                mime = "image/jpeg"

        data = f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
        with self._lock:
            if len(self._thumb_cache) >= THUMB_CACHE_LIMIT:
                self._thumb_cache.clear()
            self._thumb_cache[key] = data
        return data

    # ------------------------------------------------------------------ 任务
    def start(self, scan_only: bool, mirror: str | None = None) -> tuple[bool, str]:
        with self._lock:
            if self._state["running"]:
                return False, "已有资源任务正在运行"
            self._state = _idle_state()
            self._state["running"] = True
            self._state["phase"] = "manifest"
            self._state["message"] = "准备中…"
            self._state["started_at"] = time.time()
        self._cancel.clear()
        thread = threading.Thread(
            target=self._run,
            args=(bool(scan_only), mirror),
            name="memelite-resource",
            daemon=True,
        )
        thread.start()
        return True, ""

    def cancel(self) -> bool:
        with self._lock:
            running = bool(self._state["running"])
        self._cancel.set()
        return running

    def _patch(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)

    def _failed_count(self) -> int:
        with self._lock:
            return int(self._state["failed"])

    def _add_bytes(self, size: int) -> None:
        with self._lock:
            self._state["bytes_done"] += size

    def _record_error(self, item: dict[str, str], reason: str) -> None:
        with self._lock:
            self._state["failed"] += 1
            if len(self._state["errors"]) < MAX_ERRORS:
                self._state["errors"].append(
                    {"kind": item["kind"], "file": item["file"], "reason": reason}
                )

    # --------------------------------------------------------------- 执行体
    def _run(self, scan_only: bool, mirror: str | None) -> None:
        try:
            if self.flavor == "python":
                self._run_python_builtin()
                return

            entries, base = self._load_manifest(mirror)
            self._base = base
            self._patch(
                phase="scan",
                message="校验本地资源文件…",
                mirror=base,
                total=len(entries),
                completed=0,
            )
            missing = self._scan(entries)
            self._patch(
                missing_total=len(missing),
                scanned=True,
                missing_kinds={
                    "images": sum(1 for item in missing if item["kind"] == "images"),
                    "fonts": sum(1 for item in missing if item["kind"] == "fonts"),
                },
                missing=[
                    {"kind": item["kind"], "file": item["file"]}
                    for item in missing[:MAX_MISSING_IN_STATE]
                ],
                missing_truncated=len(missing) > MAX_MISSING_IN_STATE,
            )

            if scan_only:
                self._patch(
                    phase="done",
                    message=f"缺失 {len(missing)} 个资源文件"
                    if missing
                    else "资源完整，无需下载",
                )
                return

            if not missing:
                self._patch(phase="done", message="资源完整，无需下载")
                return

            self._patch(
                phase="download",
                message=f"开始下载 {len(missing)} 个资源文件…",
                total=len(missing),
                completed=0,
                failed=0,
                bytes_done=0,
            )
            self._download(missing)

            if self._cancel.is_set():
                self._patch(phase="cancelled", message="已停止下载")
                return

            failed = self._failed_count()
            if failed:
                self._patch(
                    phase="done",
                    message=f"下载结束，{failed} 个文件在所有镜像上均失败，请稍后重试",
                )
            else:
                self._patch(
                    phase="done",
                    message="全部资源已就绪",
                    missing=[],
                    missing_total=0,
                    missing_kinds={"images": 0, "fonts": 0},
                    missing_truncated=False,
                )
        except _Cancelled:
            self._patch(phase="cancelled", message="已取消")
        except Exception as exc:
            logger.error(f"资源任务失败: {exc}")
            self._patch(phase="error", message=str(exc))
        finally:
            self._patch(running=False, current="", finished_at=time.time())

    def _run_python_builtin(self) -> None:
        """0.1.x（Python 版）由 meme_generator 自行管理资源，直接调用内置检查。"""
        func = self.manager.check_resources_func
        if not func:
            raise RuntimeError("当前环境未安装 meme_generator")
        self._patch(phase="download", message="调用 meme_generator 内置资源检查…")
        try:
            asyncio.run(func())
        except Exception as exc:
            raise RuntimeError(f"内置资源检查失败: {exc}") from exc
        self.manager._load_memes()
        self._patch(phase="done", message="内置资源检查已完成")

    # ----------------------------------------------------------------- 清单
    @staticmethod
    def _normalize_base(base: str) -> str:
        base = base.strip()
        if base.endswith(("@", "/")):
            return base
        return base + "/"

    def _candidate_bases(self, mirror: str | None) -> list[str]:
        bases: list[str] = []
        raw = (mirror or "").strip() or self._configured_mirror()
        if raw:
            bases.append(self._normalize_base(raw))
        for item in MIRRORS:
            base = self._normalize_base(item["base"])
            if base not in bases:
                bases.append(base)
        return bases

    @staticmethod
    def _fetch(url: str, timeout: int = 30) -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": "astrbot-plugin-memelite"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _load_manifest(self, mirror: str | None) -> tuple[list[dict[str, str]], str]:
        failures: list[str] = []
        for base in self._candidate_bases(mirror):
            if self._cancel.is_set():
                raise _Cancelled
            url = f"{base}v{MEME_VERSION}/resources/resources.json"
            try:
                data = json.loads(self._fetch(url).decode("utf-8"))
            except Exception as exc:
                failures.append(f"{base}（{exc}）")
                continue

            entries: list[dict[str, str]] = []
            for kind in ("fonts", "images"):
                for item in data.get(kind) or []:
                    file_name = item.get("file") if isinstance(item, dict) else None
                    file_hash = item.get("hash") if isinstance(item, dict) else None
                    if isinstance(file_name, str) and isinstance(file_hash, str):
                        entries.append(
                            {
                                "kind": kind,
                                "file": file_name,
                                "hash": file_hash.lower(),
                            }
                        )
            if entries:
                return entries, base
            failures.append(f"{base}（资源清单为空）")

        detail = "；".join(failures[-3:])
        raise RuntimeError(f"获取资源清单失败，请检查网络或更换镜像。{detail}")

    # ----------------------------------------------------------------- 扫描
    @staticmethod
    def _target(root: Path, name: str) -> Path:
        relative = Path(name.replace("\\", "/"))
        if relative.is_absolute() or any(
            part in ("", ".", "..") for part in relative.parts
        ):
            raise ValueError(f"非法资源路径: {name}")
        return root / relative

    def _scan(self, entries: list[dict[str, str]]) -> list[dict[str, str]]:
        dirs = self._resource_dirs()
        missing: list[dict[str, str]] = []
        total = len(entries)
        for index, item in enumerate(entries, 1):
            if self._cancel.is_set():
                raise _Cancelled
            try:
                path = self._target(dirs[item["kind"]], item["file"])
                ok = path.is_file() and _sha256(path) == item["hash"]
            except Exception:
                ok = False
            if not ok:
                missing.append(item)
            if index % 40 == 0 or index == total:
                self._patch(completed=index, current=item["file"])
        return missing

    # ----------------------------------------------------------------- 下载
    def _download(self, missing: list[dict[str, str]]) -> None:
        dirs = self._resource_dirs()
        completed = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._download_one, dirs, item): item for item in missing
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    future.result()
                except _Cancelled:
                    pass
                except Exception as exc:
                    self._record_error(item, str(exc))
                finally:
                    completed += 1
                    self._patch(completed=completed, current=item["file"])
        if self._cancel.is_set():
            raise _Cancelled

    def _download_bases(self) -> list[str]:
        """按「清单可用源 → 已配置镜像 → 内置镜像」的顺序给出下载候选源。"""
        bases = self._candidate_bases(None)
        primary = self._normalize_base(self._base) if self._base else ""
        if primary and primary in bases:
            bases.remove(primary)
            bases.insert(0, primary)
        return bases

    def _download_one(self, dirs: dict[str, Path], item: dict[str, str]) -> None:
        if self._cancel.is_set():
            raise _Cancelled

        target = self._target(dirs[item["kind"]], item["file"])
        target.parent.mkdir(parents=True, exist_ok=True)
        relative = f"v{MEME_VERSION}/resources/{item['kind']}/{quote(item['file'], safe='/')}"

        errors: list[str] = []
        for base in self._download_bases():
            for attempt in range(ATTEMPTS_PER_MIRROR):
                if self._cancel.is_set():
                    raise _Cancelled
                try:
                    self._fetch_to_path(base + relative, target, item["hash"])
                    return
                except _Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{base}（{exc}）")
                    if attempt + 1 < ATTEMPTS_PER_MIRROR:
                        time.sleep(RETRY_DELAY)

        detail = "；".join(errors[-3:])
        raise RuntimeError(f"全部镜像均失败。{detail}")

    def _fetch_to_path(self, url: str, target: Path, expected_hash: str) -> None:
        """下载单个文件并校验哈希，成功后才替换到目标位置。"""
        temp = target.with_name(f"{target.name}.part")
        digest = hashlib.sha256()
        written = 0
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "astrbot-plugin-memelite"}
            )
            with urllib.request.urlopen(request, timeout=60) as response, temp.open(
                "wb"
            ) as fh:
                while True:
                    chunk = response.read(1 << 15)
                    if not chunk:
                        break
                    if self._cancel.is_set():
                        raise _Cancelled
                    fh.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    self._add_bytes(len(chunk))
            if digest.hexdigest() != expected_hash:
                raise RuntimeError("文件校验失败（哈希不匹配）")
            os.replace(temp, target)
        except BaseException:
            # 失败后重试时要回退本次已计入的进度字节，避免统计偏高
            if written:
                self._add_bytes(-written)
            raise
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
