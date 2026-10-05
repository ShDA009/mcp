import ssl

import httpx
import pytest

from zephyr_mcp.client import (
    ZephyrClient,
    ZephyrError,
    _build_execution_result_payload,
    _build_test_case_payload,
    _build_test_case_update_payload,
    _build_test_run_payload,
    _build_verify,
    _chunk,
    _dedup_keys,
    _escape_jql,
    _filter_by_folder_prefix,
    _handle_response,
    _retry_delay,
)
from zephyr_mcp.config import Config


def test_build_verify_without_bundle_returns_true():
    assert _build_verify("") is True


_TEST_CA_PEM = """\
-----BEGIN CERTIFICATE-----
MIIBeTCCAR+gAwIBAgIUGEiq+Jsq0TPy2mwlzyxopTRJuFEwCgYIKoZIzj0EAwIw
EjEQMA4GA1UEAwwHdGVzdC1jYTAeFw0yNjEwMDUwODE2NDZaFw0zNjEwMDIwODE2
NDZaMBIxEDAOBgNVBAMMB3Rlc3QtY2EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAQAYo5brUo4UyK4/ndHuvj0lMB3NXuY5ppshjwmSWNDLxSEMwfaqtYhszdNo61G
A6VlZmLi6ixrEK2OJVjWuQCHo1MwUTAdBgNVHQ4EFgQUX58dkMffTJb7VqhzR4aZ
+6FEl3MwHwYDVR0jBBgwFoAUX58dkMffTJb7VqhzR4aZ+6FEl3MwDwYDVR0TAQH/
BAUwAwEB/zAKBggqhkjOPQQDAgNIADBFAiEA903sqcXMhm2xWLykeCBM5QC5ipof
NuK+4wVV16b7THcCIGeLmi+zbJZaR0UpP+QDyokzfDo3laZaUCTWEmTjPCJh
-----END CERTIFICATE-----
"""


def test_build_verify_with_bundle_drops_strict_flag(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text(_TEST_CA_PEM)
    context = _build_verify(str(ca))
    assert isinstance(context, ssl.SSLContext)
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_filter_by_folder_prefix_none_returns_all():
    items = [{"folder": "/Папка/Подпапка"}, {"folder": "/1. Пользователь"}]
    assert _filter_by_folder_prefix(items, None) == items


def test_filter_by_folder_prefix_matches_subfolders():
    items = [
        {"folder": "/Папка/Подпапка/Ядро/Аутентификация"},
        {"folder": "/Папка/Раздел/Метрики"},
        {"folder": "/1. Пользователь/Авторизация"},
    ]
    result = _filter_by_folder_prefix(items, "/Папка")
    assert result == items[:2]


def test_filter_by_folder_prefix_matches_exact_leaf():
    items = [{"folder": "/6. Разовые задачи"}, {"folder": "/Другое"}]
    result = _filter_by_folder_prefix(items, "/6. Разовые задачи")
    assert result == items[:1]


def test_filter_by_folder_prefix_does_not_match_sibling_with_shared_prefix():
    items = [{"folder": "/Папка/Подпапка"}, {"folder": "/ПапкаЛайт/Что-то"}]
    result = _filter_by_folder_prefix(items, "/Папка")
    assert result == items[:1]


def test_filter_by_folder_prefix_ignores_items_without_folder():
    items = [{"folder": "/Папка/Подпапка"}, {"testCaseKey": "X-1"}]
    result = _filter_by_folder_prefix(items, "/Папка")
    assert result == items[:1]


def test_filter_by_folder_prefix_strips_trailing_slash():
    items = [{"folder": "/Папка/Подпапка"}]
    result = _filter_by_folder_prefix(items, "/Папка/")
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
    result = _handle_response(_response(200, json={"key": "PROJ-T853"}), "/testcase/PROJ-T853")
    assert result == {"key": "PROJ-T853"}


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


def test_build_payload_minimal_has_only_required_fields():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], None, None, None, None
    )
    assert payload == {
        "projectKey": "PROJ",
        "name": "Имя",
        "testScript": {
            "type": "STEP_BY_STEP",
            "steps": [{"description": "шаг"}],
        },
    }


