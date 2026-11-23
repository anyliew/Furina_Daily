"""定时任务：多个推送时间 + 多个缓存刷新时间。

遵循 GsCore 规范：主动推送一律走 gs_subscribe，不硬遍历 active_bot。
缓存刷新只预生成图片、不发消息，作用是让用户随时取图都能命中缓存。
"""

from collections.abc import Callable, Awaitable

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gsuid_core.aps import scheduler
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.subscribe import gs_subscribe

from ..config import get_config
from ..utils.format import cron_human
from ..utils.data_status import MODULE_NAMES
from ..utils.message_hint import notify_master
from ..config.config_default import PUSH_TIMES_DEFAULT, REFRESH_TIMES_DEFAULT

TASK_NAME = "Furina_Daily 日报推送"
PUSH_JOB_PREFIX = "furina_daily_push_"
REFRESH_JOB_PREFIX = "furina_daily_refresh_"
# 配置巡检任务：WebConsole 保存配置没有变更回调，靠轮询让新的时间点尽快生效
WATCH_JOB_ID = "furina_daily_watch"
WATCH_INTERVAL_SECONDS = 60

# 旧版固定的早晚任务 id，重排任务时一并清掉。
LEGACY_JOB_IDS = ("furina_daily_morning", "furina_daily_evening")

# 定时刷新连续失败/降级的阈值：单次抖动不打扰主人，连续多次才告警。
REFRESH_ALERT_STREAK = 2

JobFunc = Callable[[str], Awaitable[None]]

# 定时刷新的连续失败计数（失败或存在降级模块都算一次）
_refresh_fail_streak = 0

# 已注册的配置签名（推送时间 / 刷新时间 / 推送群列表），用于检测配置改动
_registered_signature: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None = None

# 上一次「缺群且无 bot 元数据」告警的群集合，用于抑制每分钟重复的 warning
_meta_warned: tuple[str, ...] | None = None


def _effective_times() -> tuple[list[str], list[str]]:
    """读取实际要注册的推送/刷新时间；空列表回退默认，保证至少一个任务。"""
    config = get_config()
    push_times: list[str] = list(config["pushTimes"]) or list(PUSH_TIMES_DEFAULT)
    refresh_times: list[str] = list(config["refreshTimes"]) or list(REFRESH_TIMES_DEFAULT)
    return push_times, refresh_times


def _is_managed_job(job_id: str) -> bool:
    return (
        job_id in LEGACY_JOB_IDS
        or job_id.startswith(PUSH_JOB_PREFIX)
        or job_id.startswith(REFRESH_JOB_PREFIX)
    )


def _add_or_replace(job_id: str, cron: str, func: JobFunc, arg: str, label: str) -> bool:
    """注册/更新一个 Cron 任务；表达式非法时返回 False 而不是抛错中断整轮重排。"""
    try:
        trigger = CronTrigger.from_crontab(cron)
    except ValueError as exc:
        logger.warning(f"[芙芙日报] {label} {cron!r} 不是合法的 Cron 表达式: {exc}")
        return False

    existing = scheduler.get_job(job_id)
    if existing is not None:
        # args 一起改，避免删掉/插入中间项后 label 与实际时间错位
        existing.modify(trigger=trigger, args=[arg])
    else:
        scheduler.add_job(func, trigger=trigger, id=job_id, args=[arg], replace_existing=True)
    return True


async def _push_daily(push_label: str) -> None:
    """生成一次日报并推送给所有订阅会话。"""
    from ..commands import generate_for_push

    logger.info(f"[芙芙日报] 定时任务触发：{push_label}")

    subs = await gs_subscribe.get_subscribe(TASK_NAME)
    if not subs:
        # 到点仍为空：就地补一次。巡检周期（60s）与推送时刻可能错开，
        # 例如 bot 刚重连、连接名变了，等下一轮巡检就会错过这次推送。
        try:
            await sync_subscriptions()
        except Exception as exc:  # noqa: BLE001 - 补订阅失败仍按空订阅处理
            logger.warning(f"[芙芙日报] 推送前补订阅失败：{exc}")
        subs = await gs_subscribe.get_subscribe(TASK_NAME)
    if not subs:
        logger.warning("[芙芙日报] 日报推送订阅列表为空，跳过本次推送")
        return

    result = await generate_for_push()
    if result is None:
        logger.warning(f"[芙芙日报] {push_label} 未取得日报图片，本次跳过推送")
        return

    message = MessageSegment.image(result.image)
    success = 0
    fail = 0
    for sub in subs:
        try:
            await sub.send(message)
            success += 1
        except Exception as exc:  # noqa: BLE001 - 单会话失败不影响其他订阅
            fail += 1
            logger.warning(f"[芙芙日报] 推送失败（{sub.group_id or sub.user_id}）: {exc}")

    # 用本次生成时的 stats 快照，避免并发生成把状态覆盖成别人的结果
    stats = result.stats
    if stats.level == "degraded":
        names = "、".join(MODULE_NAMES.get(d.name, d.name) for d in stats.degraded)
        await notify_master(f"[芙芙日报] {push_label}：日报已推送，但以下模块降级：{names}", key="degraded")
    logger.success(f"[芙芙日报] {push_label} 推送完成：成功 {success}，失败 {fail}")


