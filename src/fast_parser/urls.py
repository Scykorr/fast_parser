from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query keys that are campaign tags, not part of the article identity.
TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_name",
        "utm_id",
        "fbclid",
        "gclid",
        "yclid",
        "ysclid",
        "_openstat",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "igshid",
        "spm",
    }
)


def canonical_url(url: str) -> str:
    """Stable article URL: lowercase host, no fragment, no tracking query."""
    raw = url.strip()
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            pass
    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"

    segments = [segment for segment in (parts.path or "").split("/") if segment]
    path = ("/" + "/".join(segments)) if segments else ""

    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, value))
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))
