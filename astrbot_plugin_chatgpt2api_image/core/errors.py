"""生图插件异常类型。"""

from __future__ import annotations


class ImageGenerationError(Exception):
    """生图流程中可以直接展示给用户的异常。"""


class ConfigError(ImageGenerationError):
    """配置缺失或不合法。"""


class ApiError(ImageGenerationError):
    """ChatGPT2API 返回错误或响应无法解析。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
