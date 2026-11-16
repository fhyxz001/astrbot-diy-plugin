"""插件内部异常。"""

from __future__ import annotations


class PtError(Exception):
    """可以直接展示给用户的错误。"""


class CookieInvalidError(PtError):
    """Cookie 失效或没有访问权限。"""


class NexusError(PtError):
    """站点请求失败。"""


class TorrentFileError(NexusError):
    """下载到的内容不是有效种子文件。"""


class QbittorrentError(PtError):
    """qBittorrent 操作失败。"""
