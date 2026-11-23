"""主人告警封装：直连活跃连接私聊主人，带冷却去重。

不走框架的 ``send_msg_to_master``：它按 ``(user_id, bot_id)`` 去重后**只发 rowid 最小的那条**，
而那一行的 ``WS_BOT_ID`` 常常是早已断开的旧连接名，``Subscribe.send`` 会直接返回 -1，
于是告警静默丢失（本插件的降级/连败告警长期发不出去就是这个原因）。
这里改为按订阅表里的真实账号元数据直投；拿不到元数据时退回「当前唯一活跃连接」。
"""

import time

from gsuid_core.gss import gss
from gsuid_core.config import core_config
from gsuid_core.logger import logger

_last_sent: dict[str, float] = {}
COOLDOWN_SECONDS = 1800

# MessageSend.bot_id 只参与统计与历史归因，不参与连接选择（帧走当前 _Bot 的 ws），
# 因此没有订阅行可参照时用 onebot 兜底即可；bot_self_id 留空时
# OneBotUID 的 find_api("") 会在「只有一条协议端连接」时自动命中。
FALLBACK_BOT_ID = "onebot"


async def _resolve_target() -> tuple[str, str, str] | None:
    """挑一对真能发出去的身份：(WS_BOT_ID, bot_id, bot_self_id)。"""
    from ..push.scheduler import _bot_metadata  # 局部导入：scheduler 模块级依赖本模块

    meta = await _bot_metadata()
    if meta is not None and meta["WS_BOT_ID"] in gss.active_bot:
        return meta["WS_BOT_ID"], meta["bot_id"], meta["bot_self_id"]

    live = list(gss.active_bot)
    if len(live) == 1:
        return live[0], FALLBACK_BOT_ID, ""
    return None


def _master_id() -> str:
    masters = core_config.get_config("masters")
    if not masters:
        return ""
    return str(masters[0]).strip()


async def notify_master(text: str, *, key: str = "default", cooldown: int = COOLDOWN_SECONDS) -> bool:
    """同一 key 在 cooldown 秒内只发送一次；成功返回 True。"""
    now = time.monotonic()
    if now - _last_sent.get(key, 0.0) < cooldown:
        return False

    master_id = _master_id()
    if not master_id:
        logger.warning("[芙芙日报] 未配置主人，告警未发送")
        return False

    target = await _resolve_target()
    if target is None:
        logger.warning("[芙芙日报] 没有可用于告警的 bot 连接，本次未发送")
        return False

    ws_bot_id, bot_id, bot_self_id = target
    try:
        await gss.active_bot[ws_bot_id].target_send(text, "direct", master_id, bot_id, bot_self_id)
    except Exception as exc:  # noqa: BLE001 - 告警失败不应影响主流程
        logger.warning(f"[芙芙日报] 告警发送失败（{ws_bot_id} -> {master_id}）：{exc}")
        return False

    _last_sent[key] = now
    return True
