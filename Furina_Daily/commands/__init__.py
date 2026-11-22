"""Furina_Daily 命令层：日报查询、订阅、配置与主题切换指令。"""

import time
import asyncio
from pathlib import Path
from dataclasses import dataclass

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from ..config import set_value, get_config, furina_config
from ..utils.format import cron_human, mask_sensitive
from ..render.generator import generate_daily, browser_session, normalize_format
from ..utils.mock_store import get_mock_status
from ..utils.daily_cache import CacheEntry, config_fingerprint
from ..utils.data_status import MODULE_NAMES, DataStats, get_stats
from ..utils.message_hint import notify_master
from ..config.config_default import THEME_ENTRIES, hot_label_for, side_label_for

sv_daily = SV("芙芙日报", pm=6, area="ALL")
sv_admin = SV("芙芙日报管理", pm=0, area="ALL")

TASK_NAME = "Furina_Daily 日报推送"

# 定时推送可复用多久以内的缓存：默认刷新 21:00 → 推送 22:00、刷新 09:30 → 推送 10:00 都能直接复用，
# 避免同一张图重复生成；超过 1 小时则重新生成，避免「晚上推早上那张」。
PUSH_CACHE_MAX_AGE = 3600.0
# 定时刷新 / 手动刷新必须重新生成。
REFRESH_CACHE_MAX_AGE = 0.0
# 配置备份只留最近几份，长期反复重置不会把 data 目录堆满。
BACKUP_KEEP_FILES = 5
# 「日报模拟」连出五张图，间隔一下，避免触发适配器发送频率限制。
MOCK_SEND_INTERVAL = 1.0

_cache: CacheEntry | None = None
_gen_lock = asyncio.Lock()


@dataclass(frozen=True)
class DailyImage:
    """一次取图的结果。

    fresh=False 表示命中了缓存，此时 stats 是「上一次生成」留下的快照，
    不能拿来再做降级告警，否则每次查询都会把老告警重发一遍。
    """

    image: bytes
    fresh: bool
    stats: DataStats


def _today() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def _invalidate_cache() -> None:
    global _cache
    _cache = None


def _compress_label(fmt: object) -> str:
    return "WebP（无损）" if normalize_format(fmt) == "webp-lossless" else "PNG（无损）"


async def _get_image(*, max_age: float | None = None, wait: bool = False) -> DailyImage | None:
    """取今日日报。

    - max_age=None：当日缓存一直有效（手动命令）
    - max_age=0：必须重新生成（刷新日报 / 定时刷新）
    - max_age=N：N 秒内的缓存可复用（定时推送，避免推出几小时前的旧图）

    缓存同时校验配置指纹：WebConsole 改主题 / 热搜 / 标题 / 数据源后会自动失效，
    不必等到第二天或手动刷新。
    """
    global _cache
    config = get_config()
    today = _today()
    fingerprint = config_fingerprint(config)

    entry = _cache
    if entry is not None and entry.is_fresh(today, fingerprint, max_age):
        return DailyImage(image=entry.image, fresh=False, stats=entry.stats)

    if _gen_lock.locked() and not wait:
        return None

    async with _gen_lock:
        # 排队期间可能有别的任务刚生成完，进锁后再复查一次
        entry = _cache
        if entry is not None and entry.is_fresh(today, fingerprint, max_age):
            return DailyImage(image=entry.image, fresh=False, stats=entry.stats)

        logger.info("[芙芙日报] 开始生成芙芙日报...")
        image = await generate_daily(config)
        # 生成全程独占锁，此刻的 stats 必然是这一次生成的结果
        stats = get_stats()
        _cache = CacheEntry(
            image=image,
            date=today,
            fingerprint=fingerprint,
            built_at=time.monotonic(),
            stats=stats,
        )
        return DailyImage(image=image, fresh=True, stats=stats)


async def _notify_degraded(source: str, stats: DataStats) -> None:
    """降级告警：只在本次真的重新生成时发，同一原因 30 分钟内只发一次。"""
    if stats.level != "degraded":
        return
    lines = []
    for item in stats.degraded:
        name = MODULE_NAMES.get(item.name, item.name)
        lines.append(f"{name}: {item.detail or '数据源不可用'}")
    await notify_master(
        f"[芙芙日报] {source}：日报已生成，但以下模块降级为示例数据：\n" + "\n".join(lines),
        key="degraded",
    )


