"""Furina_Daily 配置项定义。"""

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsDivider,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsImageConfig,
    GsListStrConfig,
    GsFileUploadConfig,
)

from ..utils.path import CUSTOM_PATH

# 主题的展示名（WebConsole 下拉 / 命令）与渲染模板 key 的映射。
# WebConsole 选项与落库值用中文展示名；读取配置时统一归一化为英文 key 供渲染层使用。
THEME_ENTRIES = [
    ("素白简讯", "plain"),
    ("芙芙蓝色", "blue"),
    ("真寻粉色", "pink"),
    ("霜笺白鹭", "lu"),
    ("绿野青穗", "green"),
]

# 海报热搜板块：中文展示名 -> 内部模块 key
HOT_ENTRIES = [
    ("抖音热搜", "douyin"),
    ("今日新番", "bangumi"),
    ("头条热搜", "toutiao"),
]

# 侧栏话题模块：中文展示名 -> 内部模块 key
SIDE_ENTRIES = [
    ("知乎话题榜", "zhihu"),
    ("哔哩哔哩热搜", "bilibili"),
]

THEME_LABELS = [label for label, _ in THEME_ENTRIES]
HOT_LABELS = [label for label, _ in HOT_ENTRIES]
SIDE_LABELS = [label for label, _ in SIDE_ENTRIES]

# 默认项一律从 ENTRY 表派生，避免「默认值写死在表外、改表时漏改」导致默认值不在 options 里。
DEFAULT_THEME_CODE = "plain"
DEFAULT_HOT_CODE = "douyin"
DEFAULT_SIDE_CODE = "bilibili"

DEFAULT_THEME_LABEL = {code: label for label, code in THEME_ENTRIES}[DEFAULT_THEME_CODE]
DEFAULT_HOT_LABEL = {code: label for label, code in HOT_ENTRIES}[DEFAULT_HOT_CODE]
DEFAULT_SIDE_LABEL = {code: label for label, code in SIDE_ENTRIES}[DEFAULT_SIDE_CODE]

# 默认推送时间：两条 Cron；至少一个，可在 WebConsole 自行增删。
PUSH_TIMES_DEFAULT = ["0 10 * * *", "0 22 * * *"]

# 默认缓存刷新时间：到点后台预生成日报并刷新今日缓存，用户随时取图都能命中缓存。
# 至少一个，可在 WebConsole 自行增删。
REFRESH_TIMES_DEFAULT = [
    "30 7 * * *",
    "30 9 * * *",
    "30 17 * * *",
    "0 21 * * *",
]


def _normalize(entries: list[tuple[str, str]], value: object, default_code: str) -> str:
    """把配置里的展示名或旧英文 code 归一化为内部模块 key。"""
    label_to_code = dict(entries)
    codes = set(label_to_code.values())
    if isinstance(value, str):
        if value in label_to_code:
            return label_to_code[value]
        if value in codes:
            return value
    return default_code


def _label_for(entries: list[tuple[str, str]], value: object) -> str | None:
    """把旧英文 code 转成中文展示名；已是展示名或未知值返回 None。"""
    code_to_label = {code: label for label, code in entries}
    if isinstance(value, str) and value in code_to_label:
        return code_to_label[value]
    return None


def normalize_theme(value: object) -> str:
    return _normalize(THEME_ENTRIES, value, DEFAULT_THEME_CODE)


def normalize_hot(value: object) -> str:
    return _normalize(HOT_ENTRIES, value, DEFAULT_HOT_CODE)


def normalize_side(value: object) -> str:
    return _normalize(SIDE_ENTRIES, value, DEFAULT_SIDE_CODE)


def theme_label_for(value: object) -> str | None:
    return _label_for(THEME_ENTRIES, value)


def hot_label_for(value: object) -> str | None:
    return _label_for(HOT_ENTRIES, value)


def side_label_for(value: object) -> str | None:
    return _label_for(SIDE_ENTRIES, value)


