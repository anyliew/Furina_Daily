"""Furina_Daily 配置实例与读取封装。"""

from gsuid_core.utils.plugins_config.models import GsConfig
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from ..utils.path import CONFIG_PATH
from .config_default import (
    CONFIG_DEFAULT,
    PUSH_TIMES_DEFAULT,
    REFRESH_TIMES_DEFAULT,
    hot_label_for,
    normalize_hot,
    normalize_side,
    side_label_for,
    normalize_theme,
    theme_label_for,
)

furina_config = StringConfig("Furina_Daily", CONFIG_PATH, CONFIG_DEFAULT)


def _data(key: str) -> object:
    item = furina_config.config.get(key)
    return item.data if isinstance(item, GsConfig) else None


def _migrate_legacy_values() -> None:
    """一次性迁移旧配置：英文模块 code -> 中文展示名；早晚时间 -> 推送时间列表。"""
    for key, label_for in (
        ("theme", theme_label_for),
        ("hotModule", hot_label_for),
        ("sideModule", side_label_for),
    ):
        label = label_for(_data(key))
        if label is not None:
            furina_config.set_config(key, label)

    # 旧版本热搜展示名「抖音」改为「抖音热搜」
    if _data("hotModule") == "抖音":
        furina_config.set_config("hotModule", hot_label_for("douyin"))

    # 旧版本把时间拆成 morningTime / eveningTime：合并进 pushTimes。
    push_raw = _data("pushTimes")
    if not isinstance(push_raw, list) or not push_raw:
        old_times = [
            str(_data(key)).strip()
            for key in ("morningTime", "eveningTime")
            if isinstance(_data(key), str) and str(_data(key)).strip()
        ]
        if old_times:
            furina_config.set_config("pushTimes", old_times)

    # 旧版本的 reportGroup 是推送群的本地镜像，与订阅表重复且会脱节，清掉避免误读。
    if "reportGroup" in furina_config.config:
        furina_config.config.pop("reportGroup", None)
        furina_config.write_config()


_migrate_legacy_values()


def _text(key: str, default: str = "") -> str:
    value = _data(key)
    return value if isinstance(value, str) else default


def _time_list(key: str, default: list[str]) -> list[str]:
    """读取时间列表；保证至少一个，空列表回退默认值。"""
    raw = _data(key)
    if isinstance(raw, list):
        times = [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]
        if times:
            return times
    return list(default)


def get_config() -> dict:
    """把 StringConfig 展开成渲染层使用的配置字典。"""
    api_base: dict[str, str] = {
        "viki": _text("api_viki"),
        "news60s": _text("api_news60s"),
        "moyu": _text("api_moyu"),
        "zhihu": _text("api_zhihu"),
        "itNews": _text("api_itNews"),
        "douyin": _text("api_douyin"),
        "toutiao": _text("api_toutiao"),
        "bilibili": _text("api_bilibili"),
        "bangumi": _text("api_bangumi"),
    }

    scale_raw = _data("renderScale")
    scale = scale_raw if isinstance(scale_raw, int) else 1
    compress_raw = _data("compressImage")
    compress = compress_raw if isinstance(compress_raw, bool) else False

    return {
        "pushTimes": _time_list("pushTimes", PUSH_TIMES_DEFAULT),
        "refreshTimes": _time_list("refreshTimes", REFRESH_TIMES_DEFAULT),
        "theme": normalize_theme(_data("theme")),
        "customTitle": _text("customTitle", "芙芙心日报"),
        "logoImage": _text("logoImage", "logo.png"),
        "logoSize": _text("logoSize"),
        "titleFont": _text("titleFont", "Title.ttf"),
        "titleFontSize": _text("titleFontSize"),
        "secondaryTitleFont": _text("secondaryTitleFont", "Secondary_Title.ttf"),
        "secondaryTitleFontSize": _text("secondaryTitleFontSize"),
        "contentFont": _text("contentFont", "Content.ttf"),
        "contentFontSize": _text("contentFontSize"),
        "renderScale": scale,
        "hotModule": normalize_hot(_data("hotModule")),
        "sideModule": normalize_side(_data("sideModule")),
        "compressImage": compress,
        "compressFormat": _text("compressFormat", "png"),
        "apiBase": api_base,
        "proxy": {"bangumi": _text("proxy_bangumi")},
    }


def set_value(key: str, value: str | int | bool | list[str]) -> bool:
    return furina_config.set_config(key, value)
