"""日期与农历工具（基于 lunar-python）。"""

from datetime import datetime

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def get_date_info(now: datetime | None = None) -> dict:
    """返回日报模板需要的日期信息：星期、公历、农历。"""
    now = now or datetime.now()
    weekday = WEEKDAYS[now.weekday()]  # Monday=0 -> 一
    gregorian = now.strftime("%Y-%m-%d")
    lunar_text = _lunar_text(now)
    return {
        "weekday": weekday,
        "weekdayShort": weekday,
        "gregorian": gregorian,
        "lunar": lunar_text,
    }


def _lunar_text(day: datetime) -> str:
    # lunar-python 的 Lunar.fromDate 需要 datetime（内部读取时分秒），
    # 传 date 会抛 AttributeError；与上游 date.js 的 Lunar.fromDate(now) 对齐。
    from lunar_python import Lunar

    lunar = Lunar.fromDate(day)
    return f"{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"


WEEKDAY_EN_CN = {
    "monday": "星期一",
    "tuesday": "星期二",
    "wednesday": "星期三",
    "thursday": "星期四",
    "friday": "星期五",
    "saturday": "星期六",
    "sunday": "星期日",
}


def weekday_en_to_cn(value: str) -> str:
    """把英文星期名转成中文；其他状态（如节日名）原样返回。"""
    return WEEKDAY_EN_CN.get(value.strip().lower(), value)
