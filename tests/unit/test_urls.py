import pytest

from mind_the_data.common.urls import strip_app_key


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # absent
        ("/Line/Meta/Severity", "/Line/Meta/Severity"),
        ("https://api.tfl.gov.uk/Line/Meta/Severity", "https://api.tfl.gov.uk/Line/Meta/Severity"),
        (
            "/Line/victoria/Timetable/X?direction=inbound",
            "/Line/victoria/Timetable/X?direction=inbound",
        ),
        # only parameter
        ("/StopPoint/X/Arrivals?app_key=abc123", "/StopPoint/X/Arrivals"),
        # first, middle and last position
        ("/a?app_key=k&direction=inbound", "/a?direction=inbound"),
        ("/a?x=1&app_key=k&y=2", "/a?x=1&y=2"),
        ("/a?direction=inbound&app_key=k", "/a?direction=inbound"),
        # absolute URL with fragment
        ("https://api.tfl.gov.uk/a?app_key=k&x=1#top", "https://api.tfl.gov.uk/a?x=1#top"),
        # case and percent-encoded names
        ("/a?APP_KEY=k&x=1", "/a?x=1"),
        ("/a?app%5Fkey=k&x=1", "/a?x=1"),
        ("/a?app%5fkey=k", "/a"),
        # legacy app_id goes too
        ("/a?app_id=i&app_key=k&x=1", "/a?x=1"),
        # repeated and empty values
        ("/a?app_key=k1&app_key=k2&x=1", "/a?x=1"),
        ("/a?app_key=&x=1", "/a?x=1"),
        ("/a?app_key&x=1", "/a?x=1"),
        # other parameters keep their exact spelling, including encoded commas
        ("/a?ids=tube%2Cdlr&app_key=k", "/a?ids=tube%2Cdlr"),
        # lookalike names are not touched
        ("/a?app_keys=1&my_app_key=2", "/a?app_keys=1&my_app_key=2"),
    ],
)
def test_strip_app_key(url: str, expected: str) -> None:
    assert strip_app_key(url) == expected


def test_strip_app_key_never_leaves_the_value() -> None:
    secret = "0123456789abcdef0123456789abcdef"
    result = strip_app_key(f"https://api.tfl.gov.uk/Line/Mode/tube/Status?app_key={secret}")
    assert secret not in result
