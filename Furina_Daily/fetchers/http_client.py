"""httpx 请求封装：全局串行 + 最小间隔，429 等失败交由上层顺序切换数据源。"""

import time
import asyncio

import httpx

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    ),
}

# 所有数据源请求串行执行，且相邻请求至少间隔该秒数，避免瞬时并发触发上游 429。
MIN_INTERVAL = 0.6
_request_lock = asyncio.Lock()
_last_request_at = 0.0


async def http_get(
    url: str,
    *,
    timeout: float = 12.0,
    accept: str = "application/json, text/plain, */*",
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """串行发起一次 GET；429 等错误直接抛出，由上层按顺序切换下一个数据源。"""
    global _last_request_at
    headers = {**HEADERS, "Accept": accept}
    async with _request_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        _last_request_at = time.monotonic()
    return response
