"""各数据源抓取与聚合。"""

import re
import math
from typing import Callable
from datetime import datetime

import httpx

from gsuid_core.logger import logger

from .http_client import HEADERS, http_get
from ..utils.format import format_hot_value
from ..utils.api_base import request_with_fallback
from ..utils.date_util import get_date_info, weekday_en_to_cn
from ..utils.mock_store import read_mock
from ..utils.data_status import mark_module


# ---------------------------------------------------------------------------
# 摸鱼日历文本解析
# ---------------------------------------------------------------------------
def parse_moyu_text(text: str) -> dict:
    result: dict = {
        "date": None,
        "weekday": None,
        "lunar": None,
        "zodiacYear": None,
        "holidayStatus": None,
        "holidayRemaining": None,
        "lunarFestival": None,
        "countdowns": [],
        "progress": {"week": None, "month": None, "year": None},
        "nextPayday": None,
        "motto": None,
    }

    match = re.search(r"📆\s*(\d{4}-\d{2}-\d{2})\s*(星期[一二三四五六日])", text)
    if match:
        result["date"], result["weekday"] = match.group(1), match.group(2)

    match = re.search(r"🌙\s*农历\s*(.+?)(?:\n|$)", text)
    if match:
        result["lunar"] = match.group(1).strip()

    # 干支纪年每年都变，不能把「丙午」写死；取 🐯 整行即可
    match = re.search(r"🐯\s*([^\n]+)", text)
    if match:
        result["zodiacYear"] = match.group(1).strip()

    if "恭喜！您正处于【" in text and "】假期中！" in text:
        match = re.search(r"恭喜！您正处于【([^】]+)】假期中！", text)
        if match:
            result["holidayStatus"] = weekday_en_to_cn(match.group(1))
        match = re.search(r"还剩\s*(\d+)\s*天（含今天）", text)
        if match:
            result["holidayRemaining"] = int(match.group(1))

    match = re.search(r"🏮\s*农历节日：(.+)", text)
    if match:
        result["lunarFestival"] = match.group(1).strip()

    for name, days in re.findall(r"距离【([^】]+)】：还要搬砖\s*(\d+)\s*天", text):
        result["countdowns"].append({"name": name, "days": int(days), "isHighlight": int(days) <= 30})

    match = re.search(r"距离周末：(.+?)(?:\n|$)", text)
    if match:
        if "今天就是周末" in match.group(1):
            result["countdowns"].append(
                {"name": "周末", "days": 0, "isHighlight": True, "special": "今天就是周末！尽情摸鱼！"}
            )
        else:
            days = re.search(r"(\d+)", match.group(1))
            if days:
                result["countdowns"].append(
                    {"name": "周末", "days": int(days.group(1)), "isHighlight": int(days.group(1)) <= 3}
                )

    match = re.search(r"距离月底：还剩\s*(\d+)\s*天", text)
    if match:
        result["countdowns"].append({"name": "月底", "days": int(match.group(1)), "isHighlight": False})

    match = re.search(r"距离年底：还剩\s*(\d+)\s*天", text)
    if match:
        result["countdowns"].append({"name": "年底", "days": int(match.group(1)), "isHighlight": False})

    for field, pattern in (
        ("week", r"本周\s*(\d+)%"),
        ("month", r"本月\s*(\d+)%"),
        ("year", r"本年\s*(\d+)%"),
    ):
        match = re.search(pattern, text)
        if match:
            result["progress"][field] = int(match.group(1))

    title = re.search(r"🎊\s*节日：(.+)", text)
    date_m = re.search(r"📅\s*日期：(.+)", text)
    duration = re.search(r"⏱️\s*时长：(\d+)\s*天", text)
    adjust = re.search(r"💼\s*调休：(.+)", text)
    if title and date_m:
        result["nextPayday"] = {
            "name": title.group(1).strip(),
            "date": date_m.group(1).strip(),
            "duration": int(duration.group(1)) if duration else None,
            "noWorkAdjust": ("无需调休" in adjust.group(1)) if adjust else None,
        }

    match = re.search(r"💬\s*摸鱼格言\n(.+)", text)
    if match:
        result["motto"] = match.group(1).strip()

    return result


