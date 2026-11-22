"""帮助菜单出图。"""

import html
import base64
from datetime import datetime

from playwright.async_api import async_playwright

from gsuid_core.logger import logger

from ..utils.path import CACHE_PATH, IMAGES_PATH

MODULE_DATA = [
    (
        "基础指令",
        "获取日报及刷新",
        [
            ("日报 / 芙芙日报", "手动获取今天的最新日报（智能缓存）"),
            ("今日日报 / 今日头条", "同上，快捷获取日报"),
            ("刷新日报", "强制刷新日报，重新抓取最新数据（仅主人）"),
        ],
    ),
    (
        "订阅管理",
        "控制日报自动推送（仅主人）",
        [
            ("开启日报推送", "将本群加入推送列表（推送对象以订阅表为准）"),
            ("关闭日报推送", "将本群从推送列表中移除"),
        ],
    ),
    (
        "主题与板块",
        "切换主题、海报热搜或侧栏话题（仅主人）",
        [
            ("日报主题切换 素白简讯", "纯白极简（默认）"),
            ("日报主题切换 芙芙蓝色", "芙芙蓝色"),
            ("日报主题切换 真寻粉色", "真寻粉色"),
            ("日报主题切换 霜笺白鹭", "霜笺白鹭"),
            ("日报主题切换 绿野青穗", "绿野青穗"),
            ("海报切换抖音热搜", "海报区切换为抖音热搜"),
            ("海报切换今日新番", "海报区切换为今日新番"),
            ("海报切换头条热搜", "海报区切换为头条热搜"),
            ("侧栏切换知乎话题榜", "侧栏显示知乎话题榜"),
            ("侧栏切换B站热搜", "侧栏显示哔哩哔哩热搜"),
        ],
    ),
    (
        "图片与数据",
        "压缩开关、渲染预览与数据源（仅主人）",
        [
            ("日报压缩 开/关", "开启或关闭日报图片压缩，不带参数则切换"),
            ("日报模拟", "用本地示例数据按五套主题各出一份，预览渲染效果（不联网、不落盘）"),
            ("日报数据源", "查看各模块当前实际使用的 API 地址（仅主人）"),
        ],
    ),
    (
        "高级指令",
        "帮助与配置",
        [
            ("日报帮助", "显示本帮助菜单（实时生成）"),
            ("日报清空配置", "备份当前配置并恢复默认设置；不会退订推送（仅主人）"),
        ],
    ),
]

def _logo_base64() -> str:
    path = IMAGES_PATH / "furina.png"
    if not path.is_file():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _build_help_html() -> str:
    logo = _logo_base64()
    logo_img = (
        f'<img src="{logo}" style="height:28px;width:28px;vertical-align:middle;margin-right:8px;">'
        if logo
        else "📰"
    )
    modules_html = []
    for name, desc, commands in MODULE_DATA:
        command_html = "".join(
            f'<div class="command-item"><code>{html.escape(cmd)}</code><span>{html.escape(text)}</span></div>'
            for cmd, text in commands
        )
        modules_html.append(
            f'<div class="module-card"><div class="module-header"><h3>{html.escape(name)}</h3>'
            f"<p>{html.escape(desc)}</p></div><div class=\"command-list\">{command_html}</div></div>"
        )
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:"Microsoft YaHei",sans-serif;background:#f6f8fa;color:#1f2328;
display:flex;justify-content:center;padding:20px;}}
.container{{width:600px;background:#fff;border:1px solid #d0d7de;border-radius:12px;overflow:hidden;}}
.header{{padding:16px 20px;border-bottom:1px solid #d0d7de;background:#f6f8fa;font-size:24px;font-weight:600;}}
.content{{padding:16px 20px 8px;}}
.module-card{{background:#fff;border:1px solid #d0d7de;border-radius:12px;padding:16px;margin-bottom:16px;}}
.module-header{{margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #d8dee4;}}
.module-header h3{{font-size:18px;font-weight:600;margin-bottom:4px;}}
.module-header p{{font-size:13px;color:#656d76;}}
.command-list{{display:flex;flex-direction:column;gap:10px;}}
.command-item{{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;}}
.command-item code{{background:#f6f8fa;border:1px solid #d0d7de;padding:3px 10px;
border-radius:20px;font-size:13px;font-weight:600;color:#0969da;white-space:nowrap;}}
.command-item span{{flex:1;font-size:13px;color:#57606a;line-height:1.5;}}
.footer{{padding:12px 20px;border-top:1px solid #d0d7de;text-align:center;
font-size:12px;color:#656d76;background:#f6f8fa;}}
</style></head><body><div class="container">
<div class="header">{logo_img} 芙芙日报</div>
<div class="content">{''.join(modules_html)}</div>
<div class="footer">Power By GsCore & Created By Furina_Daily<br>生成时间: {now}</div>
</div></body></html>"""


async def _render(html_text: str) -> bytes:
    temp = CACHE_PATH / "_help.html"
    temp.write_text(html_text, encoding="utf-8")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 640, "height": 900}, device_scale_factor=2)
                await page.goto(temp.resolve().as_uri(), wait_until="load", timeout=30000)
                await page.wait_for_timeout(200)
                return await page.screenshot(type="png", full_page=True)
            finally:
                await browser.close()
    finally:
        temp.unlink(missing_ok=True)


async def get_help_image() -> bytes | str:
    """实时生成并返回帮助图（不缓存）。"""
    try:
        return await _render(_build_help_html())
    except Exception as exc:  # noqa: BLE001 - 帮助图失败降级为文本提示
        logger.error(f"[芙芙日报] 生成帮助图失败: {exc}")
        return (
            "芙芙日报 · 命令一览\n"
            "日报 / 芙芙日报 / 今日日报 / 今日头条：获取日报；刷新日报：强制刷新（主人）\n"
            "开启/关闭日报推送：订阅管理（主人）\n"
            "日报主题切换 <主题>：素白简讯/芙芙蓝色/真寻粉色/霜笺白鹭/绿野青穗（主人）\n"
            "海报切换抖音热搜/今日新番/头条热搜：切换海报热搜（主人）\n"
            "侧栏切换知乎话题榜/B站热搜：切换侧栏（主人）\n"
            "日报压缩 开/关、日报模拟、日报数据源（主人）、日报帮助（实时生成）、日报清空配置"
        )
