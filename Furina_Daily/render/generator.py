"""日报渲染核心。

Jinja2 渲染 HTML 模板 -> Playwright chromium 截图 -> PIL 无损压缩 -> bytes。
"""

import io
import base64
import asyncio
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from PIL import Image
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Browser, async_playwright

from gsuid_core.logger import logger

from ..utils.path import FONT_PATH, HTML_PATH, IMAGES_PATH, OUTPUT_PATH
from ..fetchers.fetchers import fetch_douyin, fetch_bangumi, fetch_toutiao, fetch_all_data, fetch_all_mock_data
from ..utils.data_status import reset_stats

THEME_TEMPLATES = {
    "blue": "base.html",
    "pink": "base_pink.html",
    "lu": "base_lu.html",
    "green": "base_green.html",
    "plain": "base_plain.html",
}

LAYOUT_WIDTH = 1408
COMPRESS_FORMATS = {"png", "webp-lossless"}
# 海报热搜可用的模块；配置里出现未知值时按抖音处理
HOT_MODULES = {"douyin", "bangumi", "toutiao"}
# output/ 只保留最近几张成品图：开启定时刷新后每天会生成多张，不清理会持续占盘
OUTPUT_KEEP_FILES = 3


def _resolve_asset(value: object, builtin_dir: Path, default_name: str) -> Path:
    """上传文件优先；未上传回退内置资源；兼容旧配置的相对文件名。"""
    raw = str(value or "").strip()
    if raw:
        candidate = Path(raw)
        # WebConsole 上传后 data 是绝对路径
        if candidate.is_absolute() and candidate.is_file():
            return candidate
        # 旧配置：相对文件名，指向内置资源目录
        if not candidate.is_absolute():
            inner = builtin_dir / candidate.name
            if inner.is_file():
                return inner
    return builtin_dir / default_name


def _font_url(value: object, default_name: str) -> tuple[str, str]:
    """返回 @font-face 的 (src, format)；上传字体用 file:// 绝对路径，避免大体积 base64。"""
    path = _resolve_asset(value, FONT_PATH, default_name)
    fmt = "opentype" if path.suffix.lower() == ".otf" else "truetype"
    raw = str(value or "").strip()
    if raw and Path(raw).is_absolute() and Path(raw).is_file():
        return path.resolve().as_uri(), fmt
    return f"../font/{path.name}", fmt


def normalize_format(value: object) -> str:
    fmt = str(value or "png").lower()
    return fmt if fmt in COMPRESS_FORMATS else "png"


def resolve_compress(config: dict) -> dict:
    return {
        "enabled": bool(config.get("compressImage", False)),
        "format": normalize_format(config.get("compressFormat")),
    }


def _logo_base64(config: dict) -> str:
    path = _resolve_asset(config.get("logoImage"), IMAGES_PATH, "logo.png")
    if not path.is_file():
        return ""
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp" if suffix == ".webp" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _content_scale(config: dict) -> float:
    raw = config.get("contentFontSize")
    try:
        size = float(raw) if raw not in ("", None) else 25.0
    except (TypeError, ValueError):
        size = 25.0
    return (size if size > 0 else 25.0) / 19.0