CONFIG_DEFAULT: Dict[str, GSC] = {
    "_div_push": GsDivider("推送配置", ""),
    # 推送群的唯一数据源：命令与 WebConsole 都写这里，订阅表由 sync_subscriptions 对齐成镜像。
    "reportGroup": GsListStrConfig(
        title="日报推送群",
        desc="接收日报推送的群号列表（字符串）。群内命令「开启/关闭日报推送」会自动维护；"
        "也可在 WebConsole 手动增删，改动 1 分钟内自动同步订阅表（新增补订、移除退订）。"
        "留空表示当前没有群订阅。",
        data=[],
    ),
    "pushTimes": GsListStrConfig(
        title="推送时间",
        desc="Cron 表达式（分 时 日 月 周）。至少一个，可自行添加更多；默认每天 10:00、22:00。",
        data=list(PUSH_TIMES_DEFAULT),
    ),
    "refreshTimes": GsListStrConfig(
        title="刷新时间",
        desc="Cron 表达式（分 时 日 月 周）。到点自动预生成日报并刷新今日缓存，用户随时取图都能秒回；"
        "至少一个，可自行增删；默认 07:30、09:30、17:30、21:00。",
        data=list(REFRESH_TIMES_DEFAULT),
    ),
    "_div_appearance": GsDivider("外观配置", ""),
    # 整体
    "theme": GsStrConfig(
        title="配色主题",
        desc="选择配色主题；旧配置若填英文 code 仍自动兼容。",
        data=DEFAULT_THEME_LABEL,
        options=THEME_LABELS,
    ),
    "customTitle": GsStrConfig(
        title="自定义标题",
        desc="日报顶部的标题文字。",
        data="芙芙心日报",
    ),
    # 上传资源（Logo 与三套字体）
    "logoImage": GsImageConfig(
        title="LOGO图片",
        desc="上传自定义 Logo（png/jpg/webp）；留空则使用内置 Logo。",
        data=str(CUSTOM_PATH / "logo.png"),
        upload_to=str(CUSTOM_PATH),
        filename="logo",
        suffix="png",
    ),
    "titleFont": GsFileUploadConfig(
        title="标题字体",
        desc="上传 ttf/otf 字体文件；留空则使用内置字体。",
        data=str(CUSTOM_PATH / "Title.ttf"),
        upload_to=str(CUSTOM_PATH),
        filename="Title",
        suffix="ttf",
    ),
    "secondaryTitleFont": GsFileUploadConfig(
        title="模块标题字体",
        desc="上传 ttf/otf 字体文件；留空则使用内置字体。",
        data=str(CUSTOM_PATH / "Secondary_Title.ttf"),
        upload_to=str(CUSTOM_PATH),
        filename="Secondary_Title",
        suffix="ttf",
    ),
    "contentFont": GsFileUploadConfig(
        title="正文字体",
        desc="上传 ttf/otf 字体文件；留空则使用内置字体。",
        data=str(CUSTOM_PATH / "Content.ttf"),
        upload_to=str(CUSTOM_PATH),
        filename="Content",
        suffix="ttf",
    ),
    # 尺寸数值
    "logoSize": GsStrConfig(title="Logo 尺寸", desc="留空为原始尺寸。", data=""),
    "titleFontSize": GsStrConfig(title="标题字号", desc="留空为默认。", data=""),
    "secondaryTitleFontSize": GsStrConfig(title="模块标题字号", desc="留空默认 35。", data=""),
    "contentFontSize": GsStrConfig(title="正文字号基准", desc="留空按默认 25 换算。", data=""),
    # 渲染
    "renderScale": GsIntConfig(
        title="渲染倍率",
        desc="像素倍率，1 为默认；越大越清晰、体积越大。",
        data=1,
        max_value=4,
    ),
    "_div_content": GsDivider("内容配置", ""),
    "hotModule": GsStrConfig(
        title="海报热搜",
        desc="选择下方海报区的热搜来源。",
        data=DEFAULT_HOT_LABEL,
        options=HOT_LABELS,
    ),
    "sideModule": GsStrConfig(
        title="侧栏话题模块",
        desc="选择侧栏的话题来源。",
        data=DEFAULT_SIDE_LABEL,
        options=SIDE_LABELS,
    ),
    "_div_image": GsDivider("图片配置", ""),
    "compressImage": GsBoolConfig(title="压缩图片", desc="默认关闭，原图直出；开启可减小体积。", data=False),
    "compressFormat": GsStrConfig(
        title="压缩格式",
        desc="png 无损 / webp-lossless WebP 无损（体积更小）。",
        data="png",
        options=["png", "webp-lossless"],
    ),
    "_div_api": GsDivider("数据源配置", ""),
    "api_viki": GsStrConfig(title="60s 通用地址", desc="留空使用内置公共实例。", data=""),
    "api_news60s": GsStrConfig(title="新闻 API 地址", desc="留空使用通用地址。", data=""),
    "api_moyu": GsStrConfig(title="摸鱼日历 API 地址", desc="留空使用通用地址。", data=""),
    "api_zhihu": GsStrConfig(title="知乎 API 地址", desc="留空使用通用地址。", data=""),
    "api_itNews": GsStrConfig(title="IT 资讯 API 地址", desc="留空使用通用地址。", data=""),
    "api_douyin": GsStrConfig(title="抖音 API 地址", desc="留空使用通用地址。", data=""),
    "api_toutiao": GsStrConfig(title="头条 API 地址", desc="留空使用通用地址。", data=""),
    "api_bilibili": GsStrConfig(title="B站 API 地址", desc="留空使用内置实例。", data="https://60s.7se.cn"),
    "api_bangumi": GsStrConfig(title="新番 API 地址", desc="留空使用 api.bgm.tv。", data="https://api.bgm.tv"),
    "proxy_bangumi": GsStrConfig(
        title="新番代理前缀",
        desc="留空直连；国内访问不了 Bangumi 时填前缀式代理地址。",
        data="",
    ),
}
