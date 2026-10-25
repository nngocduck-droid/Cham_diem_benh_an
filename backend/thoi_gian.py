"""Giờ Việt Nam (UTC+7). Máy chủ Railway chạy giờ UTC nên không dùng datetime.now() trực tiếp."""
from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7), "ICT")


def now_vn() -> datetime:
    return datetime.now(VN_TZ).replace(tzinfo=None)