def _build_template_data(config: dict, base: dict, hot_module: str, hot: object) -> dict:
    """组装模板变量。

    hot_module 是「实际渲染」的模块而非配置值：新番取不到数据时上层会降级成 douyin，
    这里必须跟着降级，否则模板按新番分支渲染而数据是抖音列表，海报区会整块空白。
    """
    title_font, title_fmt = _font_url(config.get("titleFont"), "Title.ttf")
    sec_font, sec_fmt = _font_url(config.get("secondaryTitleFont"), "Secondary_Title.ttf")
    content_font, content_fmt = _font_url(config.get("contentFont"), "Content.ttf")

    bangumi_list: list[object] = []
    if hot_module == "bangumi" and isinstance(hot, dict):
        raw_items = hot.get("items")
        if isinstance(raw_items, list):
            bangumi_list = raw_items[:10]

    return {
        "date": base.get("date", {}),
        "moyuData": base.get("moyuData", {}),
        "zhihuHot": base.get("zhihuHot", []),
        "bilibiliHot": base.get("bilibiliHot", []),
        "sideModule": base.get("sideModule", "zhihu"),
        "worldNews": base.get("worldNews", []),
        "itNews": base.get("itNews", []),
        "generatedAt": base.get("generatedAt", ""),
        "logoBase64": _logo_base64(config),
        "customTitle": config.get("customTitle") or "芙芙心日报",
        "logoSize": config.get("logoSize") or "",
        "titleFont": title_font,
        "titleFontSize": config.get("titleFontSize") or "",
        "secondaryTitleFont": sec_font,
        "secondaryTitleFontSize": config.get("secondaryTitleFontSize") or "35",
        "contentFont": content_font,
        "contentFontSize": config.get("contentFontSize") or "",
        "contentFontScale": _content_scale(config),
        "titleFontFormat": title_fmt,
        "secondaryTitleFontFormat": sec_fmt,
        "contentFontFormat": content_fmt,
        "isBangumi": hot_module == "bangumi",
        "isToutiao": hot_module == "toutiao",
        "douyinHotList": hot if hot_module == "douyin" and isinstance(hot, list) else [],
        "bangumiList": bangumi_list,
        "toutiaoHot": hot if hot_module == "toutiao" and isinstance(hot, list) else [],
    }


