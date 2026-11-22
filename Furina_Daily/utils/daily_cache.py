"""日报缓存：配置指纹 + 带时效的缓存条目。

缓存的正确性依赖三件事：
1. 日期换天必须失效；
2. 配置变了必须失效（WebConsole 改主题/热搜/标题不会走命令，没有 hook 可用，
   所以每次取缓存时重算指纹比对，是唯一稳妥的做法）；
3. 定时推送不能复用太久以前的图（否则 22:00 会推 10:00 那张），因此引入 max_age。
"""

import json
import time
import hashlib
from dataclasses import dataclass

from .data_status import DataStats

# 参与缓存指纹的配置项：任何一项变化都会让今日缓存失效。
FINGERPRINT_KEYS = (
    # 外观
    "theme",
    "customTitle",
    "logoImage",
    "logoSize",
    "titleFont",
    "titleFontSize",
    "secondaryTitleFont",
    "secondaryTitleFontSize",
    "contentFont",
    "contentFontSize",
    "renderScale",
    # 内容
    "hotModule",
    "sideModule",
    # 图片
    "compressImage",
    "compressFormat",
    # 数据源：换了地址就等于换了一份数据，缓存必须失效，否则改了 API 还看到旧图
    "apiBase",
    "proxy",
)


def config_fingerprint(config: dict) -> str:
    """把影响出图结果的配置折算成短哈希，用于判断缓存是否仍然对应当前配置。"""
    payload = {key: config.get(key) for key in FINGERPRINT_KEYS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    """一次成功生成的产物快照。"""

    image: bytes
    date: str
    fingerprint: str
    built_at: float
    stats: DataStats

    def is_fresh(self, date: str, fingerprint: str, max_age: float | None) -> bool:
        """max_age 为 None 表示「当日有效」；为 0 表示必须重新生成。

        用严格小于：Windows 上 time.monotonic() 只有约 15.6ms 分辨率，刚生成的条目
        elapsed 可能正好是 0.0，用 <= 会让 max_age=0 意外命中缓存。
        """
        if self.date != date or self.fingerprint != fingerprint:
            return False
        if max_age is None:
            return True
        return (time.monotonic() - self.built_at) < max_age
