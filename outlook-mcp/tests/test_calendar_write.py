from zoneinfo import ZoneInfo

import pytest
from exchangelib import EWSDate, EWSDateTime

from outlook_mcp.calendar_write import parse_date_only, parse_datetime
from outlook_mcp.config import Config
from outlook_mcp.errors import InvalidArgumentError


def make_config(timezone="Europe/Moscow", email="me@example.com"):
    cfg = Config()
    cfg.timezone = timezone
    cfg.ews_email = email
    return cfg


def test_parse_datetime_naive_uses_config_timezone():
    result = parse_datetime("2026-08-20T15:00", "start", make_config())
    assert isinstance(result, EWSDateTime)
    assert (result.hour, result.minute) == (15, 0)
    assert result.utcoffset().total_seconds() == 3 * 3600


def test_parse_datetime_with_offset_is_respected():
    result = parse_datetime("2026-08-20T15:00:00+05:00", "start", make_config())
    # 15:00 +05:00 — это 10:00 UTC; момент времени сохраняется
    assert result.astimezone(ZoneInfo("UTC")).hour == 10


def test_parse_datetime_with_z_suffix():
    result = parse_datetime("2026-08-20T12:00:00Z", "start", make_config())
    assert result.astimezone(ZoneInfo("UTC")).hour == 12


def test_parse_datetime_invalid_raises_invalid_argument():
    with pytest.raises(InvalidArgumentError) as excinfo:
        parse_datetime("not-a-time", "start", make_config())
    assert "start" in str(excinfo.value)


def test_parse_datetime_date_only_raises():
    # Дата без времени для обычной встречи — ошибка; для all-day есть all_day=True
    with pytest.raises(InvalidArgumentError) as excinfo:
        parse_datetime("2026-08-20", "start", make_config())
    assert "all_day" in str(excinfo.value)


def test_parse_datetime_accepts_space_separated_time():
    result = parse_datetime("2026-08-20 15:00", "start", make_config())
    assert (result.hour, result.minute) == (15, 0)


def test_parse_datetime_accepts_explicit_midnight():
    result = parse_datetime("2026-08-20T00:00", "start", make_config())
    assert (result.hour, result.minute) == (0, 0)


def test_parse_date_only_accepts_date():
    assert parse_date_only("2026-08-20", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_accepts_datetime_string():
    # all_day=True: время игнорируется, берётся только календарный день
    assert parse_date_only("2026-08-20T15:00", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_invalid_raises():
    with pytest.raises(InvalidArgumentError):
        parse_date_only("20.08.2026", "start")
