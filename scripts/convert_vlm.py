#!/usr/bin/env python3
"""VLM-driven converter — drop-in replacement for scripts/convert.py.

CLI surface is intentionally compatible with the legacy convert.py so that
web/backend/app/runner.py can invoke this binary unchanged. Heavy local
deps (paddleocr, easyocr, opencv, onnxruntime, paddlex) are NOT required.

Pipeline per page:
    1. normalize source → PNG ≤1600px long edge
    2. POST to OpenAI-compatible /v1/chat/completions with vision (default: gpt-5.5
       via sub2api.ace-ozer.tech)
    3. validate + persist layout JSON
    4. invoke scripts.deck.build_pptx_from_layout.Builder → slides.pptx
    5. write qa.json with element counts + per-page timings

Progress markers (consumed by runner.py STAGE_RE/PAGE_RE):
    === N/M  <stage name>
    page N: <message>
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Allow running both as `python scripts/convert_vlm.py` and as a subprocess
# spawned by runner.py (CWD = repo root).
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(SCRIPTS_ROOT / "deck") not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT / "deck"))

# Lazy: only imported once we know we'll actually need them.
def _lazy_pil():
    from PIL import Image
    return Image

def _lazy_httpx():
    import httpx
    return httpx

def _lazy_fitz():
    import fitz  # PyMuPDF
    return fitz

# build_pptx_from_layout depends on text_safety + text_finalizers in scripts/
# scripts/deck/build_pptx_from_layout.py already does the path munging.
def _import_builder():
    from deck.build_pptx_from_layout import Builder  # type: ignore  # noqa: E402
    return Builder


SUPPORTED_IMG = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


# ----------------------------- VLM prompt -----------------------------

LAYOUT_SYSTEM_PROMPT = """\
You are a slide layout extractor. Given ONE slide screenshot, return a single
JSON object that recreates the slide as an editable PowerPoint layout.

OUTPUT FORMAT — return ONLY the JSON, no prose, no markdown fences:

