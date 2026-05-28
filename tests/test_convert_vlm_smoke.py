"""Smoke tests for scripts/convert_vlm.py.

No external network access — exercises the renderer path with a
synthetic layout JSON so we catch breaks in the python-pptx contract
without burning VLM tokens.

Run from repo root:
    .venv/bin/python -m pytest tests/test_convert_vlm_smoke.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_convert_vlm_imports():
    """The module must import without VLM creds set."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); "
         "import convert_vlm; "
         "assert callable(convert_vlm.normalize_image)"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, f"import failed: {out.stderr}"


def test_layout_json_renderer_roundtrip(tmp_path: Path):
    """build_pptx_from_layout must render our synthetic layout JSON cleanly."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "deck"))
    from deck.build_pptx_from_layout import Builder  # type: ignore

    layout = {
        "slide_size": {"width_in": 13.333333, "height_in": 7.5},
        "source_width": 1600, "source_height": 900,
        "background": "#FFFFFF",
        "slides": [
            {
                "background": "#FFFFFF",
                "source_width": 1600, "source_height": 900,
                "elements": [
                    {"type": "text", "name": "slide-title",
                     "text": "Smoke Test", "box": [100, 100, 1400, 120],
                     "font": "Microsoft YaHei", "size": 48, "bold": True,
                     "color": "#111111", "align": "center", "valign": "middle"},
                    {"type": "shape", "name": "card", "shape": "rounded_rect",
                     "box": [100, 300, 1400, 400], "fill": "#F0F4FA",
                     "line": "#6AA6FF", "line_width": 2, "radius": 0.05},
                    {"type": "line", "points": [200, 700, 1400, 700],
                     "line": "#888888", "line_width": 1, "dash": "dash"},
                ],
            }
        ],
    }
    out = tmp_path / "smoke.pptx"
    Builder(layout, out, assets_root=tmp_path).build()
    assert out.exists()
    # PPTX is a ZIP — size > 10KB is a sane lower bound for a 1-slide doc.
    assert out.stat().st_size > 10_000


def test_extract_json_strips_fences():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import convert_vlm  # type: ignore

    cases = [
        ('{"a":1}', {"a": 1}),
        ('```json\n{"a":1}\n```', {"a": 1}),
        ('blah blah ```json\n{"a":1}\n``` more', {"a": 1}),
        ('prefix {"a":1} suffix', {"a": 1}),
    ]
    for text, expected in cases:
        assert convert_vlm._parse_layout_json(text) == expected, f"failed on {text!r}"


def test_selected_pages():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import convert_vlm  # type: ignore

    assert convert_vlm.selected_pages(None, 5) == [1, 2, 3, 4, 5]
    assert convert_vlm.selected_pages("", 5) == [1, 2, 3, 4, 5]
    assert convert_vlm.selected_pages("2,4", 5) == [2, 4]
    assert convert_vlm.selected_pages("1-3", 5) == [1, 2, 3]
    assert convert_vlm.selected_pages("1-3,5", 5) == [1, 2, 3, 5]
    # Out-of-range clamped
    assert convert_vlm.selected_pages("0,1,99", 5) == [1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
