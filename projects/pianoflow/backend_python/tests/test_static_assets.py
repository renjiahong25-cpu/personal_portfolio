"""Tests for the SPA static handler, which is what serves the built frontend.

These cover a defect that made the whole production build unusable: assets were
sent with Tornado's default ``text/html`` content type, and a browser handed a
script labelled as HTML refuses to execute it, so the app never mounted.
"""

from __future__ import annotations

from pathlib import Path

from api.routes import content_type_for


def test_a_script_is_served_as_javascript() -> None:
    """The bug: text/html on a .js file makes the browser refuse to run it."""
    assert content_type_for(Path("index-abc123.js")).startswith("text/javascript")


def test_a_module_script_is_javascript_too() -> None:
    assert content_type_for(Path("chunk.mjs")).startswith("text/javascript")


def test_a_stylesheet_is_served_as_css() -> None:
    assert content_type_for(Path("index-abc.css")).startswith("text/css")


def test_the_entry_document_is_html() -> None:
    assert content_type_for(Path("index.html")).startswith("text/html")


def test_data_files_get_json() -> None:
    assert content_type_for(Path("manifest.json")).startswith("application/json")
    assert content_type_for(Path("chunk.js.map")).startswith("application/json")


def test_fonts_and_icons_get_their_own_types() -> None:
    assert content_type_for(Path("logo.svg")) == "image/svg+xml"
    assert content_type_for(Path("font.woff2")) == "font/woff2"


def test_an_unknown_suffix_falls_back_to_octet_stream() -> None:
    assert content_type_for(Path("data.bin")) == "application/octet-stream"


def test_types_do_not_depend_on_case() -> None:
    """Vite emits lowercase, but a stray capital must not change the answer."""
    assert content_type_for(Path("INDEX.JS")) == content_type_for(Path("index.js"))