# ---------------------------------------------------------------------------
# 数据源降级取数：失败 -> mock 兜底
# ---------------------------------------------------------------------------
def _fallback(module: str) -> object:
    saved = read_mock(module)
    if saved is not None:
        return saved
    mark_module(module, "degraded", "数据源不可用且无本地示例")
    return None


async def _fetch(
    config: dict,
    module: str,
    path: str,
    transform: Callable[[object], object],
    *,
    accept: str = "application/json, text/plain, */*",
    params: dict | None = None,
) -> object:
    async def handler(base: str) -> object:
        response = await http_get(f"{base}{path}", accept=accept, params=params)
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 200 or not payload.get("data"):
            raise ValueError("API 返回异常")
        return transform(payload["data"])

    try:
        return await request_with_fallback(config, module, handler)
    except Exception as exc:  # noqa: BLE001 - API 失败需降级到 mock
        logger.error(f"[芙芙日报] {module} API 请求失败: {exc}")
        mark_module(module, "degraded", str(exc))
        fallback = _fallback(module)
        return fallback if fallback is not None else None


async def fetch_news60s(config: dict) -> dict:
    def transform(data: object) -> dict:
        d = data if isinstance(data, dict) else {}
        news = d.get("news") if isinstance(d.get("news"), list) else []
        return {"worldNews": news, "date": d.get("date", ""), "tip": d.get("tip", "")}

    result = await _fetch(config, "news60s", "/v2/60s", transform)
    return result if isinstance(result, dict) else {"worldNews": [], "date": "", "tip": ""}


async def fetch_moyu(config: dict) -> dict:
    async def handler(base: str) -> dict:
        response = await http_get(
            f"{base}/v2/moyu",
            accept="text/plain, */*",
            params={"encoding": "text"},
        )
        ctype = response.headers.get("content-type", "")
        if ctype and "text/" not in ctype:
            raise ValueError(f"返回非文本格式（{ctype.split(';')[0]}）")
        text = response.text
        parsed = parse_moyu_text(text)
        if not parsed["date"] or not parsed["countdowns"]:
            raise ValueError("文本格式异常：未解析出日期或倒计时")
        return parsed

    try:
        return await request_with_fallback(config, "moyu", handler)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[芙芙日报] moyu API 请求失败: {exc}")
        mark_module("moyu", "degraded", str(exc))
        fallback = _fallback("moyu")
        return fallback if isinstance(fallback, dict) else {}


async def fetch_zhihu(config: dict) -> list:
    def transform(data: object) -> list:
        items = data if isinstance(data, list) else []
        return [
            {
                "title": item.get("title", ""),
                "hotValueDesc": item.get("hot_value_desc", ""),
                "answerCnt": item.get("answer_cnt", 0),
                "followerCnt": item.get("follower_cnt", 0),
                "commentCnt": item.get("comment_cnt", 0),
                "link": item.get("link", ""),
                "cover": item.get("cover", ""),
                "rank": index + 1,
            }
            for index, item in enumerate(items[:10])
            if isinstance(item, dict)
        ]

    result = await _fetch(config, "zhihu", "/v2/zhihu", transform)
    return result if isinstance(result, list) else []


async def fetch_bilibili(config: dict) -> list:
    def transform(data: object) -> list:
        items = data if isinstance(data, list) else []
        return [
            {"title": item.get("title", ""), "link": item.get("link", ""), "hot": True, "rank": index + 1}
            for index, item in enumerate(items[:10])
            if isinstance(item, dict)
        ]

    result = await _fetch(config, "bilibili", "/v2/bili", transform)
    return result if isinstance(result, list) else []


async def fetch_itnews(config: dict) -> list:
    def transform(data: object) -> list:
        items = data if isinstance(data, list) else []
        return [item.get("title", "") for item in items[:10] if isinstance(item, dict)]

    result = await _fetch(config, "itNews", "/v2/it-news/rank", transform, params={"type": "day", "limit": "10"})
    return result if isinstance(result, list) else []


