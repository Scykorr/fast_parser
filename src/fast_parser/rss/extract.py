from __future__ import annotations

from typing import Protocol


class TextExtractor(Protocol):
    def extract(self, html: str, url: str) -> str | None:
        """Return plain article text, or None when nothing usable was found."""


class TrafilaturaExtractor:
    """Lazy wrapper so tests and `--help` do not pay the import unless needed."""

    def extract(self, html: str, url: str) -> str | None:
        import warnings

        # trafilatura pulls urllib3, which warns once if PySocks is absent.
        warnings.filterwarnings(
            "ignore",
            message="SOCKS support in urllib3 requires the installation of optional dependencies",
        )
        import trafilatura

        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if text is None:
            return None
        cleaned = text.strip()
        return cleaned or None
