"""Furina_Daily 帮助：注册到全局帮助一览 + 实时生成帮助图指令。"""

from PIL import Image

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

from ..utils.path import ICON_PATH
from ..render.help_render import get_help_image

sv_help = SV("芙芙日报帮助", pm=6, area="ALL")


@sv_help.on_fullmatch("日报帮助", block=True)
async def send_help(bot: Bot, ev: Event) -> None:
    await bot.send(await get_help_image())


# copy() 会先把像素读进内存，随后关掉文件句柄：
# 直接把 Image.open() 的懒加载对象交出去，Windows 上会长期占用 ICON.png 无法替换。
with Image.open(ICON_PATH) as _icon:
    register_help(
        "Furina_Daily",
        "日报帮助",
        _icon.copy(),
    )
