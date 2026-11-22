"""主人告警封装：带冷却去重，避免重复降级告警刷屏。"""

import time

from gsuid_core.utils.message import send_msg_to_master

_last_sent: dict[str, float] = {}
COOLDOWN_SECONDS = 1800


async def notify_master(text: str, *, key: str = "default", cooldown: int = COOLDOWN_SECONDS) -> bool:
    """同一 key 在 cooldown 秒内只发送一次；成功返回 True。"""
    now = time.monotonic()
    last = _last_sent.get(key, 0.0)
    if now - last < cooldown:
        return False
    try:
        await send_msg_to_master(text)
        _last_sent[key] = now
        return True
    except Exception:  # noqa: BLE001 - 告警失败不应影响主流程
        return False