def test_build_payload_never_sends_server_defaults():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], None, None, None, None
    )
    assert "status" not in payload
    assert "priority" not in payload
    assert "majorVersion" not in payload


def test_build_payload_keeps_step_order_and_never_sends_index():
    steps = [
        {"description": "первый"},
        {"description": "второй", "expectedResult": "ок"},
        {"description": "третий", "testData": "данные"},
    ]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    built_steps = payload["testScript"]["steps"]
    assert [s["description"] for s in built_steps] == ["первый", "второй", "третий"]
    assert built_steps[1]["expectedResult"] == "ок"
    assert built_steps[2]["testData"] == "данные"
    assert all("index" not in s for s in built_steps)


def test_build_payload_ignores_caller_supplied_index():
    steps = [{"description": "a", "index": 99}, {"description": "b", "index": 42}]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    built_steps = payload["testScript"]["steps"]
    assert [s["description"] for s in built_steps] == ["a", "b"]
    assert all("index" not in s for s in built_steps)


def test_build_payload_drops_empty_optional_step_fields():
    steps = [{"description": "шаг", "expectedResult": "", "testData": None}]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    assert payload["testScript"]["steps"][0] == {"description": "шаг"}


def test_build_payload_includes_optional_fields_when_set():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}],
        "цель", "предусловие", "/Папка/Подпапка", ["smoke"],
    )
    assert payload["objective"] == "цель"
    assert payload["precondition"] == "предусловие"
    assert payload["folder"] == "/Папка/Подпапка"
    assert payload["labels"] == ["smoke"]


def test_build_payload_drops_empty_optional_fields():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], "", None, "", [],
    )
    assert "objective" not in payload
    assert "precondition" not in payload
    assert "folder" not in payload
    assert "labels" not in payload


def test_build_payload_passes_html_through_unchanged():
    html = "<ul><li>первый</li></ul>"
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": html}], html, None, None, None
    )
    assert payload["testScript"]["steps"][0]["description"] == html
    assert payload["objective"] == html


def test_build_payload_rejects_empty_steps():
    with pytest.raises(ZephyrError, match="at least one step"):
        _build_test_case_payload("PROJ", "Имя", [], None, None, None, None)


def test_build_payload_rejects_step_without_description():
    with pytest.raises(ZephyrError, match="description"):
        _build_test_case_payload(
            "PROJ", "Имя", [{"expectedResult": "ок"}], None, None, None, None
        )


def test_build_update_payload_omits_none_fields():
    payload = _build_test_case_update_payload(
        "новая имя", None, None, None, None, None
    )
    assert payload == {"name": "новая имя"}


def test_build_update_payload_keeps_empty_string_to_clear_field():
    payload = _build_test_case_update_payload(
        None, "", "", "/Папка", [], None
    )
    assert payload["objective"] == ""
    assert payload["precondition"] == ""
    assert payload["folder"] == "/Папка"
    assert payload["labels"] == []


def test_build_update_payload_rejects_invalid_folder():
    with pytest.raises(ZephyrError, match="must not be just '/'"):
        _build_test_case_update_payload(None, None, None, "/", None, None)


def test_build_update_payload_keeps_step_order_and_never_sends_index():
    steps = [
        {"description": "первый", "index": 99},
        {"description": "второй", "expectedResult": "ок", "index": 42},
        {"description": "третий", "testData": "данные"},
    ]
    payload = _build_test_case_update_payload(None, None, None, None, None, steps)
    built_steps = payload["testScript"]["steps"]
    assert [s["description"] for s in built_steps] == ["первый", "второй", "третий"]
    assert built_steps[1]["expectedResult"] == "ок"
    assert built_steps[2]["testData"] == "данные"
    assert payload["testScript"]["type"] == "STEP_BY_STEP"
    assert all("index" not in s for s in built_steps)


def test_build_update_payload_drops_empty_optional_step_fields():
    steps = [{"description": "шаг", "expectedResult": "", "testData": None}]
    payload = _build_test_case_update_payload(None, None, None, None, None, steps)
    assert payload["testScript"]["steps"][0] == {"description": "шаг"}


def test_build_update_payload_rejects_all_none():
    with pytest.raises(ZephyrError, match="nothing to change"):
        _build_test_case_update_payload(None, None, None, None, None, None)


