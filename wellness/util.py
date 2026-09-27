"""共享的时间工具与业务异常。"""

import re
from datetime import datetime, timezone


class DomainError(Exception):
    """业务规则冲突，携带对应的 HTTP 状态码。"""

    status = 400


class NotFound(DomainError):
    status = 404


class Forbidden(DomainError):
    status = 403


def now_iso():
    """当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(text):
    """把 ISO 字符串解析为带时区的时间，日期按当日零点处理。

    查询串里的时区加号会被解码为空格，这里把 T 之后的时区空格还原为加号。
    """
    if "T" in text:
        text = re.sub(r" (\d{2}:\d{2}(:\d{2})?)$", r"+\1", text)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def in_window(at, start, end):
    """判断时间点是否落在 [start, end] 生效窗口内，空端表示不限。"""
    moment = parse_time(at)
    if start and moment < parse_time(start):
        return False
    if end and moment > parse_time(end):
        return False
    return True
