"""Furina_Daily 资源 / 运行时路径常量集中定义。"""

from pathlib import Path

from gsuid_core.data_store import get_res_path

# 内层包目录：Furina_Daily/Furina_Daily/
PACKAGE_PATH = Path(__file__).resolve().parents[1]
# 外层插件目录：Furina_Daily/
PLUGIN_PATH = Path(__file__).resolve().parents[2]

# 只读静态资源（随插件分发，不写盘）
RESOURCE_PATH = PACKAGE_PATH / "resource"
HTML_PATH = RESOURCE_PATH / "html"
FONT_PATH = RESOURCE_PATH / "font"
IMAGES_PATH = RESOURCE_PATH / "images"
SVG_PATH = RESOURCE_PATH / "svg"
MOCK_DIR = RESOURCE_PATH / "mock"
MOCK_IMAGE_PATH = MOCK_DIR / "images"

ICON_PATH = PLUGIN_PATH / "ICON.png"

# 运行时数据（data/Furina_Daily/），严禁写进插件目录
DATA_PATH = get_res_path("Furina_Daily")
CONFIG_PATH = DATA_PATH / "config.json"
OUTPUT_PATH = DATA_PATH / "output"
CACHE_PATH = DATA_PATH / "cache"
# WebConsole 上传的自定义 Logo / 字体落盘目录（运行时数据，不写进插件目录）
CUSTOM_PATH = DATA_PATH / "custom"

for _p in (DATA_PATH, OUTPUT_PATH, CACHE_PATH, CUSTOM_PATH):
    _p.mkdir(parents=True, exist_ok=True)
