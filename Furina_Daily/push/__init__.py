"""定时任务子包：框架启动后注册推送与缓存刷新任务。"""

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start

from .scheduler import setup_jobs


@on_core_start
async def _start_furina_jobs() -> None:
    try:
        await setup_jobs()
    except Exception as exc:  # noqa: BLE001 - 钩子异常会被框架吞掉，这里必须自己记日志
        logger.error(f"[芙芙日报] 注册定时任务失败: {exc}")