async def _deliver(bot: Bot, result: DailyImage | None, source: str) -> None:
    if result is None:
        await bot.send("⏳ 日报正在生成中，请稍后再试。")
        return
    await bot.send(MessageSegment.image(result.image))
    if result.fresh:
        await _notify_degraded(source, result.stats)


async def _send_failure(bot: Bot, ev: Event, exc: BaseException) -> None:
    """失败反馈：群聊隐去内部地址与磁盘路径，私聊主人保留完整原文便于排查。"""
    raw = str(exc)
    detail = f"{type(exc).__name__}: {raw}" if raw else type(exc).__name__
    if ev.group_id:
        masked = mask_sensitive(detail)
        if masked != detail:
            masked += "\n（详情见后台日志）"
        text = f"❌ 日报生成失败：{masked}"
    else:
        text = f"❌ 日报生成失败：{detail}"
    try:
        await bot.send(text)
    except Exception as send_exc:  # noqa: BLE001 - 连失败提示都发不出去就只能记日志
        logger.error(f"[芙芙日报] 发送失败提示时出错: {send_exc}")


# ---------------------------------------------------------------------------
# 普通命令
# ---------------------------------------------------------------------------
@sv_daily.on_fullmatch(("芙芙日报", "日报", "今日日报", "今日头条"), block=True)
async def manual_daily(bot: Bot, ev: Event) -> None:
    try:
        result = await _get_image()
        await _deliver(bot, result, f"命令触发（群 {ev.group_id or '私聊'}）")
    except Exception as exc:  # noqa: BLE001 - 命令需给用户友好反馈
        logger.error(f"[芙芙日报] 手动获取日报失败: {exc}")
        await _send_failure(bot, ev, exc)


@sv_admin.on_fullmatch("日报数据源", block=True)
async def show_api_status(bot: Bot, ev: Event) -> None:
    from ..utils.api_base import get_current_base

    config = get_config()
    rows = [
        ("60S 读世界", "news60s"),
        ("摸鱼日历", "moyu"),
        ("知乎话题榜", "zhihu"),
        ("哔哩哔哩热搜", "bilibili"),
        ("IT 资讯", "itNews"),
        ("抖音热搜", "douyin"),
        ("头条热搜", "toutiao"),
        ("今日新番", "bangumi"),
    ]
    text = "\n".join(f"{label}：{get_current_base(config, key)}" for label, key in rows)
    await bot.send(f"🌐 当前数据源\n{text}\n（请求失败会自动切换到内置实例）")


# ---------------------------------------------------------------------------
# 订阅管理（用 gs_subscribe 持久化，仅主人可操作）
# ---------------------------------------------------------------------------
@sv_admin.on_fullmatch("开启日报推送", block=True)
async def subscribe_group(bot: Bot, ev: Event) -> None:
    if not ev.group_id:
        await bot.send("❌ 仅群聊可用")
        return
    from gsuid_core.subscribe import gs_subscribe

    await gs_subscribe.add_subscribe(
        subscribe_type="session",
        task_name=TASK_NAME,
        event=ev,
    )
    config = get_config()
    times = "、".join(cron_human(item) for item in config["pushTimes"])
    await bot.send(f"✅ 已开启日报推送！本群将在每日 {times} 自动收到日报。")


@sv_admin.on_fullmatch("关闭日报推送", block=True)
async def unsubscribe_group(bot: Bot, ev: Event) -> None:
    if not ev.group_id:
        await bot.send("❌ 仅群聊可用")
        return
    from gsuid_core.subscribe import gs_subscribe

    await gs_subscribe.delete_subscribe("session", TASK_NAME, ev)
    await bot.send("❎ 已关闭日报推送。")


# ---------------------------------------------------------------------------
# 主人命令
# ---------------------------------------------------------------------------
@sv_admin.on_fullmatch("刷新日报", block=True)
async def refresh_daily(bot: Bot, ev: Event) -> None:
    await bot.send("🔄 正在刷新日报...")
    try:
        result = await _get_image(max_age=REFRESH_CACHE_MAX_AGE)
        await _deliver(bot, result, "主人刷新日报")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[芙芙日报] 刷新日报失败: {exc}")
        await _send_failure(bot, ev, exc)