async def _refresh_cache(refresh_label: str) -> None:
    """定时刷新今日缓存：只预生成，不发消息。"""
    from ..commands import generate_for_refresh

    global _refresh_fail_streak

    logger.info(f"[芙芙日报] 定时刷新缓存触发：{refresh_label}")
    try:
        result = await generate_for_refresh()
    except Exception as exc:  # noqa: BLE001 - 失败下次自动重试，连续多次才告警
        _refresh_fail_streak += 1
        logger.error(f"[芙芙日报] {refresh_label} 刷新缓存失败（连续第 {_refresh_fail_streak} 次）: {exc}")
        if _refresh_fail_streak >= REFRESH_ALERT_STREAK:
            await notify_master(
                f"[芙芙日报] {refresh_label} 连续 {_refresh_fail_streak} 次刷新失败：{exc}",
                key="refresh",
            )
        return

    stats = result.stats
    if stats.level == "degraded":
        _refresh_fail_streak += 1
        names = "、".join(MODULE_NAMES.get(d.name, d.name) for d in stats.degraded)
        logger.warning(f"[芙芙日报] {refresh_label} 刷新完成，但模块降级：{names}")
        if _refresh_fail_streak >= REFRESH_ALERT_STREAK:
            await notify_master(
                f"[芙芙日报] {refresh_label} 连续 {_refresh_fail_streak} 次刷新存在降级模块：{names}",
                key="refresh",
            )
        return

    _refresh_fail_streak = 0
    logger.success(f"[芙芙日报] {refresh_label} 刷新缓存完成（{len(result.image)} bytes）")


async def _bot_metadata() -> dict | None:
    """给「配置里新加的群」找一组能投递的 bot 元数据。

    坑：`gss.active_bot` 的 key 是**连接名**（如 OneBot / napcat-xxx），
    不是 bot_self_id（QQ 号），不能拿它去查 subscribe.bot_self_id。

    WS_BOT_ID 必须是当前活跃连接名：Subscribe.send 先按它查 active_bot，
    查不到再退化为「按 bot_id 反查」——而 _Bot.bot_id 同样是连接名，
    跟 subscribe.bot_id（平台名 onebot）比不中，最终直接返回 -1 发不出去。
    所以宁可不动，也不写一个失效的 WS_BOT_ID。
    """
    from gsuid_core.gss import gss
    from gsuid_core.utils.database.models import Subscribe

    live = list(gss.active_bot)
    if not live:
        return None

    ours = list(await gs_subscribe.get_subscribe(TASK_NAME) or [])
    ours_ids = {row.id for row in ours}
    rows = ours + [row for row in (await Subscribe.select_rows(distinct=False) or []) if row.id not in ours_ids]

    # 1) 连接还活着的行：直接照搬，最稳
    alive = [row for row in rows if row.bot_self_id and str(row.WS_BOT_ID) in live]
    if alive:
        row = alive[0]
        return {
            "bot_id": row.bot_id,
            "bot_self_id": row.bot_self_id,
            "WS_BOT_ID": str(row.WS_BOT_ID),
        }

    # 2) 只认识账号、连接名已变：仅当只有一个活跃连接时改用它
    with_meta = [row for row in rows if row.bot_self_id]
    if with_meta and len(live) == 1:
        row = with_meta[0]
        return {
            "bot_id": row.bot_id,
            "bot_self_id": row.bot_self_id,
            "WS_BOT_ID": live[0],
        }

    # 3) 多连接且没有可参照的行：不猜
    return None


