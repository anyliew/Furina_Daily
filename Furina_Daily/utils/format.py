"""文本与数值格式化工具。"""

import re


def cron_human(expr: object) -> str:
    """把常见的「分 时 日 月 周」Cron 转成人类可读时间；无法识别的原样返回。"""
    raw = str(expr or "").strip()
    parts = raw.split()
    if len(parts) != 5:
        return raw
    minute, hour, day, month, weekday = parts
    if day != "*" or month != "*" or weekday != "*":
        return raw
    if minute.isdigit() and hour.isdigit():
        return f"{int(hour):02d}:{int(minute):02d}"
    if minute.isdigit() and hour == "*":
        return f"每小时第 {int(minute):02d} 分"
    return raw


def format_hot_value(value: object) -> str:
    """把热度值格式化为「1.3w」形式；无效值尽量原样返回。"""
    if value is None or value == "" or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return value
    else:
        return str(value)

    if num != num:  # NaN
        return str(value)
    if num >= 10000:
        return f"{num / 10000:.1f}w"
    if num == int(num):
        return str(int(num))
    return str(num)


# 异常原文里常见的敏感片段：内部接口地址、磁盘绝对路径。
# 命令反馈会发到群里，这些内容必须隐去，避免把部署信息抛给普通用户。
_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:https?|wss?)://\S+"),
    re.compile(r"\bfile://\S+"),
    re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>]*"),
    # POSIX 绝对路径：要求至少两级目录且含字母，避免误伤「开/关」「2026/11/21」这类普通文本
    re.compile(r"(?=[\w./@+-]*[A-Za-z])(?:/[\w.@+-]+){2,}/?"),
)

MASK_TEXT = "<已隐藏>"


def mask_sensitive(text: object) -> str:
    """把文本中的 URL 与绝对路径替换为占位符，用于发往群聊的错误提示。"""
    masked = str(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        masked = pattern.sub(MASK_TEXT, masked)
    return masked
