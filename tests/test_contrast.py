"""Lock the feed colors to WCAG AA so pager links cannot fade into the paper again."""

from pathlib import Path


def _channel(value: int) -> float:
    channel = value / 255
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_interface_colors_meet_wcag_aa():
    css = Path("src/fast_parser/web/static/style.css").read_text(encoding="utf-8")
    for token in (
        "#1c1915",
        "#ffffff",
        "#7a2424",
        "#3a342c",
        "#e7e0d4",
        "#5c564c",
        "#f6f1e7",
        "#241c16",
        "#f4ecdf",
        "#fff4d6",
        "a.pager-link:link",
        ".pager-current",
        ".pager-disabled",
    ):
        assert token in css
    text = (
        ("#1c1915", "#ffffff"),
        ("#ffffff", "#7a2424"),
        ("#3a342c", "#e7e0d4"),
        ("#1c1915", "#f3e6d0"),
        ("#7a2424", "#fffdf8"),
        ("#7a2424", "#f6f1e7"),
        ("#f6f1e7", "#241c16"),
        ("#f4ecdf", "#241c16"),
        ("#5e584f", "#fffdf8"),
        ("#5e584f", "#f6f1e7"),
        ("#5e584f", "#fff7f4"),
        ("#5e584f", "#efe4d4"),
        ("#1c1915", "#fff4d6"),
        ("#1c1915", "#d9ead3"),
        ("#1c1915", "#e7e2d8"),
        ("#1c1915", "#f4d4d4"),
        ("#ffffff", "#7a2424"),
    )
    for foreground, background in text:
        assert contrast(foreground, background) >= 4.5
    assert contrast("#5c564c", "#ffffff") >= 3
    assert contrast("#5c564c", "#e7e0d4") >= 3


def test_windows_install_points_at_the_cpu_wheel():
    readme = Path("README.md").read_text(encoding="utf-8")
    bat = Path("run.bat").read_text(encoding="utf-8")
    assert "https://download.pytorch.org/whl/cpu" in readme
    assert 'pip install -e ".[ml]"' in readme
    assert "https://download.pytorch.org/whl/cpu" in bat
    assert "fast_parser run" in bat
