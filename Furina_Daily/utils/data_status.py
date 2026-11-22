"""模块级数据状态登记：区分「全部真实数据」与「存在 mock 降级」。"""

from dataclasses import field, dataclass


@dataclass
class DegradedItem:
    name: str
    detail: str = ""


@dataclass
class DataStats:
    ok: list[str] = field(default_factory=list)
    degraded: list[DegradedItem] = field(default_factory=list)

    @property
    def level(self) -> str:
        return "degraded" if self.degraded else "ok"


_stats = DataStats()


def reset_stats() -> None:
    global _stats
    _stats = DataStats()


def mark_module(name: str, status: str, detail: str = "") -> None:
    if not name:
        return
    if status == "ok":
        if name not in _stats.ok:
            _stats.ok.append(name)
        _stats.degraded = [d for d in _stats.degraded if d.name != name]
    elif not any(d.name == name for d in _stats.degraded):
        _stats.degraded.append(DegradedItem(name=name, detail=str(detail or "")))


def get_stats() -> DataStats:
    """返回快照，避免调用方改动内部状态。"""
    return DataStats(
        ok=list(_stats.ok),
        degraded=[DegradedItem(d.name, d.detail) for d in _stats.degraded],
    )


MODULE_NAMES: dict[str, str] = {
    "news60s": "60s 读世界",
    "moyu": "摸鱼日历",
    "zhihu": "知乎话题榜",
    "bilibili": "哔哩哔哩热搜",
    "itNews": "IT 资讯",
    "douyin": "抖音热搜",
    "toutiao": "头条热搜",
    "bangumi": "今日新番",
}