def test_build_update_payload_rejects_step_without_description():
    with pytest.raises(ZephyrError, match="description"):
        _build_test_case_update_payload(
            None, None, None, None, None, [{"expectedResult": "ок"}]
        )


def test_build_update_payload_includes_status_when_set():
    payload = _build_test_case_update_payload(
        None, None, None, None, None, None, "Approved"
    )
    assert payload == {"status": "Approved"}


def test_build_update_payload_omits_status_when_none():
    payload = _build_test_case_update_payload(
        "новая имя", None, None, None, None, None, None
    )
    assert "status" not in payload


def test_build_update_payload_status_alone_is_valid_update():
    payload = _build_test_case_update_payload(
        None, None, None, None, None, None, "Deprecated"
    )
    assert payload == {"status": "Deprecated"}


def test_build_test_run_payload_with_keys_includes_items():
    payload = _build_test_run_payload(
        "PROJ", "Спринт 1", ["PROJ-T853", "PROJ-T1124"], None
    )
    assert payload == {
        "projectKey": "PROJ",
        "name": "Спринт 1",
        "items": [
            {"testCaseKey": "PROJ-T853"},
            {"testCaseKey": "PROJ-T1124"},
        ],
    }


def test_build_test_run_payload_without_keys_omits_items():
    payload = _build_test_run_payload("PROJ", "Спринт 1", None, None)
    assert payload == {"projectKey": "PROJ", "name": "Спринт 1"}
    assert "items" not in payload


def test_build_test_run_payload_with_empty_keys_omits_items():
    payload = _build_test_run_payload("PROJ", "Спринт 1", [], None)
    assert "items" not in payload


def test_build_test_run_payload_includes_folder_when_set():
    payload = _build_test_run_payload("PROJ", "Спринт 1", None, "/Папка/Подпапка")
    assert payload["folder"] == "/Папка/Подпапка"


def test_build_test_run_payload_omits_folder_when_empty_or_none():
    assert "folder" not in _build_test_run_payload("PROJ", "Спринт 1", None, None)
    assert "folder" not in _build_test_run_payload("PROJ", "Спринт 1", None, "")


def test_add_test_cases_to_run_with_empty_keys_raises_and_no_api_call():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    client = _client_with_transport(handler)

    with pytest.raises(ZephyrError, match="nothing to add"):
        client.add_test_cases_to_run("PROJ-C818", [])

    assert request_count == 0


def test_add_test_cases_to_run_posts_only_new_keys():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path.endswith("/testrun/PROJ-C818"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"testCaseKey": "PROJ-T1"},
                        {"testCaseKey": "PROJ-T2"},
                    ]
                },
            )
        return httpx.Response(201, json=[{"id": 1}, {"id": 2}])

    client = _client_with_transport(handler)
    result = client.add_test_cases_to_run("PROJ-C818", ["PROJ-T2", "PROJ-T3", "PROJ-T1", "PROJ-T4"])

    assert result == {"key": "PROJ-C818", "updated": True, "added": 2}
    assert len(captured_requests) == 2
    assert captured_requests[0].method == "GET"
    assert captured_requests[1].method == "POST"
    assert captured_requests[1].url.path == "/rest/atm/1.0/testrun/PROJ-C818/testresults"
    assert captured_requests[1].content == b'[{"testCaseKey":"PROJ-T3"},{"testCaseKey":"PROJ-T4"}]'


def test_add_test_cases_to_run_dedupes_input_and_ignores_key_case():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"testCaseKey": "PROJ-T1"}]})
        return httpx.Response(201, json=[{"id": 1}])

    client = _client_with_transport(handler)
    result = client.add_test_cases_to_run(
        "PROJ-C818", ["PROJ-T5", "proj-t5", "PROJ-T5", "proj-t1"]
    )

    assert result == {"key": "PROJ-C818", "updated": True, "added": 1}
    assert len(captured_requests) == 2
    assert captured_requests[1].content == b'[{"testCaseKey":"PROJ-T5"}]'


