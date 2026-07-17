import httpx
import pytest

from zephyr_mcp.client import (
    ZephyrError,
    _chunk,
    _escape_jql,
    _filter_by_folder_prefix,
    _handle_response,
    _retry_delay,
)


def test_filter_by_folder_prefix_none_returns_all():
    items = [{"folder": "/Турбо/Портал"}, {"folder": "/1. Пользователь"}]
    assert _filter_by_folder_prefix(items, None) == items


def test_filter_by_folder_prefix_matches_subfolders():
    items = [
        {"folder": "/Турбо/Портал/Ядро/Аутентификация"},
        {"folder": "/Турбо/Сайт/Метрики"},
        {"folder": "/1. Пользователь/Авторизация"},
    ]
    result = _filter_by_folder_prefix(items, "/Турбо")
    assert result == items[:2]


def test_filter_by_folder_prefix_matches_exact_leaf():
    items = [{"folder": "/6. Разовые задачи"}, {"folder": "/Другое"}]
    result = _filter_by_folder_prefix(items, "/6. Разовые задачи")
    assert result == items[:1]


def test_filter_by_folder_prefix_does_not_match_sibling_with_shared_prefix():
    items = [{"folder": "/Турбо/Портал"}, {"folder": "/ТурбоЛайт/Что-то"}]
    result = _filter_by_folder_prefix(items, "/Турбо")
    assert result == items[:1]


def test_filter_by_folder_prefix_ignores_items_without_folder():
    items = [{"folder": "/Турбо/Портал"}, {"testCaseKey": "X-1"}]
    result = _filter_by_folder_prefix(items, "/Турбо")
    assert result == items[:1]


def test_filter_by_folder_prefix_strips_trailing_slash():
    items = [{"folder": "/Турбо/Портал"}]
    result = _filter_by_folder_prefix(items, "/Турбо/")
    assert result == items


def _response(status_code: int, *, json=None, text: str = "", headers: dict | None = None) -> httpx.Response:
    content = httpx.Response(status_code, json=json).content if json is not None else text.encode()
    return httpx.Response(status_code, content=content, headers=headers or {})


def test_handle_response_401_raises_readable_error():
    with pytest.raises(ZephyrError, match="bad credentials"):
        _handle_response(_response(401), "/testcase/X-1")


def test_handle_response_403_raises_readable_error():
    with pytest.raises(ZephyrError, match="bad credentials"):
        _handle_response(_response(403), "/testcase/X-1")


def test_handle_response_404_includes_path():
    with pytest.raises(ZephyrError, match="not found: /testcase/X-1"):
        _handle_response(_response(404), "/testcase/X-1")


def test_handle_response_429_raises_readable_error():
    with pytest.raises(ZephyrError, match="rate limited"):
        _handle_response(_response(429), "/testcase/search")


def test_handle_response_generic_5xx_includes_status_and_body_snippet():
    with pytest.raises(ZephyrError, match="zephyr API error 500"):
        _handle_response(_response(500, text="internal server error"), "/testrun/X/testresults")


def test_handle_response_200_returns_parsed_json():
    result = _handle_response(_response(200, json={"key": "CLOUDDEV-T853"}), "/testcase/CLOUDDEV-T853")
    assert result == {"key": "CLOUDDEV-T853"}


def test_handle_response_200_invalid_json_raises_readable_error():
    with pytest.raises(ZephyrError, match="invalid JSON"):
        _handle_response(_response(200, text="not json"), "/testcase/X-1")


def test_retry_delay_honors_retry_after_header():
    resp = _response(429, headers={"Retry-After": "3"})
    assert _retry_delay(resp, attempt=0) == 3.0


def test_retry_delay_falls_back_to_backoff_schedule_on_invalid_header():
    resp = _response(429, headers={"Retry-After": "not-a-number"})
    delay = _retry_delay(resp, attempt=0)
    assert 0.5 <= delay <= 0.75


def test_retry_delay_falls_back_to_backoff_schedule_without_header():
    resp = _response(429)
    delay = _retry_delay(resp, attempt=1)
    assert 1.0 <= delay <= 1.25


def test_escape_jql_escapes_quotes_and_backslashes():
    assert _escape_jql('say "hi"') == 'say \\"hi\\"'
    assert _escape_jql("back\\slash") == "back\\\\slash"


def test_chunk_splits_into_even_batches():
    items = [str(i) for i in range(10)]
    result = _chunk(items, 3)
    assert result == [["0", "1", "2"], ["3", "4", "5"], ["6", "7", "8"], ["9"]]


def test_chunk_single_batch_when_smaller_than_size():
    items = ["a", "b"]
    assert _chunk(items, 100) == [["a", "b"]]


def test_chunk_empty_list_returns_empty():
    assert _chunk([], 100) == []
