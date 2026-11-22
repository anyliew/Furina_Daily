"""本地示例数据仓库：resources/mock/<模块>.json。"""

import json
from pathlib import Path

from .path import MOCK_DIR, MOCK_IMAGE_PATH

MOCK_KEYS = ["news60s", "moyu", "zhihu", "bilibili", "itNews", "douyin", "toutiao", "bangumi"]
IMAGE_FIELDS = {"cover", "image", "bgImage", "icon", "pic"}


def _resolve_asset(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(("http://", "https://", "//", "data:", "file:")):
        return value
    return (MOCK_IMAGE_PATH / Path(value).name).resolve().as_uri()


def _hydrate(node: object) -> object:
    if isinstance(node, list):
        return [_hydrate(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, object] = {}
    for key, value in node.items():
        if key in IMAGE_FIELDS and isinstance(value, str):
            out[key] = _resolve_asset(value)
        else:
            out[key] = _hydrate(value)
    return out


def read_mock(key: str) -> object | None:
    """读取某模块示例数据；不存在或损坏返回 None。"""
    path = MOCK_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _hydrate(raw)


def get_mock_status() -> dict:
    """返回各模块示例数据概况，供「日报模拟」预检。"""
    meta_path = MOCK_DIR / "meta.json"
    meta: dict[str, str] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

    items = []
    for key in MOCK_KEYS:
        data = read_mock(key)
        count = 0
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            for field in ("worldNews", "items", "countdowns"):
                value = data.get(field)
                if isinstance(value, list):
                    count = len(value)
                    break
        items.append({"key": key, "has": data is not None, "count": count, "updatedAt": meta.get(key, "")})

    image_count = len(list(MOCK_IMAGE_PATH.glob("*"))) if MOCK_IMAGE_PATH.exists() else 0
    return {"items": items, "generatedAt": meta.get("generatedAt", ""), "imageCount": image_count}
