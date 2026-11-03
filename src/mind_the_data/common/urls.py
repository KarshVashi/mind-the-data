"""URL helpers shared by the client, the raw writer and fixture recording."""

from urllib.parse import unquote_plus, urlsplit, urlunsplit

# TfL accepts the key as app_key; app_id is the legacy companion parameter.
# Neither may ever reach storage, logs or fixtures.
_SECRET_PARAMS = frozenset({"app_key", "app_id"})


def strip_app_key(url: str) -> str:
    """Return ``url`` with any TfL credential query parameters removed.

    Works on absolute URLs and on bare paths. Other parameters keep their
    original spelling and order, so the result still names the exact request.
    Parameter names are matched case-insensitively after percent-decoding, so
    ``APP_KEY=`` and ``app%5Fkey=`` are removed as well.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        pair
        for pair in parts.query.split("&")
        if pair and unquote_plus(pair.split("=", 1)[0]).strip().lower() not in _SECRET_PARAMS
    ]
    return urlunsplit(parts._replace(query="&".join(kept)))