async def fetch_douyin(config: dict) -> list:
    def transform(data: object) -> list:
        items = data if isinstance(data, list) else []
        return [
            {
                "title": item.get("title") or "未知标题",
                "hot_value": item.get("hot_value"),
                "cover": item.get("cover", ""),
                "rank": index + 1,
                "hotDisplay": format_hot_value(item.get("hot_value")),
            }
            for index, item in enumerate(items[:10])
            if isinstance(item, dict)
        ]

    result = await _fetch(config, "douyin", "/v2/douyin", transform)
    return result if isinstance(result, list) else []


async def fetch_toutiao(config: dict) -> list:
    def transform(data: object) -> list:
        items = data if isinstance(data, list) else []
        return [
            {
                "title": item.get("title", ""),
                "hotValue": item.get("hot_value") or 0,
                "hotDisplay": format_hot_value(item.get("hot_value")),
                "cover": item.get("cover", ""),
                "link": item.get("link", ""),
                "rank": index + 1,
            }
            for index, item in enumerate(items[:10])
            if isinstance(item, dict)
        ]

    result = await _fetch(config, "toutiao", "/v2/toutiao", transform)
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# 今日新番（Bangumi calendar + 剧集）
# ---------------------------------------------------------------------------
WEEKDAY_CN = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]


def _bangumi_weekday_id(now: datetime) -> int:
    """Bangumi 的 weekday.id 取值是 1(周一) ~ 7(周日)。"""
    return now.weekday() + 1


def _weekday_cn(now: datetime) -> str:
    """datetime.weekday() 周一=0，而 WEEKDAY_CN 以周日开头，所以要 +1 再取模。"""
    return WEEKDAY_CN[(now.weekday() + 1) % 7]


def _safe_float(value: object) -> float:
    """把 API 数值字段转成 float；None / 空串 / 脏数据 / NaN 一律按 0 处理。"""
    if value is None or isinstance(value, bool):
        return 0.0
    num = 0.0
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return 0.0
    return num if math.isfinite(num) else 0.0


def _safe_int(value: object) -> int:
    """把 API 数值字段转成 int；脏数据按 0 处理，避免一条坏数据整块板块渲染失败。"""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _normalize_image(images: object, proxy: str) -> str:
    if not isinstance(images, dict):
        return ""
    raw = ""
    for key in ("large", "common", "medium", "grid", "small"):
        if images.get(key):
            raw = str(images[key])
            break
    if not raw.startswith(("http://", "https://")):
        return raw
    if proxy:
        return raw if raw.startswith(proxy) else proxy + raw
    return raw.replace("http://", "https://", 1)


def _normalize_bangumi_item(item: dict, proxy: str) -> dict:
    display_name = item.get("name_cn") or item.get("name") or "未命名番剧"
    sub_name = ""
    if item.get("name_cn") and item.get("name") and item["name_cn"] != item["name"]:
        sub_name = str(item["name"])
    rating = item.get("rating")
    collection = item.get("collection")
    score = _safe_float(rating.get("score")) if isinstance(rating, dict) else 0.0
    rank = _safe_int(item.get("rank"))
    doing = _safe_int(collection.get("doing")) if isinstance(collection, dict) else 0
    url = str(item.get("url", "") or "").replace("http://", "https://", 1)
    return {
        "id": item.get("id"),
        "displayName": display_name,
        "subName": sub_name,
        "image": _normalize_image(item.get("images"), proxy),
        "scoreText": f"{score:.1f}" if score > 0 else "暂无",
        "rankText": f"Rank {rank}" if rank > 0 else "",
        "doingText": f"{doing / 10000:.1f}万" if doing >= 10000 else str(doing),
        "airDate": item.get("air_date") or "未知",
        "episodeText": "",
        "url": url,
    }


