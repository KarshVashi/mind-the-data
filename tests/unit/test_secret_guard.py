"""The pre-commit app_key guard is the only automated check that keeps a TfL
key out of recorded fixtures, so its pattern is tested against the formats a
key could be saved in. The pattern is read from the config, not copied."""

import re
from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"
# Built at runtime so this file never contains a literal key-shaped string.
KEY = "0f" * 16


def _guard() -> re.Pattern[str]:
    config = yaml.safe_load(CONFIG.read_text())
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] == "no-tfl-app-key":
                return re.compile(hook["entry"])
    raise AssertionError("no-tfl-app-key hook not found")


@pytest.mark.parametrize(
    "line",
    [
        f"https://api.tfl.gov.uk/Line/Mode/tube/Status?app_key={KEY}",
        f"/StopPoint/940GZZLUOXC/Arrivals?foo=1&app_key={KEY}&bar=2",
        f"TFL_APP_KEY={KEY}",
        f'TFL_APP_KEY="{KEY}"',
        f"app_key = {KEY}",
        f'{{"params": {{"app_key": "{KEY}"}}}}',
        f"  app_key: {KEY}",
        f"app_key%3D{KEY}",
        f"APP_KEY%3d{KEY.upper()}",
        f"'appkey': '{KEY}'",
    ],
)
def test_guard_catches_key(line: str) -> None:
    assert _guard().search(line)


@pytest.mark.parametrize(
    "line",
    [
        "https://api.tfl.gov.uk/Line/Mode/tube/Status",
        "/Line/Mode/tube/Status?app_key=",
        '{"app_key": "REDACTED"}',
        "TFL_APP_KEY=${TFL_APP_KEY}",
        "TFL_APP_KEY=",
        "The app_key lives in SSM at /mind-the-data/tfl/app_key.",
    ],
)
def test_guard_allows_scrubbed_or_absent_key(line: str) -> None:
    assert not _guard().search(line)