def render_html(config: dict, template_data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(HTML_PATH)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    theme = str(config.get("theme") or "blue")
    template_file = THEME_TEMPLATES.get(theme, "base.html")
    template = env.get_template(template_file)
    return template.render(**template_data)


@asynccontextmanager
async def browser_session() -> AsyncIterator[Browser]:
    """复用一个 chromium 进程。

    单张日报用不上，但「日报模拟」要连出五张图，每次重新 launch 会多花几秒；
    传 browser 进 generate_daily 就能复用同一个实例。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            await browser.close()


async def _capture(browser: Browser, url: str) -> bytes:
    """在已启动的浏览器里开一个上下文，全页截图。"""
    context = await browser.new_context(
        viewport={"width": LAYOUT_WIDTH, "height": 1080},
        device_scale_factor=1,
    )
    try:
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=30000)
        # 等待图片与字体就绪
        await page.evaluate(
            """async () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                await Promise.all(imgs.map(img => img.complete ? null :
                    new Promise(res => {
                        img.addEventListener('load', res);
                        img.addEventListener('error', res);
                    })
                ));
                if (document.fonts && document.fonts.ready) await document.fonts.ready;
            }"""
        )
        await page.wait_for_timeout(300)
        return await page.screenshot(type="png", full_page=True)
    finally:
        await context.close()


async def _screenshot(html: str, scale: int, browser: Browser | None = None) -> bytes:
    """把 HTML 写入临时文件并用 chromium 全页截图，返回字节。"""
    base_url = HTML_PATH.resolve().as_uri() + "/"
    scale_style = f"html{{zoom:{scale};}}" if scale > 1 else ""
    final_html = html.replace("<head", f'<head><base href="{base_url}">', 1)
    inject_style = (
        f"<style>{scale_style}body{{width:{LAYOUT_WIDTH}px!important;"
        f"min-width:{LAYOUT_WIDTH}px!important;}}</style>"
    )
    final_html = final_html.replace("</head>", f"{inject_style}</head>", 1)

    temp_html = OUTPUT_PATH / f"_render_{datetime.now().strftime('%H%M%S%f')}.html"
    await asyncio.to_thread(temp_html.write_text, final_html, encoding="utf-8")
    url = temp_html.resolve().as_uri()
    try:
        if browser is not None:
            return await _capture(browser, url)
        async with browser_session() as own_browser:
            return await _capture(own_browser, url)
    finally:
        await asyncio.to_thread(temp_html.unlink, missing_ok=True)


def _compress(png_bytes: bytes, compress: dict) -> bytes:
    """用 PIL 做无损压缩：PNG 最大压缩，或 WebP 无损。"""
    if not compress.get("enabled", False):
        return png_bytes
    with Image.open(io.BytesIO(png_bytes)) as source:
        image = source.convert("RGB")
    out = io.BytesIO()
    fmt = normalize_format(compress.get("format"))
    if fmt == "webp-lossless":
        image.save(out, format="WEBP", lossless=True, quality=100)
    else:
        image.save(out, format="PNG", optimize=True, compress_level=9)
    return out.getvalue()


def _prune_output() -> None:
    """只保留最近 OUTPUT_KEEP_FILES 张成品图。"""
    files = sorted(
        (p for p in OUTPUT_PATH.glob("fufu-*") if p.suffix.lower() in {".png", ".webp"}),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in files[OUTPUT_KEEP_FILES:]:
        old.unlink(missing_ok=True)


def _save_output(result: bytes, config: dict) -> Path:
    """把成品图写入 output/ 并清理旧文件。"""
    compress = resolve_compress(config)
    suffix = "webp" if compress["enabled"] and compress["format"] == "webp-lossless" else "png"
    out_path = OUTPUT_PATH / f"fufu-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.{suffix}"
    out_path.write_bytes(result)
    _prune_output()
    return out_path


# ---------------------------------------------------------------------------
# 海报热搜：按配置取数，取不到就降级
# ---------------------------------------------------------------------------
async def _load_douyin(config: dict, base: dict, use_mock: bool) -> list:
    if use_mock:
        # 模拟模式承诺「不联网」：没有示例数据就返回空，宁可海报区留白也不打上游
        mock_list = base.get("douyinHot")
        return mock_list if isinstance(mock_list, list) else []
    return await fetch_douyin(config)


async def _load_toutiao(config: dict, base: dict, use_mock: bool) -> list:
    if use_mock:
        mock_list = base.get("toutiaoHot")
        return mock_list if isinstance(mock_list, list) else []
    return await fetch_toutiao(config)


async def _load_bangumi(config: dict, base: dict, use_mock: bool) -> dict | None:
    """返回可渲染的新番数据；取不到或为空时返回 None，由调用方回退抖音。"""
    data: object = None
    if use_mock:
        data = base.get("bangumiData")
    else:
        try:
            data = await fetch_bangumi(config)
        except Exception as exc:  # noqa: BLE001 - 新番是可选板块，失败必须能回退
            logger.error(f"[芙芙日报] 今日新番获取失败，回退抖音热搜: {exc}")
            return None
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    return {**data, "items": items[:10]}


async def _resolve_hot(config: dict, base: dict, use_mock: bool) -> tuple[str, object]:
    """按配置加载海报热搜，返回 (实际渲染模块, 数据)。"""
    hot_module = str(config.get("hotModule") or "douyin")
    if hot_module not in HOT_MODULES:
        hot_module = "douyin"

    if hot_module == "bangumi":
        bangumi = await _load_bangumi(config, base, use_mock)
        if bangumi is not None:
            return "bangumi", bangumi
        hot_module = "douyin"

    if hot_module == "toutiao":
        return "toutiao", await _load_toutiao(config, base, use_mock)
    return "douyin", await _load_douyin(config, base, use_mock)


async def generate_daily(config: dict, *, use_mock: bool = False, browser: Browser | None = None) -> bytes:
    """生成日报，返回图片字节。

    browser 由调用方传入时复用同一个 chromium 实例（批量出图用）；
    不传则自己起一个，用完即关。
    """
    if not use_mock:
        reset_stats()

    base = await (fetch_all_mock_data(config) if use_mock else fetch_all_data(config))
    hot_module, hot = await _resolve_hot(config, base, use_mock)

    template_data = await asyncio.to_thread(_build_template_data, config, base, hot_module, hot)
    html = await asyncio.to_thread(render_html, config, template_data)

    scale_raw = config.get("renderScale", 1)
    scale = scale_raw if isinstance(scale_raw, int) and scale_raw > 0 else 1
    png_bytes = await _screenshot(html, scale, browser)

    result = await asyncio.to_thread(_compress, png_bytes, resolve_compress(config))

    if use_mock:
        # 模拟日报只是预览：不落盘、不占今日缓存，反复执行也不会把 output/ 撑大
        logger.info(f"[芙芙日报] 模拟日报已生成（{len(result)} bytes）")
        return result

    out_path = await asyncio.to_thread(_save_output, result, config)
    logger.info(f"[芙芙日报] 日报已生成: {out_path} ({len(result)} bytes)")
    return result