@sv_admin.on_fullmatch("日报模拟", block=True)
async def mock_daily(bot: Bot, ev: Event) -> None:
    status = get_mock_status()
    if not any(item["has"] for item in status["items"]):
        await bot.send("⚠️ 未找到本地示例数据（resource/mock/*.json），无法生成预览日报。")
        return
    await bot.send(f"🔄 正在生成模拟日报（本地示例数据 × {len(THEME_ENTRIES)} 套主题）...")

    ok = 0
    try:
        # 五张图共用一个 chromium，省掉四次冷启动
        async with browser_session() as browser:
            for index, (label, theme) in enumerate(THEME_ENTRIES):
                try:
                    config = get_config()
                    config["theme"] = theme
                    config["customTitle"] = "模拟日报"
                    image = await generate_daily(config, use_mock=True, browser=browser)
                    await bot.send(MessageSegment.image(image))
                    ok += 1
                except Exception as exc:  # noqa: BLE001 - 单套主题失败不影响其它主题
                    logger.error(f"[芙芙日报] 模拟日报（{label}）生成失败: {exc}")
                if index + 1 < len(THEME_ENTRIES):
                    await asyncio.sleep(MOCK_SEND_INTERVAL)
    except Exception as exc:  # noqa: BLE001 - 浏览器起不来时别把异常抛给适配器
        logger.error(f"[芙芙日报] 模拟日报生成失败: {exc}")

    if ok:
        await bot.send(f"✅ 模拟日报已完成（{ok}/{len(THEME_ENTRIES)} 套主题）")
    else:
        await bot.send("❌ 模拟日报生成失败，请查看日志。")


def _prune_backups(parent: Path) -> int:
    """只留最近 BACKUP_KEEP_FILES 份配置备份，返回删除数量。"""
    files = sorted(parent.glob("daily_*.json"), key=lambda p: p.name, reverse=True)
    removed = 0
    for old in files[BACKUP_KEEP_FILES:]:
        try:
            old.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning(f"[芙芙日报] 清理旧配置备份失败（{old.name}）: {exc}")
    return removed


async def _subscription_count() -> int:
    """当前推送订阅数；查不到就返回 0，不影响重置流程。"""
    from gsuid_core.subscribe import gs_subscribe

    try:
        subs = await gs_subscribe.get_subscribe(TASK_NAME)
    except Exception as exc:  # noqa: BLE001 - 仅用于文案提示
        logger.warning(f"[芙芙日报] 查询推送订阅失败: {exc}")
        return 0
    return len(subs) if subs else 0


@sv_admin.on_fullmatch("日报清空配置", block=True)
async def clear_config(bot: Bot, ev: Event) -> None:
    from copy import deepcopy
    from datetime import datetime

    from ..config.config_default import CONFIG_DEFAULT

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        config_file = furina_config.CONFIG_PATH
        backup = config_file.with_name(f"daily_{stamp}.json")
        backup.write_bytes(config_file.read_bytes())

        for key, default_item in CONFIG_DEFAULT.items():
            furina_config.set_config(key, deepcopy(default_item.data))
        furina_config.write_config()

        _invalidate_cache()
        from ..push.scheduler import reschedule_jobs

        await reschedule_jobs()

        pruned = _prune_backups(backup.parent)
        text = f"✅ 日报配置已重置为默认，原配置备份为 {backup.name}"
        if pruned:
            text += f"（已清理 {pruned} 份更早的备份，最多保留 {BACKUP_KEEP_FILES} 份）"
        # 配置重置不动订阅表：推送到点仍会执行，必须说清楚
        count = await _subscription_count()
        if count:
            text += (
                f"\n⚠️ 推送订阅仍保留 {count} 个会话：重置配置不会退订。"
                "需要停止推送，请到 WebConsole 的订阅管理页删除，或在对应群发送「关闭日报推送」。"
            )
        await bot.send(text)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[芙芙日报] 清空配置失败: {exc}")
        await _send_failure(bot, ev, exc)


