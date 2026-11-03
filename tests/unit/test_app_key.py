import logging
from collections.abc import Mapping

import pytest

from mind_the_data.ingest.app_key import KeySource, resolve_app_key


class FakeSsm:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[tuple[str, bool]] = []

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> Mapping[str, object]:
        self.calls.append((Name, WithDecryption))
        return {"Parameter": {"Name": Name, "Value": self.value}}


def _no_ssm() -> FakeSsm:
    raise AssertionError("SSM must not be called")


def test_ssm_parameter_wins_and_is_decrypted() -> None:
    ssm = FakeSsm("from-ssm")
    env = {"TFL_APP_KEY_PARAMETER": "/mind-the-data/tfl/app_key", "TFL_APP_KEY": "local"}
    assert resolve_app_key(env, lambda: ssm) == ("from-ssm", KeySource.SSM)
    assert ssm.calls == [("/mind-the-data/tfl/app_key", True)]


def test_env_var_used_locally() -> None:
    assert resolve_app_key({"TFL_APP_KEY": " local "}, _no_ssm) == ("local", KeySource.ENV)


def test_anonymous_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert resolve_app_key({}, _no_ssm) == (None, KeySource.ANONYMOUS)
    assert "anonymous" in caplog.text


def test_blank_values_are_ignored() -> None:
    assert resolve_app_key({"TFL_APP_KEY_PARAMETER": " ", "TFL_APP_KEY": ""}, _no_ssm)[1] is (
        KeySource.ANONYMOUS
    )


@pytest.mark.parametrize(
    "response",
    [
        {"Parameter": {"Value": "   "}},
        {"Parameter": {"Value": None}},
        {"Parameter": "not-a-mapping"},
        {},
    ],
)
def test_unusable_ssm_value_is_an_error_not_anonymous(response: Mapping[str, object]) -> None:
    class Broken:
        def get_parameter(self, *, Name: str, WithDecryption: bool) -> Mapping[str, object]:
            return response

    with pytest.raises(RuntimeError, match="no usable value"):
        resolve_app_key({"TFL_APP_KEY_PARAMETER": "/p"}, Broken)
