"""海报图片处理：把多组拼图裁剪成当前页那一组。"""

from __future__ import annotations

import io

try:  # Pillow 已在 requirements.txt 中声明；缺失时优雅降级。
    from PIL import Image
except ImportError:  # pragma: no cover - 取决于运行环境
    Image = None  # type: ignore[assignment]

GROUP_GAP = 12
GROUP_HEIGHT = 2560


def crop_page(image_bytes: bytes, groups: int, page: int) -> tuple[bytes, bool]:
    """从 groups 组的拼图里裁出第 page 组（页码从 1 开始）。

    Returns:
        (图片字节, 是否真的裁剪过)
    """
    if not image_bytes or groups <= 1 or page <= 1 or page > groups:
        return image_bytes, False
    if Image is None:
        return image_bytes, False

    try:
        source = Image.open(io.BytesIO(image_bytes))
        source.load()
        width, height = source.size
    except Exception:
        return image_bytes, False

    # 已知版式：每组 2560px，组间 12px。若服务端版式变化，按实际高度反推组高。
    expected = groups * GROUP_HEIGHT + (groups - 1) * GROUP_GAP
    if abs(height - expected) <= groups * 4:
        group_height = GROUP_HEIGHT
    else:
        group_height = max(1, (height - (groups - 1) * GROUP_GAP) // groups)

    top = (page - 1) * (group_height + GROUP_GAP)
    if top + group_height > height + GROUP_GAP:
        return image_bytes, False

    bottom = min(top + group_height, height)
    try:
        cropped = source.crop((0, top, width, bottom))
        if cropped.mode != "RGB":
            cropped = cropped.convert("RGB")
        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue(), True
    except Exception:
        return image_bytes, False
