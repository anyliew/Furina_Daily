"""定时任务：多个推送时间 + 多个缓存刷新时间。

遵循 GsCore 规范：主动推送一律走 gs_subscribe，不硬遍历 active_bot。
缓存刷新只预生成图片、不发消息，作用是让用户随时取图都能命中缓存。
"""

from collections.abc import Callable, Awaitable

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gsuid_core.aps import scheduler
from gsuid_core.logger import logger
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

# 已注册的时间点签名，用于检测配置改动
_registered_signature: tuple[tuple[str, ...], tuple[str, ...]] | None = None


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


async def reschedule_jobs() -> None:
    """按 pushTimes / refreshTimes 重设全部定时任务。"""
    global _registered_signature

    push_times, refresh_times = _effective_times()
    # 记下本次注册签名，供 _watch_config 判断配置是否被改过
    _registered_signature = (tuple(push_times), tuple(refresh_times))

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
    """轮询推送/刷新时间：WebConsole 保存配置后没有回调，靠这里在 1 分钟内生效。"""
    push_times, refresh_times = _effective_times()
    if (tuple(push_times), tuple(refresh_times)) != _registered_signature:
        logger.info("[芙芙日报] 检测到推送/刷新时间变化，重新注册定时任务")
        await reschedule_jobs()


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
    """框架启动后注册定时任务。"""
    _ensure_watch_job()
    await reschedule_jobs()
