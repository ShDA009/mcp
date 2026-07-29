from dataclasses import dataclass

import pytest
from exchangelib.errors import ErrorNameResolutionNoResults, TransportError

from outlook_mcp.directory_service import resolve_person
from outlook_mcp.errors import ConnectionUnavailableError


@dataclass
class FakeMailboxResult:
    name: str
    email_address: str


class FakeProtocol:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    def resolve_names(self, names):
        self.calls.append(names)
        if self._error is not None:
            raise self._error
        return self._result if self._result is not None else []


class FakeAccount:
    def __init__(self, protocol):
        self.protocol = protocol


def test_resolve_person_returns_candidates():
    protocol = FakeProtocol(result=[FakeMailboxResult(name="Ivanov Ivan", email_address="i.ivanov@example.com")])
    result = resolve_person(FakeAccount(protocol), "Ivanov Ivan")
    assert result == {"candidates": [{"name": "Ivanov Ivan", "email": "i.ivanov@example.com"}]}
    assert protocol.calls == [["Ivanov Ivan"]]


def test_resolve_person_multiple_candidates():
    protocol = FakeProtocol(
        result=[
            FakeMailboxResult(name="Ivanov Ivan", email_address="i.ivanov@example.com"),
            FakeMailboxResult(name="Ivanov Igor", email_address="ig.ivanov@example.com"),
        ]
    )
    result = resolve_person(FakeAccount(protocol), "Ivanov")
    assert len(result["candidates"]) == 2


def test_resolve_person_no_results_returns_empty_list_not_error():
    protocol = FakeProtocol(result=[ErrorNameResolutionNoResults("no matches")])
    result = resolve_person(FakeAccount(protocol), "Nobody")
    assert result == {"candidates": []}


def test_resolve_person_translates_transport_errors():
    protocol = FakeProtocol(error=TransportError("boom"))
    with pytest.raises(ConnectionUnavailableError):
        resolve_person(FakeAccount(protocol), "Ivanov")
