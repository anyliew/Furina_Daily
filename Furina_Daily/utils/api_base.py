"""数据源基址解析与失败回退。

每个模块可单独配置地址；请求失败时按「用户自定义 > 上次成功 > 通用基址 > 内置池」顺序回退。
"""

from collections.abc import Callable, Awaitable

from gsuid_core.logger import logger

from .data_status import mark_module

BILI_POOL = [
    "https://60s.7se.cn",
    "https://60s.mizhoubaobei.top",
    "https://60s.crystelf.top",
    "https://api.elysiayanyu.top",
    "https://api.cczo.cc/60s",
    "https://60s.superjeason.qzz.io",
]

VIKI_POOL = [
    "https://60s.viki.moe",
    "https://60s.7se.cn",
    "https://60s.crystelf.top",
    "https://api.elysiayanyu.top",
    "https://api.cczo.cc/60s",
]

POOLS: dict[str, list[str]] = {
    "news60s": VIKI_POOL,
    "moyu": VIKI_POOL,
    "zhihu": VIKI_POOL,
    "itNews": VIKI_POOL,
    "douyin": VIKI_POOL,
    "toutiao": VIKI_POOL,
    "bilibili": BILI_POOL,
    "bangumi": ["https://api.bgm.tv"],
}

DEFAULTS: dict[str, str] = {
    "news60s": "https://60s.viki.moe",
    "moyu": "https://60s.viki.moe",
    "zhihu": "https://60s.viki.moe",
    "itNews": "https://60s.viki.moe",
    "douyin": "https://60s.viki.moe",
    "toutiao": "https://60s.viki.moe",
    "bilibili": "https://60s.7se.cn",
    "bangumi": "https://api.bgm.tv",
}

API_MODULES = list(DEFAULTS.keys())

# 进程内缓存：模块 -> 最近一次成功的地址
_success_cache: dict[str, str] = {}


def _trim_slash(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def get_proxy_prefix(config: dict, module: str) -> str:
    """仅 bangumi 支持前缀式代理；留空或非法值返回空串。"""
    if module != "bangumi":
        return ""
    raw = config.get("proxy", {}).get("bangumi", "")
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value.startswith(("http://", "https://")):
        return ""
    return value if value.endswith("/") else value + "/"


def get_api_bases(config: dict, module: str) -> list[str]:
    """返回某模块的候选基址，按优先级去重。"""
    proxy = get_proxy_prefix(config, module)
    api_base = config.get("apiBase", {})
    custom = _trim_slash(api_base.get(module, ""))
    generic = _trim_slash(api_base.get("viki", ""))
    cached = _success_cache.get(module, "")

    if proxy:
        target = custom or DEFAULTS.get(module, DEFAULTS["news60s"])
        return [target if target.startswith(proxy) else proxy + target]

    bases: list[str] = []
    if custom:
        bases.append(custom)
    if cached:
        bases.append(cached)
    if generic and module not in ("bangumi", "bilibili"):
        bases.append(generic)
    bases.append(DEFAULTS.get(module, DEFAULTS["news60s"]))
    bases.extend(_trim_slash(b) for b in POOLS.get(module, VIKI_POOL))

    seen: set[str] = set()
    result: list[str] = []
    for base in bases:
        if base and base not in seen:
            seen.add(base)
            result.append(base)
    return result


def get_current_base(config: dict, module: str) -> str:
    bases = get_api_bases(config, module)
    return bases[0] if bases else ""


def remember_base(module: str, base: str) -> None:
    if base:
        _success_cache[module] = _trim_slash(base)


async def request_with_fallback(
    config: dict,
    module: str,
    handler: Callable[[str], Awaitable[object]],
) -> object:
    """依次尝试候选地址，handler 为协程函数；全部失败抛出最后一个异常。"""
    bases = get_api_bases(config, module)
    last_error: Exception | None = None
    for base in bases:
        try:
            result = await handler(base)
            remember_base(module, base)
            mark_module(module, "ok")
            return result
        except Exception as exc:  # noqa: BLE001 - 多数据源逐个尝试，需吞掉单次失败
            last_error = exc
            logger.debug(f"[芙芙日报] {module} 数据源 {base} 请求失败: {exc}")
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{module} 没有可用数据源")
