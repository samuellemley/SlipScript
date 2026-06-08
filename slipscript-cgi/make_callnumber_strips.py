#!/usr/bin/env python3
"""
make_callnumber_strips.py

Generate print-ready PDFs of call-number strips laid out as vertical strips on
Letter portrait pages, optimized to maximize strips per page.

CSV headers (case-insensitive):
    author, short_title, location, call_number

Layout:
    - 4 vertical strips per 8.5 x 11 sheet
    - no borders
    - no margins in PDF
    - trim marks only at top and bottom edges for vertical cuts
    - all text uses one uniform font size across the whole PDF
    - font size auto-shrinks globally until every label fits

Each strip:
    - 10" tall
    - width automatically computed from page width / strips_per_page
    - text grouped at top: location, title block, call number
"""

from __future__ import annotations

import argparse
import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List

from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def _safe(text: str) -> str:
    """Normalize to ASCII so ReportLab's built-in fonts can render the text.

    Accented Latin characters are decomposed and their base letters kept
    (e.g. é → e, ü → u).  Characters with no ASCII equivalent (e.g. Hebrew,
    CJK) are dropped rather than rendered as black squares.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


@dataclass
class Row:
    author: str
    short_title: str
    location: str
    call_number: str
    volume: str = ""
    copy_number: str = ""
    barcode: str = ""


def to_printable_ascii(s: str) -> str:
    if not s:
        return ""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("…", "...")
    s = s.replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return " ".join(s.split()).strip()


def read_csv(path: Path) -> List[Row]:
    rows: List[Row] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV appears to be missing a header row.")

        header_map = {h.strip().lower(): h for h in reader.fieldnames if h is not None}
        required = {"author", "short_title", "location", "call_number"}
        if not required.issubset(header_map.keys()):
            raise ValueError(
                "CSV must have headers: author, short_title, location, call_number"
            )

        has_barcode_col = "barcode" in header_map
        has_volume_col  = "volume" in header_map
        has_copy_col    = "copy_number" in header_map

        for i, r in enumerate(reader, start=2):
            author = to_printable_ascii((r.get(header_map["author"]) or "").strip())
            short_title = to_printable_ascii((r.get(header_map["short_title"]) or "").strip())
            location = to_printable_ascii((r.get(header_map["location"]) or "").strip())
            call_number = to_printable_ascii((r.get(header_map["call_number"]) or "").strip())
            volume  = to_printable_ascii((r.get(header_map["volume"]) or "").strip()) if has_volume_col else ""
            copy_number = to_printable_ascii((r.get(header_map["copy_number"]) or "").strip()) if has_copy_col else ""
            barcode = (r.get(header_map["barcode"]) or "").strip() if has_barcode_col else ""

            if not (author or short_title or location or call_number):
                continue
            if not call_number:
                raise ValueError(f"Missing call_number on line {i}")

            rows.append(
                Row(
                    author=author,
                    short_title=short_title,
                    location=location,
                    call_number=call_number,
                    volume=volume,
                    copy_number=copy_number,
                    barcode=barcode,
                )
            )
    return rows


def string_width(c: canvas.Canvas, text: str, font_name: str, font_size: float) -> float:
    return c.stringWidth(text, font_name, font_size)


def wrap_text(
    c: canvas.Canvas,
    text: str,
    font_name: str,
    font_size: float,
    max_width_pts: float,
    max_lines: int,
) -> List[str]:
    words = (text or "").replace("\n", " ").strip().split()
    if not words:
        return []

    lines: List[str] = []
    current: List[str] = []

    def width(s: str) -> float:
        return string_width(c, s, font_name, font_size)

    for w in words:
        candidate = " ".join(current + [w]).strip()
        if not current or width(candidate) <= max_width_pts:
            current.append(w)
        else:
            lines.append(" ".join(current))
            current = [w]
            if len(lines) == max_lines - 1:
                break

    if current and len(lines) < max_lines:
        lines.append(" ".join(current))

    used_words = len(" ".join(lines).split())
    if used_words < len(words) and lines:
        ell = " ..."
        last_words = lines[-1].split()
        while last_words:
            candidate = " ".join(last_words) + ell
            if width(candidate) <= max_width_pts:
                lines[-1] = candidate
                break
            last_words.pop()
        else:
            lines[-1] = "..."

    return lines


def fits_row(
    c: canvas.Canvas,
    row: Row,
    font_name: str,
    font_size: float,
    text_width: float,
    middle_height: float,
    max_middle_lines: int,
    line_gap_extra: float,
) -> bool:
    location = (row.location or "").upper()
    call_number = row.call_number or ""
    middle = " - ".join([p for p in [row.author, row.short_title] if p])

    if string_width(c, location, font_name, font_size) > text_width:
        return False
    if string_width(c, call_number, font_name, font_size) > text_width:
        return False

    line_gap = font_size + line_gap_extra
    max_lines_by_height = int(middle_height // line_gap)
    allowed_lines = max(1, min(max_middle_lines, max_lines_by_height))

    wrapped = wrap_text(c, middle, font_name, font_size, text_width, allowed_lines)
    needed_height = len(wrapped) * line_gap

    # Need space for:
    # location line + gap + middle block + gap + call number line
    total_needed = (2 * font_size) + needed_height
    return total_needed <= middle_height + (2 * font_size) + 0.01


def choose_global_font_size(
    rows: List[Row],
    page_w: float,
    page_h: float,
    strip_w: float,
    strip_h: float,
    pad_x: float,
    pad_top: float,
    pad_bottom: float,
    section_gap: float,
    max_middle_lines: int,
    font_name: str,
    start_size: float = 12.0,
    min_size: float = 6.0,
    step: float = 0.25,
) -> float:
    scratch = canvas.Canvas("/dev/null", pagesize=(page_w, page_h))
    text_width = strip_w - 2 * pad_x

    sizes = [round(start_size - i * step, 2) for i in range(int((start_size - min_size) / step) + 1)]

    for size in sizes:
        line_gap = size + 1.0

        # Available block height from top padding down to bottom padding
        total_text_area = strip_h - pad_top - pad_bottom
        reserved = (2 * size) + (2 * section_gap)  # location + call number + gaps
        middle_height = total_text_area - reserved

        if middle_height <= line_gap:
            continue

        ok = True
        for row in rows:
            if not fits_row(
                scratch,
                row,
                font_name=font_name,
                font_size=size,
                text_width=text_width,
                middle_height=middle_height,
                max_middle_lines=max_middle_lines,
                line_gap_extra=1.0,
            ):
                ok = False
                break

        if ok:
            return size

    return min_size


def draw_trim_marks(
    c: canvas.Canvas,
    page_w: float,
    page_h: float,
    strips_per_page: int,
    mark_len: float = 0.18 * inch,
) -> None:
    """
    Draw trim marks only at the top and bottom edges of the sheet,
    marking the vertical cut lines between strips.
    """
    strip_w = page_w / strips_per_page
    c.saveState()
    c.setLineWidth(0.5)

    for i in range(1, strips_per_page):
        x = i * strip_w
        c.line(x, 0, x, mark_len)
        c.line(x, page_h - mark_len, x, page_h)

    c.restoreState()


def draw_barcode_verso_page(
    c: canvas.Canvas,
    page_rows: List[Row],
    strips_per_page: int,
    page_w: float,
    page_h: float,
    strip_w: float,
    strip_h: float,
    y0: float,
    show_trim_marks: bool,
) -> None:
    """
    Draw one verso (back-side) page of barcodes.

    For long-edge double-sided printing, the page is flipped left-to-right,
    so column order is reversed: the barcode for the strip at column 0 on the
    front must appear at the rightmost column on the back, and so on.

    Each barcode is centered both horizontally and vertically within its strip.
    """
    if show_trim_marks:
        draw_trim_marks(c, page_w, page_h, strips_per_page)

    # Short-edge (top-bottom) flip: columns stay in the same left-right order.
    # The page flips vertically, so a barcode placed at the bottom of the PDF
    # strip lands at the top of the physical strip after printing.

    # Fit barcode width inside strip with a small margin on each side
    margin_x = 0.15 * inch
    max_bc_width = strip_w - 2 * margin_x
    bc_height = 0.65 * inch

    # Find a barWidth that keeps the widest barcode within max_bc_width.
    # Code128 encodes digit pairs (Code C), so width ≈ modules × barWidth.
    # Start at 1.4 pt and step down until everything fits.
    bar_width = 1.4
    for _ in range(20):
        fits = True
        for row in page_rows:
            if not row.barcode:
                continue
            probe = Code128(row.barcode, barWidth=bar_width, barHeight=bc_height,
                            humanReadable=False)
            if probe.width > max_bc_width:
                fits = False
                break
        if fits:
            break
        bar_width = round(bar_width - 0.05, 3)
    bar_width = max(bar_width, 0.5)  # never go below 0.5 pt

    for col, row in enumerate(page_rows):
        if not row.barcode:
            continue

        bc = Code128(
            row.barcode,
            barWidth=bar_width,
            barHeight=bc_height,
            humanReadable=True,
            fontSize=7,
        )

        x_strip = col * strip_w
        # Center horizontally in the strip
        bc_x = x_strip + (strip_w - bc.width) / 2
        # Place at the bottom of the PDF strip — after short-edge flip this
        # becomes the top of the physical strip
        bc_y = y0 + (0.198 * inch)

        # Rotate 180° around the barcode's own centre so it reads correctly
        # after the short-edge (top-to-bottom) flip
        c.saveState()
        c.translate(bc_x + bc.width / 2, bc_y + bc.height / 2)
        c.rotate(180)
        bc.drawOn(c, -bc.width / 2, -bc.height / 2)
        c.restoreState()


def make_pdf(
    rows: List[Row],
    out_path: Path,
    strips_per_page: int = 4,
    show_trim_marks: bool = True,
    strip_height_in: float = 10.0,
    font_name: str = "Helvetica",
    max_middle_lines: int = 8,
    with_barcodes: bool = False,
) -> None:
    page_w, page_h = letter  # 8.5 x 11 portrait
    c = canvas.Canvas(str(out_path), pagesize=(page_w, page_h))

    # Maximize sheet usage: full page width divided evenly into strips
    strip_w = page_w / strips_per_page
    strip_h = strip_height_in * inch

    if strip_h > page_h + 0.01:
        raise ValueError("Strip height exceeds page height.")

    # Center the 10" label area vertically on the 11" sheet
    y0 = (page_h - strip_h) / 2.0

    # Internal padding / spacing (+ ~10%)
    pad_x = 0.22 * inch
    pad_top = 0.198 * inch
    pad_bottom = 0.198 * inch
    section_gap = 0.154 * inch

    global_font_size = choose_global_font_size(
        rows=rows,
        page_w=page_w,
        page_h=page_h,
        strip_w=strip_w,
        strip_h=strip_h,
        pad_x=pad_x,
        pad_top=pad_top,
        pad_bottom=pad_bottom,
        section_gap=section_gap,
        max_middle_lines=max_middle_lines,
        font_name=font_name,
        start_size=12.0,
        min_size=6.0,
        step=0.25,
    )

    text_width = strip_w - 2 * pad_x
    line_gap = global_font_size + 1.0

    total_text_area = strip_h - pad_top - pad_bottom
    reserved = (2 * global_font_size) + (2 * section_gap)
    middle_height = total_text_area - reserved
    max_lines_by_height = int(middle_height // line_gap)
    allowed_middle_lines = max(1, min(max_middle_lines, max_lines_by_height))

    idx = 0
    while idx < len(rows):
        if show_trim_marks:
            draw_trim_marks(c, page_w, page_h, strips_per_page)

        page_rows: List[Row] = []

        for col in range(strips_per_page):
            if idx >= len(rows):
                break

            row = rows[idx]
            idx += 1
            page_rows.append(row)

            x = col * strip_w
            y = y0
            left = x + pad_x

            location   = _safe(row.location or "").upper()
            middle     = _safe(" - ".join([p for p in [row.author, row.short_title] if p]))
            call_number = _safe(row.call_number or "")

            c.setFont(font_name, global_font_size)

            # Compact text block at top of strip
            current_y = y + strip_h - pad_top - global_font_size

            # Location
            c.drawString(left, current_y, location)

            # Gap before middle block
            current_y -= (global_font_size + section_gap)

            # Middle block
            middle_lines = wrap_text(
                c,
                middle,
                font_name,
                global_font_size,
                text_width,
                allowed_middle_lines,
            )

            for line in middle_lines:
                c.drawString(left, current_y, line)
                current_y -= line_gap

            # Gap before call number
            current_y -= section_gap

            # Call number directly below title block
            c.drawString(left, current_y, call_number)

            # Volume — only rendered when present
            if row.volume:
                current_y -= line_gap
                c.drawString(left, current_y, _safe(row.volume))

            # Copy number — only rendered when present.
            # Drop an extra blank line above it to set it apart from
            # the call number / volume block.
            if row.copy_number:
                current_y -= 2 * line_gap
                c.drawString(left, current_y, _safe(row.copy_number))

        c.showPage()

        # Verso page: barcodes mirrored for long-edge double-sided printing
        if with_barcodes and any(r.barcode for r in page_rows):
            draw_barcode_verso_page(
                c=c,
                page_rows=page_rows,
                strips_per_page=strips_per_page,
                page_w=page_w,
                page_h=page_h,
                strip_w=strip_w,
                strip_h=strip_h,
                y0=y0,
                show_trim_marks=show_trim_marks,
            )
            c.showPage()

    c.save()
    print(f"Using global font size: {global_font_size} pt")


def main() -> None:
    ap = argparse.ArgumentParser(description="Create vertical call-number strips from a CSV.")
    ap.add_argument("csv", type=Path, help="Input CSV with headers: author,short_title,location,call_number")
    ap.add_argument("-o", "--out", type=Path, default=Path("callnumber_strips.pdf"), help="Output PDF path")
    ap.add_argument("--no-trim-marks", action="store_true", help="Disable trim marks")
    ap.add_argument("--strips-per-page", type=int, default=4, help="Number of strips per page (default: 4)")
    ap.add_argument("--with-barcodes", action="store_true",
                    help="Add a barcode verso page after each strip page (requires 'barcode' column in CSV)")
    args = ap.parse_args()

    rows = read_csv(args.csv)
    if not rows:
        raise SystemExit("No rows found in CSV.")

    make_pdf(
        rows=rows,
        out_path=args.out,
        strips_per_page=args.strips_per_page,
        show_trim_marks=not args.no_trim_marks,
        with_barcodes=args.with_barcodes,
    )
    print(f"Wrote: {args.out.resolve()}")


if __name__ == "__main__":
    main()