def test_add_test_cases_to_run_skips_post_when_all_keys_exist():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={"items": [{"testCaseKey": "PROJ-T1"}, {"testCaseKey": "PROJ-T2"}]},
        )

    client = _client_with_transport(handler)
    result = client.add_test_cases_to_run("PROJ-C818", ["PROJ-T2", "PROJ-T1"])

    assert result == {"key": "PROJ-C818", "updated": True, "added": 0}
    assert request_count == 1  # only the GET, no POST


def test_dedup_keys_collapses_case_insensitive_duplicates():
    assert _dedup_keys(["PROJ-T5", "proj-t5", "PROJ-T5"]) == ["PROJ-T5"]
    assert _dedup_keys(["PROJ-T1", "PROJ-T2"]) == ["PROJ-T1", "PROJ-T2"]


def test_archive_test_cases_resolves_ids_and_posts_bulk_archive():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 14208, "key": "PROJ-T853"})
        return httpx.Response(200, json={})

    client = _client_with_transport(handler)
    result = client.archive_test_cases(["PROJ-T853"])

    assert result == {"archived": ["PROJ-T853"]}
    assert len(captured_requests) == 2
    assert captured_requests[0].url.path == "/rest/tests/1.0/testcase/PROJ-T853"
    assert captured_requests[0].url.params["fields"] == "id"
    assert captured_requests[1].method == "POST"
    assert captured_requests[1].url.path == "/rest/tests/1.0/testcase/bulk/archive"
    assert captured_requests[1].content == b"[14208]"


def test_archive_test_cases_dedupes_input_keys():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 1, "key": "PROJ-T1"})
        return httpx.Response(200, json={})

    client = _client_with_transport(handler)
    result = client.archive_test_cases(["PROJ-T1", "proj-t1", "PROJ-T1"])

    assert result == {"archived": ["PROJ-T1"]}
    assert len(captured_requests) == 2  # one GET + one POST


def test_archive_test_cases_with_empty_keys_raises_and_no_api_call():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    client = _client_with_transport(handler)
    with pytest.raises(ZephyrError, match="nothing to archive"):
        client.archive_test_cases([])
    assert request_count == 0


def test_unarchive_test_cases_resolves_ids_and_posts_bulk_unarchive():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 14208, "key": "PROJ-T853"})
        return httpx.Response(200, json="1")

    client = _client_with_transport(handler)
    result = client.unarchive_test_cases(["PROJ-T853"])

    assert result == {"unarchived": ["PROJ-T853"]}
    assert len(captured_requests) == 2
    assert captured_requests[1].method == "POST"
    assert captured_requests[1].url.path == "/rest/tests/1.0/testcase/bulk/unarchive"
    assert captured_requests[1].content == b"[14208]"


def test_delete_test_runs_resolves_ids_and_posts_bulk_delete():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 1744, "key": "PROJ-C818"})
        return httpx.Response(200, json={})

    client = _client_with_transport(handler)
    result = client.delete_test_runs(["PROJ-C818"])

    assert result == {"deleted": ["PROJ-C818"]}
    assert len(captured_requests) == 2
    assert captured_requests[0].url.path == "/rest/tests/1.0/testrun/PROJ-C818"
    assert captured_requests[0].url.params["fields"] == "id"
    assert captured_requests[1].method == "POST"
    assert captured_requests[1].url.path == "/rest/tests/1.0/testrun/bulk/delete"
    assert captured_requests[1].content == b"[1744]"


def test_get_numeric_id_raises_when_id_missing():
    client = _client_with_transport(lambda request: httpx.Response(200, json={"key": "PROJ-T1"}))
    with pytest.raises(ZephyrError, match="could not resolve numeric id"):
        client._get_numeric_id("testcase", "PROJ-T1")


@pytest.mark.parametrize(
    "resource, key",
    [
        ("testrun", "PROJ-C1/../../testcase/PROJ-T1"),
        ("testrun", "PROJ-T1"),
        ("testcase", "PROJ-C1"),
        ("testcase", ""),
    ],
)
def test_get_numeric_id_rejects_invalid_key_without_api_call(resource, key):
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"id": 1, "key": key})

    client = _client_with_transport(handler)
    with pytest.raises(ZephyrError, match="invalid"):
        client._get_numeric_id(resource, key)
    assert request_count == 0