async def _switch_hot(bot: Bot, module: str, label: str) -> None:
    config = get_config()
    if config["hotModule"] == module:
        await bot.send(f"当前海报热搜已是{label}，无需切换")
        return
    set_value("hotModule", hot_label_for(module))
    _invalidate_cache()
    await bot.send(f"✅ 已切换为{label}，下次生成日报时生效")


@sv_admin.on_fullmatch("海报切换抖音热搜", block=True)
async def switch_douyin(bot: Bot, ev: Event) -> None:
    await _switch_hot(bot, "douyin", "抖音热搜")


@sv_admin.on_fullmatch("海报切换今日新番", block=True)
async def switch_bangumi(bot: Bot, ev: Event) -> None:
    await _switch_hot(bot, "bangumi", "今日新番")


@sv_admin.on_fullmatch("海报切换头条热搜", block=True)
async def switch_toutiao(bot: Bot, ev: Event) -> None:
    await _switch_hot(bot, "toutiao", "头条热搜")


@sv_admin.on_fullmatch(("侧栏切换知乎话题榜", "侧栏切换知乎"), block=True)
async def switch_zhihu(bot: Bot, ev: Event) -> None:
    config = get_config()
    if config["sideModule"] == "zhihu":
        await bot.send("当前侧栏已是知乎话题榜，无需切换")
        return
    set_value("sideModule", side_label_for("zhihu"))
    _invalidate_cache()
    await bot.send("✅ 侧栏已切换为知乎话题榜，下次生成日报时生效")


@sv_admin.on_fullmatch(("侧栏切换B站热搜", "侧栏切换b站热搜", "侧栏切换哔哩哔哩热搜"), block=True)
async def switch_bilibili(bot: Bot, ev: Event) -> None:
    config = get_config()
    if config["sideModule"] == "bilibili":
        await bot.send("当前侧栏已是哔哩哔哩热搜，无需切换")
        return
    set_value("sideModule", side_label_for("bilibili"))
    _invalidate_cache()
    await bot.send("✅ 侧栏已切换为哔哩哔哩热搜，下次生成日报时生效")


@sv_admin.on_command("日报压缩", block=True)
async def toggle_compress(bot: Bot, ev: Event) -> None:
    arg = ev.text.strip().lower()
    config = get_config()
    current = bool(config["compressImage"])
    if arg in ("开", "on"):
        enable = True
    elif arg in ("关", "off"):
        enable = False
    else:
        enable = not current
    set_value("compressImage", enable)
    _invalidate_cache()
    label = _compress_label(config["compressFormat"])
    await bot.send(f"✅ 日报图片压缩已{'开启' if enable else '关闭'}（格式 {label}），下次生成日报时生效")


@sv_admin.on_prefix("日报主题切换", block=True)
async def switch_theme(bot: Bot, ev: Event) -> None:
    name = ev.text.strip()
    for label, value in THEME_ENTRIES:
        if label in name or value.lower() in name.lower():
            set_value("theme", label)
            _invalidate_cache()
            await bot.send(f"✅ 已切换至主题【{label}】，下次生成日报时生效")
            return
    options = "、".join(label for label, _ in THEME_ENTRIES)
    await bot.send(f"❌ 主题名称错误，可用主题：{options}。示例：日报主题切换 霜笺白鹭")


# ---------------------------------------------------------------------------
# 供定时任务复用
# ---------------------------------------------------------------------------
async def generate_for_push() -> DailyImage | None:
    """定时推送：优先复用刚刷新过的缓存，避免与刷新任务重复生成。"""
    try:
        return await _get_image(max_age=PUSH_CACHE_MAX_AGE, wait=True)
    except Exception as exc:  # noqa: BLE001 - 推送失败私聊告知主人，不向群抛错
        logger.error(f"[芙芙日报] 定时生成日报失败: {exc}")
        await notify_master(f"[芙芙日报] 定时生成日报失败：{exc}", key="push")
        return None


async def generate_for_refresh() -> DailyImage:
    """定时刷新缓存：强制重新生成并更新今日缓存。

    失败向外抛，由调度层统计「连续失败次数」，连续多次才告警，避免单次抖动打扰主人。
    """
    result = await _get_image(max_age=REFRESH_CACHE_MAX_AGE, wait=True)
    if result is None:  # wait=True 不会走到这里，留一个显式断言式的兜底
        raise RuntimeError("刷新日报未产出图片")
    return result