async def fetch_bangumi(config: dict) -> dict:
    from ..utils.api_base import get_proxy_prefix

    proxy = get_proxy_prefix(config, "bangumi")

    async def handler(base: str) -> dict:
        bg_headers = {**HEADERS, "Accept": "application/json", "User-Agent": "bangumi-calendar-plugin/1.0"}
        async with httpx.AsyncClient(timeout=15.0, headers=bg_headers) as client:
            response = await client.get(f"{base}/calendar")

        response.raise_for_status()
        calendar = response.json()
        if isinstance(calendar, list):
            day_list = calendar
        elif isinstance(calendar, dict):
            day_list = calendar.get("value", [])
        else:
            day_list = []
        if not day_list:
            raise ValueError("Bangumi API 返回数据为空")

        now = datetime.now()
        target_weekday = _bangumi_weekday_id(now)
        day = next(
            (d for d in day_list if isinstance(d, dict) and isinstance(d.get("weekday"), dict)
             and _safe_int(d["weekday"].get("id")) == target_weekday),
            None,
        )
        if day is None:
            raise ValueError("没有找到今天的番剧日历数据")

        items = [_normalize_bangumi_item(item, proxy) for item in day.get("items", []) if isinstance(item, dict)]
        bg_image = next((item["image"] for item in items if item["image"]), "")
        weekday_info = day.get("weekday") if isinstance(day.get("weekday"), dict) else {}
        return {
            "weekday": weekday_info,
            "weekdayText": weekday_info.get("cn") or _weekday_cn(now),
            "dateText": now.strftime("%Y年%m月%d日"),
            "generatedAt": now.strftime("%H:%M"),
            "total": len(items),
            "items": items,
            "bgImage": bg_image,
        }

    try:
        result = await request_with_fallback(config, "bangumi", handler)
        return result if isinstance(result, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[芙芙日报] bangumi API 请求失败: {exc}")
        mark_module("bangumi", "degraded", str(exc))
        fallback = _fallback("bangumi")
        return fallback if isinstance(fallback, dict) else {}


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def resolve_side_module(config: dict) -> str:
    return "bilibili" if config.get("sideModule") == "bilibili" else "zhihu"


async def fetch_all_data(config: dict) -> dict:
    logger.info("[芙芙日报] 开始获取固定模块数据（新闻、摸鱼、侧栏、IT）...")
    side = resolve_side_module(config)

    news60s = await fetch_news60s(config)
    moyu = await fetch_moyu(config)
    zhihu: list = []
    bilibili: list = []
    if side == "bilibili":
        bilibili = await fetch_bilibili(config)
    else:
        zhihu = await fetch_zhihu(config)
    it_news = await fetch_itnews(config)

    return {
        "date": get_date_info(),
        "moyuData": moyu,
        "zhihuHot": zhihu,
        "bilibiliHot": bilibili,
        "sideModule": side,
        "worldNews": news60s.get("worldNews", []),
        "itNews": it_news,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def fetch_all_mock_data(config: dict) -> dict:
    news60s = read_mock("news60s")
    news = news60s if isinstance(news60s, dict) else {}
    moyu_raw = read_mock("moyu")
    zhihu = read_mock("zhihu")
    bilibili = read_mock("bilibili")
    it_news = read_mock("itNews")
    douyin = read_mock("douyin")
    toutiao = read_mock("toutiao")
    bangumi = read_mock("bangumi")

    date_info = get_date_info()
    moyu_data = moyu_raw if isinstance(moyu_raw, dict) else {}
    if moyu_data:
        moyu_data = {
            **moyu_data,
            "date": date_info["gregorian"],
            "weekday": f"星期{date_info['weekday']}",
            "lunar": date_info["lunar"],
        }

    return {
        "date": date_info,
        "moyuData": moyu_data,
        "zhihuHot": zhihu if isinstance(zhihu, list) else [],
        "bilibiliHot": bilibili if isinstance(bilibili, list) else [],
        "sideModule": resolve_side_module(config),
        "worldNews": news.get("worldNews", []) if isinstance(news, dict) else [],
        "itNews": it_news if isinstance(it_news, list) else [],
        "douyinHot": douyin if isinstance(douyin, list) else [],
        "toutiaoHot": toutiao if isinstance(toutiao, list) else [],
        "bangumiData": bangumi if isinstance(bangumi, dict) else None,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