def test_get_numeric_id_rejects_key_mismatch():
    client = _client_with_transport(
        lambda request: httpx.Response(200, json={"id": 7, "key": "PROJ-C999"})
    )
    with pytest.raises(ZephyrError, match="mismatch"):
        client._get_numeric_id("testrun", "PROJ-C818")


def test_get_numeric_id_rejects_non_int_id():
    client = _client_with_transport(
        lambda request: httpx.Response(200, json={"id": "7", "key": "PROJ-C818"})
    )
    with pytest.raises(ZephyrError, match="could not resolve numeric id"):
        client._get_numeric_id("testrun", "PROJ-C818")


def test_delete_test_runs_sends_nothing_when_one_key_is_not_found():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path.endswith("/PROJ-C2"):
            return httpx.Response(404)
        return httpx.Response(200, json={"id": 1, "key": "PROJ-C1"})

    client = _client_with_transport(handler)
    with pytest.raises(ZephyrError, match="not found"):
        client.delete_test_runs(["PROJ-C1", "PROJ-C2", "PROJ-C3"])

    assert all(request.method == "GET" for request in captured_requests)


def _client_with_transport(handler) -> ZephyrClient:
    client = ZephyrClient(Config(base_url="https://zephyr.example", api_token="token"))
    client._http = httpx.Client(
        base_url="https://zephyr.example",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_create_folder_rejects_path_without_leading_slash():
    client = _client_with_transport(lambda request: pytest.fail("should not hit network"))
    with pytest.raises(ZephyrError, match="must start with '/'"):
        client.create_folder("PROJ", "Папка")


def test_create_folder_rejects_path_with_backslash():
    client = _client_with_transport(lambda request: pytest.fail("should not hit network"))
    with pytest.raises(ZephyrError, match="must not contain backslashes"):
        client.create_folder("PROJ", "/Папка\\Подпапка")


def test_create_folder_rejects_empty_path():
    client = _client_with_transport(lambda request: pytest.fail("should not hit network"))
    with pytest.raises(ZephyrError, match="must not be empty"):
        client.create_folder("PROJ", "")


def test_create_folder_rejects_root_path():
    client = _client_with_transport(lambda request: pytest.fail("should not hit network"))
    with pytest.raises(ZephyrError, match="must not be just '/'"):
        client.create_folder("PROJ", "/")


def test_post_does_not_retry_on_429_to_avoid_duplicate_test_case():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(429)

    client = _client_with_transport(handler)

    with pytest.raises(ZephyrError):
        client._request("POST", "/rest/atm/1.0/testcase", json={"name": "x"}, retry_on_429=False)

    assert request_count == 1


def test_list_executions_rejects_empty_response_body():
    client = _client_with_transport(lambda request: httpx.Response(200, content=b""))
    with pytest.raises(ZephyrError, match="empty response"):
        client.list_executions("PROJ-C818")


def test_build_execution_result_payload_minimal():
    payload = _build_execution_result_payload("Pass", None, None)
    assert payload == {"status": "Pass"}


def test_build_execution_result_payload_with_comment_and_steps():
    payload = _build_execution_result_payload("Fail", "second step failed", ["Pass", "Fail"])
    assert payload == {
        "status": "Fail",
        "comment": "second step failed",
        "scriptResults": [
            {"index": 0, "status": "Pass"},
            {"index": 1, "status": "Fail"},
        ],
    }


def test_build_execution_result_payload_rejects_empty_status():
    with pytest.raises(ZephyrError, match="needs a status"):
        _build_execution_result_payload("", None, None)


def test_add_execution_result_puts_to_single_testresult_endpoint():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"id": 37139})

    client = _client_with_transport(handler)
    result = client.add_execution_result("PROJ-C818", "PROJ-T853", "Pass")

    assert result == {"id": 37139}
    assert len(captured_requests) == 1
    assert (
        captured_requests[0].url.path
        == "/rest/atm/1.0/testrun/PROJ-C818/testcase/PROJ-T853/testresult"
    )
    assert captured_requests[0].method == "PUT"


def test_get_still_retries_on_429():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(429)

    client = _client_with_transport(handler)

    with pytest.raises(ZephyrError):
        client._request("GET", "/rest/atm/1.0/testcase/PROJ-T1")

    assert request_count > 1