async def sync_subscriptions() -> str:
    """让订阅表与 reportGroup 对齐：配置里有的补订阅，没有的退订。

    reportGroup 是推送群的唯一数据源（WebConsole 与命令都可写），
    订阅表只是它的镜像，实际投递仍走 sub.send。
    只动 task_name == TASK_NAME 的行，不影响其他任务的订阅。
    """
    config = get_config()
    groups: list[str] = list(config["reportGroup"])

    global _meta_warned

    subs = await gs_subscribe.get_subscribe(TASK_NAME)
    group_subs = {str(s.group_id): s for s in (subs or []) if s.group_id}

    added: list[str] = []
    missing = [g for g in groups if g not in group_subs]
    if missing:
        meta = await _bot_metadata()
        if meta is None:
            # 每分钟都会重试，只在「缺的群集合变化」时提醒一次，避免刷日志
            key = tuple(missing)
            if key != _meta_warned:
                logger.warning(
                    f"[芙芙日报] 推送群 {missing} 无法自动补订阅：没有可用的 bot 元数据"
                    "（需有一个已连接的适配器，且订阅表里存在该账号的记录）。"
                    "可先在对应群发送「开启日报推送」建立订阅，之后新群会自动同步"
                )
                _meta_warned = key
        else:
            for gid in missing:
                ev = Event(
                    bot_id=meta["bot_id"],
                    bot_self_id=meta["bot_self_id"],
                    WS_BOT_ID=meta["WS_BOT_ID"],
                    user_id=gid,
                    group_id=gid,
                    user_type="group",
                )
                await gs_subscribe.add_subscribe("session", TASK_NAME, ev)
                added.append(gid)
            _meta_warned = None

    removed: list[str] = []
    for gid in group_subs:
        if gid not in groups:
            await gs_subscribe.delete_subscribe(
                "session",
                TASK_NAME,
                Event(user_id=gid, group_id=gid, user_type="group"),
            )
            removed.append(gid)

    if added or removed:
        logger.success(
            f"[芙芙日报] 推送群配置已同步到订阅表：新增 {added or '无'}，移除 {removed or '无'}"
        )
    return f"新增订阅 {len(added)} 个，移除 {len(removed)} 个"


async def reschedule_jobs() -> None:
    """按 pushTimes / refreshTimes 重设全部定时任务。"""
    global _registered_signature

    push_times, refresh_times = _effective_times()
    # 记下本次注册签名（含推送群列表），供 _watch_config 判断配置是否被改过
    _registered_signature = (tuple(push_times), tuple(refresh_times), tuple(get_config()["reportGroup"]))

    # 先清掉旧版固定任务与本插件所有任务，避免删掉某个时间后残留旧 job。
    for job in scheduler.get_jobs():
        job_id = job.id or ""
        if _is_managed_job(job_id):
            scheduler.remove_job(job_id)

    push_ok = 0
    for index, cron in enumerate(push_times, start=1):
        label = f"第 {index} 个推送时间"
        if _add_or_replace(f"{PUSH_JOB_PREFIX}{index}", cron, _push_daily, label, "推送时间"):
            push_ok += 1

    refresh_ok = 0
    for index, cron in enumerate(refresh_times, start=1):
        label = f"第 {index} 个刷新时间"
        if _add_or_replace(f"{REFRESH_JOB_PREFIX}{index}", cron, _refresh_cache, label, "刷新时间"):
            refresh_ok += 1

    total = len(push_times) + len(refresh_times)
    done = push_ok + refresh_ok
    if done < total:
        logger.warning(f"[芙芙日报] 定时任务注册不完整：成功 {done}/{total}，请检查 Cron 表达式")
        await notify_master(
            f"[芙芙日报] {total - done} 个定时任务因 Cron 表达式无效未注册，请到 WebConsole 检查推送/刷新时间",
            key="cron",
        )
        return

    push_text = "、".join(cron_human(item) for item in push_times)
    refresh_text = "、".join(cron_human(item) for item in refresh_times)
    logger.success(
        f"[芙芙日报] 定时任务已设置：推送 {push_ok} 个（{push_text}），刷新 {refresh_ok} 个（{refresh_text}）"
    )


async def _watch_config() -> None:
    """轮询推送/刷新时间与推送群列表：WebConsole 保存配置后没有回调，靠这里在 1 分钟内生效。

    订阅同步每个周期都跑，不看签名是否变化：bot 连接常常晚于插件启动才建立，
    启动那一次同步可能拿不到 bot 元数据（缺群补不上订阅），之后必须能自愈——
    否则会一直保持空订阅，到点只能打一行「订阅列表为空，跳过本次推送」。
    """
    push_times, refresh_times = _effective_times()
    signature = (tuple(push_times), tuple(refresh_times), tuple(get_config()["reportGroup"]))
    if signature != _registered_signature:
        logger.info("[芙芙日报] 检测到推送/刷新时间或推送群列表变化，重新注册定时任务")
        await reschedule_jobs()

    try:
        await sync_subscriptions()
    except Exception as exc:  # noqa: BLE001 - 巡检失败不中断下一轮，下个周期会重试
        logger.error(f"[芙芙日报] 巡检同步推送订阅失败: {exc}")


def _ensure_watch_job() -> None:
    if scheduler.get_job(WATCH_JOB_ID) is not None:
        return
    scheduler.add_job(
        _watch_config,
        trigger=IntervalTrigger(seconds=WATCH_INTERVAL_SECONDS),
        id=WATCH_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


async def setup_jobs() -> None:
    """框架启动后注册定时任务，并把订阅表对齐到 reportGroup。"""
    _ensure_watch_job()
    await reschedule_jobs()
    try:
        await sync_subscriptions()
    except Exception as exc:  # noqa: BLE001 - 启动时 DB 未就绪不应炸掉注册流程
        logger.error(f"[芙芙日报] 启动同步推送订阅失败: {exc}")
