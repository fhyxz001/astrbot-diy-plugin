"""插件内部异常。"""

from __future__ import annotations


class CopyMangaError(Exception):
    """可以直接展示给用户的错误。"""


class ApiError(CopyMangaError):
    """接口请求失败。"""


class RiskControlError(ApiError):
    """接口返回 HTTP 210，账号被风控。"""


class TokenExpiredError(ApiError):
    """Token 失效或未授权。"""


class AllAccountsLimitedError(CopyMangaError):
    """账号池暂时没有可用账号。"""

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"账号暂时受到限制，请 {self.retry_after} 秒后重试。")


class IncompleteDownloadError(CopyMangaError):
    """章节图片没有全部下载成功。"""

    def __init__(self, total: int, success: int, failed_pages: list[int]) -> None:
        self.total = total
        self.success = success
        self.failed_pages = failed_pages

        preview = "、".join(str(page) for page in failed_pages[:10])
        if len(failed_pages) > 10:
            preview += " 等"
        super().__init__(
            f"下载不完整：共 {total} 页，成功 {success} 页，失败页码：{preview}。"
            "重新发送下载命令可续传。"
        )