{
  "source_width": <int, original image width in pixels>,
  "source_height": <int, original image height>,
  "background": "#RRGGBB",
  "elements": [ ...elements in z-order, painters' algorithm... ]
}

Each element is one of:

TEXT (use for every readable string, do not flatten text into images):
  {"type":"text","name":"slide-title|body|caption|footer|...",
   "text":"...","box":[x,y,w,h],
   "font":"Microsoft YaHei|Arial|...",
   "size":<pt>,"bold":<bool>,"italic":<bool>,
   "color":"#RRGGBB","align":"left|center|right",
   "valign":"top|middle|bottom","line_spacing":1.05}

SHAPE (rectangles, cards, dividers, callout backgrounds):
  {"type":"shape","name":"...","shape":"rect|rounded_rect|oval|diamond|triangle|trapezoid",
   "box":[x,y,w,h],"fill":"#RRGGBB|transparent","line":"#RRGGBB|transparent",
   "line_width":<pt>,"radius":<0..0.5 only for rounded_rect>}

LINE (explicit dividers/arrows):
  {"type":"line","points":[x1,y1,x2,y2],"line":"#RRGGBB","line_width":<pt>,
   "dash":"dash|dot"}

TABLE (ONLY when source clearly shows a grid table):
  {"type":"table","box":[x,y,w,h],"rows":<int>,"cols":<int>,
   "cells":[{"row":i,"col":j,"text":"...","bold":<bool>,"align":"...","fill":"#RRGGBB"}],
   "font":"...","size":<pt>}

RULES:
1. Coordinates are pixel-space in the source image (0,0 top-left).
2. Preserve z-order: background bands first, then frames/shapes, then text on top.
3. Use Chinese font "Microsoft YaHei" for Chinese text; "Arial" for Latin.
4. Do NOT emit "image" elements — we cannot extract asset crops in this version.
5. Return MINIMUM 1 text element. Return strictly valid JSON.
"""


def vlm_call(
    image_bytes: bytes,
    *,
    api_base: str,
    api_key: str,
    model: str,
    extra_hint: str = "",
    timeout: float = 180.0,
    retries: int = 1,
) -> dict[str, Any]:
    httpx = _lazy_httpx()
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": LAYOUT_SYSTEM_PROMPT + ("\n\n" + extra_hint if extra_hint else "")},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
    }
    base = api_base.rstrip("/")
    # Tolerate users who paste either '.../v1' or '.../v1/chat/completions'.
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(url, json=body, headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                })
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
            return _parse_layout_json(text)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
    raise RuntimeError(f"VLM call failed after {retries+1} attempts: {last_err}")


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", re.MULTILINE)


def _parse_layout_json(text: str) -> dict[str, Any]:
    t = text.strip()
    m = _JSON_FENCE.search(t)
    if m:
        t = m.group(1)
    if not t.startswith("{"):
        idx = t.find("{")
        if idx >= 0:
            t = t[idx:]
    if not t.endswith("}"):
        idx = t.rfind("}")
        if idx >= 0:
            t = t[: idx + 1]
    return json.loads(t)


# ----------------------------- helpers --------------------------------

def normalize_image(src: Path, max_long_edge: int = 1280) -> tuple[bytes, int, int]:
    Image = _lazy_pil()
    with Image.open(src) as im:
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        w, h = im.size
        long_edge = max(w, h)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            w = int(round(w * scale))
            h = int(round(h * scale))
            im = im.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), w, h


SUPPORTED_PDF = {".pdf"}


def split_pdf_to_pages(pdf_path: Path, dst_dir: Path, dpi: int = 150) -> list[Path]:
    """Rasterize each PDF page to dst_dir/page_NN.png."""
    fitz = _lazy_fitz()
    dst_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            dst = dst_dir / f"page_{i:02d}.png"
            pix.save(str(dst))
            out.append(dst)
    finally:
        doc.close()
    return out


def discover_page_images(src: Path) -> list[Path]:
    if src.is_file():
        if src.suffix.lower() in SUPPORTED_PDF:
            return split_pdf_to_pages(src, src.parent / "_pdf_pages")
        if src.suffix.lower() in SUPPORTED_IMG:
            return [src]
        raise SystemExit(f"Unsupported source: {src}")
    if not src.is_dir():
        raise SystemExit(f"Source not found: {src}")
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in SUPPORTED_IMG)
    if not files:
        raise SystemExit(f"No supported images in {src}")
    return files


# ----------------------------- main -----------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VLM-driven image/PDF → editable PPTX.")
    p.add_argument("--source", "-s", required=True)
    p.add_argument("--work-dir", "-o", required=True)
    p.add_argument("--mode", choices=["full", "text-only"], default="full")
    p.add_argument("--pages")
    p.add_argument("--pdf-dpi", type=int, default=150)
    # Unused-but-accepted flags so the legacy runner.py CLI passthrough works:
    p.add_argument("--skip-render", action="store_true")
    p.add_argument("--skip-calibration", action="store_true")
    p.add_argument("--skip-cross-verify", action="store_true")
    p.add_argument("--calibrate-positions", action="store_true")
    p.add_argument("--font-calibration-iterations", type=int, default=None)
    p.add_argument("--calibration-iterations", type=int, default=None)
    p.add_argument("--calibration-max-shift", type=float, default=30.0)
    p.add_argument("--detect-tables", action="store_true")
    p.add_argument("--table-score-threshold", type=float, default=0.85)
    p.add_argument("--icon-review", action="store_true")
    p.add_argument("--icon-decisions", action="store_true")
    p.add_argument("--ocr-threshold", type=float, default=0.95)
    p.add_argument("--max-review-entries", type=int, default=50)
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--name", default=None)
    return p.parse_args()


def selected_pages(pages_arg: str | None, total: int) -> list[int]:
    if not pages_arg:
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(p for p in out if 1 <= p <= total)


def main() -> int:
    args = parse_args()
    src = Path(args.source).expanduser().resolve()
    work = Path(args.work_dir).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / "layouts").mkdir(exist_ok=True)
    (work / "logs").mkdir(exist_ok=True)
    (work / "source").mkdir(exist_ok=True)

    api_base = os.environ.get("DECKWEAVER_LLM_BASE", "")
    api_key = os.environ.get("DECKWEAVER_LLM_KEY", "")
    primary_model = os.environ.get("DECKWEAVER_LLM_MODEL", "gpt-5.5")
    fallback_model = os.environ.get("DECKWEAVER_LLM_FALLBACK", "gpt-5.4")
    parallel = int(os.environ.get("DECKWEAVER_LLM_PARALLEL", "2"))
    max_long_edge = int(os.environ.get("DECKWEAVER_MAX_LONG_EDGE", "1280"))
    if not api_base or not api_key:
        print("ERROR: DECKWEAVER_LLM_BASE and DECKWEAVER_LLM_KEY env vars are required",
              file=sys.stderr)
        return 2

    pages = discover_page_images(src)
    selected = selected_pages(args.pages, len(pages))
    pages = [pages[i - 1] for i in selected]

    print(f"=== 1/3  prepare ({len(pages)} pages)", flush=True)
    # Normalize + copy page images. We INTENTIONALLY do not emit "page N:"
    # markers here — runner.py treats the latest such line as current_page,
    # so emitting them in prep would race ahead of the actual VLM work and
    # peg current_page at N before extraction even starts.
    src_dir = work / "source"
    src_dir.mkdir(exist_ok=True)
    norm_pages: list[tuple[int, Path, int, int]] = []
    for i, p in enumerate(pages, 1):
        target = src_dir / f"page_{i:02d}.png"
        img_bytes, w, h = normalize_image(p, max_long_edge=max_long_edge)
        target.write_bytes(img_bytes)
        norm_pages.append((i, target, w, h))
        print(f"  prepare {i}/{len(pages)}: {p.name} -> {w}x{h}", flush=True)

    print(f"=== 2/3  vlm-extract", flush=True)
    Builder = _import_builder()
    qa: dict[str, Any] = {"pages": [], "model_primary": primary_model, "model_fallback": fallback_model}

    page_results: dict[int, dict[str, Any]] = {}

    def _extract_one(i: int, page_path: Path, w: int, h: int) -> tuple[int, dict[str, Any]]:
        # First "page N:" line for this page lands here, so current_page
        # advances when we actually start the VLM call.
        print(f"page {i}: vlm call ({primary_model})", flush=True)
        t0 = time.time()
        used_model = primary_model
        layout: dict[str, Any] | None = None
        err: str | None = None
        try:
            layout = vlm_call(page_path.read_bytes(),
                              api_base=api_base, api_key=api_key, model=primary_model,
                              extra_hint=f"The source image is {w}x{h} pixels (page {i}).")
        except Exception as e_primary:  # noqa: BLE001
            print(f"page {i}: primary failed ({e_primary}); trying {fallback_model}", flush=True)
            try:
                used_model = fallback_model
                layout = vlm_call(page_path.read_bytes(),
                                  api_base=api_base, api_key=api_key, model=fallback_model,
                                  extra_hint=f"The source image is {w}x{h} pixels (page {i}).")
            except Exception as e_fb:  # noqa: BLE001
                err = str(e_fb)
                print(f"page {i}: FAILED both models: {err}", flush=True)
        if err or layout is None:
            return i, {"status": "failed", "error": err, "duration_s": round(time.time() - t0, 1)}
        if args.mode == "text-only":
            layout["elements"] = [e for e in layout.get("elements", []) if e.get("type") == "text"]
        layout.setdefault("source_width", w)
        layout.setdefault("source_height", h)
        layout.setdefault("background", "#FFFFFF")
        (work / "layouts" / f"page_{i:03d}.layout.json").write_text(
            json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"page {i}: extracted {len(layout.get('elements', []))} elements", flush=True)
        return i, {"status": "ok", "model": used_model,
                   "elements": len(layout.get("elements", [])),
                   "duration_s": round(time.time() - t0, 1),
                   "layout": layout}

    # parallel=1 → serial (legacy). parallel>=2 → ThreadPoolExecutor cuts
    # wall time on multi-page PDFs ~linearly until we hit upstream limits.
    if parallel <= 1 or len(norm_pages) == 1:
        for i, page_path, w, h in norm_pages:
            idx, result = _extract_one(i, page_path, w, h)
            page_results[idx] = result
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futs = [pool.submit(_extract_one, i, p, w, h) for i, p, w, h in norm_pages]
            for fut in as_completed(futs):
                idx, result = fut.result()
                page_results[idx] = result

    combined_slides: list[dict[str, Any]] = []
    for i, _, _, _ in norm_pages:
        r = page_results.get(i, {})
        if r.get("status") == "ok":
            ly = r["layout"]
            combined_slides.append({
                "background": ly.get("background", "#FFFFFF"),
                "source_width": ly.get("source_width"),
                "source_height": ly.get("source_height"),
                "elements": ly.get("elements", []),
            })
            qa["pages"].append({"page": i, "status": "ok", "model": r["model"],
                                "elements": r["elements"], "duration_s": r["duration_s"]})
        else:
            qa["pages"].append({"page": i, "status": "failed", "error": r.get("error"),
                                "duration_s": r.get("duration_s")})

    print(f"=== 3/3  render", flush=True)
    out_pptx = work / "slides.pptx"
    if not combined_slides:
        print("ERROR: no pages succeeded; nothing to render", file=sys.stderr)
        qa["status"] = "failed"
        (work / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        return 3

    full_layout = {
        "slide_size": {"width_in": 13.333333, "height_in": 7.5},
        "source_width": combined_slides[0]["source_width"],
        "source_height": combined_slides[0]["source_height"],
        "background": "#FFFFFF",
        "slides": combined_slides,
    }
    Builder(full_layout, out_pptx, assets_root=work).build()
    print(f"page {len(combined_slides)}: rendered → {out_pptx.name}", flush=True)

    qa["status"] = "ok" if all(p["status"] == "ok" for p in qa["pages"]) else "partial"
    qa["pptx"] = str(out_pptx.name)
    qa["page_count"] = len(combined_slides)
    (work / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done. PPTX: {out_pptx}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
