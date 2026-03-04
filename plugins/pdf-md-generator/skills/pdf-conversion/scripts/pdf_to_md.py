#!/usr/bin/env python3
"""Batch convert PDFs to Markdown in-place.

- Input: a directory containing one or more .pdf files
- Output: writes <same-name>.md next to each PDF
- Assets: writes extracted images under ./images/<pdf-stem>/

This script is intentionally small and task-focused.

Behavior:
- Tries MinerU first (best structure/OCR, but may require heavy deps like torch).
- If MinerU isn't available or fails, falls back to a lightweight PyMuPDF
    text extraction so you still get <same-name>.md outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _mineru_error_hints_torch_missing(stderr: str) -> bool:
    s = (stderr or "").lower()
    needles = [
        "no module named 'torch'",
        "no module named \"torch\"",
        "modulenotfounderror",
        "import torch",
    ]
    return any(n in s for n in needles)


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").split())


def _rejoin_block_lines(text: str) -> str:
    """Rejoin lines within a PDF text block broken by narrow column layout.

    PyMuPDF text blocks preserve PDF line breaks which, in multi-column or
    narrow layouts, can split phrases onto separate lines (one word per line).
    Collapsing those breaks into spaces produces clean paragraph output.

    Lines that start with a footnote marker ``<sup>N</sup>``, a section
    number (e.g. ``8.1.1``), or a ``NOTE`` heading are kept as separate
    paragraphs so that the Markdown output mirrors the original structure.

    Lines prefixed with a tab character (``\\t``) are indented in the
    original PDF.  They are preserved as separate lines with 4-space
    indentation (rendered as code-like indented blocks).
    """
    # Pattern for section/clause numbers like "8.1", "8.1.1", "8.1.1.1", "(1)", "(2)"
    _section_re = re.compile(
        r"^(?:"
        r"\d+\.\d+(?:\.\d+)*"   # 8.1, 8.1.1, 8.1.1.1, ...
        r"|NOTE\s+\d+"          # NOTE 5, NOTE 6, ...
        r"|\(\d+\)"             # (1), (2), ...
        r")\s"
    )

    lines = text.split("\n")
    stripped_lines = [l.strip() for l in lines if l.strip()]

    # If any line starts with '|' it's a markdown table — return as-is
    if any(l.startswith("|") for l in stripped_lines):
        return "\n".join(stripped_lines)

    # Check if any lines are indented (tab-prefixed from _extract_text_with_superscripts)
    has_indented = any(l.startswith("\t") for l in lines if l.strip())

    # Detect list-like blocks: if ALL lines are short (≤60 chars) and there
    # are at least 3 lines, keep each line as a separate paragraph (bullet
    # list items, variable definitions, etc.).
    # Skip if the block is already a Markdown table (lines starting with |).
    non_tab_stripped = [l.lstrip("\t").strip() for l in lines if l.strip()]
    if (len(non_tab_stripped) >= 3
            and all(len(l) <= 60 for l in non_tab_stripped)
            and not any(l.startswith("|") for l in non_tab_stripped)):
        # Check that no line looks like a continuation of the previous one
        # (i.e., starts with a lowercase letter).  If most start uppercase
        # or with a symbol, treat as a list.
        upper_starts = sum(1 for l in non_tab_stripped if l[0].isupper() or not l[0].isalpha())
        if upper_starts >= len(non_tab_stripped) * 0.6:
            result_lines = []
            for l in lines:
                if not l.strip():
                    continue
                if l.startswith("\t"):
                    result_lines.append("\u2003" + _normalize_ws(l.lstrip("\t")))
                else:
                    result_lines.append(_normalize_ws(l))
            return "\n\n".join(result_lines)

    paragraphs: list[list[str]] = [[]]  # list of paragraph word-lists
    has_content = False  # Track if we've seen any content yet
    for line in lines:
        raw = line
        stripped = line.strip()
        if not stripped:
            continue

        is_indented = raw.startswith("\t")
        content = stripped.lstrip("\t").strip() if is_indented else stripped

        # A line starting with <sup>...</sup> is a new footnote paragraph.
        # A line starting with a section number is a new paragraph.
        # An indented line is always a new paragraph.
        is_break = False
        if has_content:  # only break if there's already content
            if is_indented:
                is_break = True
            elif re.match(r"<sup>\d+</sup>", content):
                is_break = True
            elif _section_re.match(content):
                is_break = True

        if is_indented:
            # Each indented line is its own paragraph
            if is_break or has_content:
                paragraphs.append(["\u2003" + content])
            else:
                paragraphs[-1] = ["\u2003" + content]
            paragraphs.append([])  # prepare empty next paragraph
        elif is_break:
            paragraphs.append([content])
        else:
            paragraphs[-1].append(content)

        has_content = True

    # Each paragraph: collapse internal whitespace, then join paragraphs
    # with double newline.
    joined_paras = []
    for para_words in paragraphs:
        if para_words:
            joined_paras.append(_normalize_ws(" ".join(para_words)))
    return "\n\n".join(joined_paras)


def _apply_unit_superscripts(md_text: str) -> str:
    """Convert common scientific unit exponents to HTML superscript tags.

    Handles patterns like cm3 -> cm<sup>3</sup>, g/cm3 -> g/cm<sup>3</sup>.
    Only applies to recognised unit abbreviations (cm, mm, µm) followed by
    digits 2 or 3 to avoid false positives.
    """
    # Unit directly followed by exponent digit (not preceded by letter/digit/slash)
    md_text = re.sub(
        r'(?<![a-zA-Z0-9/])(cm|mm|µm)([23])(?![0-9])',
        r'\1<sup>\2</sup>',
        md_text,
    )
    # Slash-prefixed variant: /cm3 -> /cm<sup>3</sup>
    md_text = re.sub(
        r'(/)(cm|mm|µm)([23])(?![0-9])',
        r'\1\2<sup>\3</sup>',
        md_text,
    )
    return md_text


_DISCLAIMER_PATTERNS: list[re.Pattern[str]] = [
    # Some PDFs contain a large background watermark that gets extracted as a
    # standalone "ASTM" line. We only de-duplicate exact-line occurrences.
    re.compile(r"^astm$", re.IGNORECASE),
    # Per-page header lines like "B213 −20" (unicode minus) or "B213-20".
    # Keep the first occurrence only.
    re.compile(r"^b213\s*[-\u2212\u2013\u2014]\s*20$", re.IGNORECASE),
    re.compile(r"\bpursuant to license agreement\b", re.IGNORECASE),
    re.compile(r"\bno further reproductions? authorized\b", re.IGNORECASE),
    re.compile(r"\bdownloaded/printed by\b", re.IGNORECASE),
    re.compile(r"\bcopyright by\s+astm\b", re.IGNORECASE),
    re.compile(r"\ball rights reserved\b", re.IGNORECASE),
    re.compile(r"\bkorea institute of materials science\b", re.IGNORECASE),
    re.compile(r"\(kims\)", re.IGNORECASE),
]


def _is_disclaimer_line(line: str) -> bool:
    s = _normalize_ws(line)
    if not s:
        return False
    return any(p.search(s) for p in _DISCLAIMER_PATTERNS)


def _dedupe_repeated_disclaimers(md_text: str) -> str:
    """Remove repeated disclaimer/watermark lines, keep first occurrence.

    Many ASTM PDFs include per-page footers such as KIMS/license/copyright lines.
    This keeps the first seen unique line and removes repeats.
    """

    lines = (md_text or "").splitlines()
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        if _is_disclaimer_line(line):
            key = _normalize_ws(line).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(_normalize_ws(line))
        else:
            out.append(line)

    # Remove standalone per-page numbers (e.g., "1", "2", "3") that appear
    # near page boundaries (marked by --- separators or ## Page headers).
    page_boundary_re = re.compile(r"^(?:##\s+Page\s+\d+\s*|---\s*)$", re.IGNORECASE)
    page_num_re = re.compile(r"^\s*\d{1,4}\s*$")
    page_idxs = [i for i, l in enumerate(out) if page_boundary_re.match(_normalize_ws(l))]
    if page_idxs:
        remove_idx: set[int] = set()

        # Also clean the preamble section before the first explicit page header.
        first_page = page_idxs[0]
        pre_nonempty = [j for j in range(0, first_page) if _normalize_ws(out[j]) != ""]
        for j in pre_nonempty[-3:]:
            if page_num_re.match(out[j]) and "|" not in out[j]:
                remove_idx.add(j)

        for pi, start in enumerate(page_idxs):
            end = page_idxs[pi + 1] if pi + 1 < len(page_idxs) else len(out)
            # Non-empty line indices within this page section (excluding header).
            nonempty = [j for j in range(start + 1, end) if _normalize_ws(out[j]) != ""]
            if not nonempty:
                continue
            boundary = set(nonempty[:2] + nonempty[-2:])
            for j in boundary:
                if page_num_re.match(out[j]) and "|" not in out[j]:
                    remove_idx.add(j)
        if remove_idx:
            out = [l for i, l in enumerate(out) if i not in remove_idx]

    # Clean up excessive blank lines introduced by removals
    cleaned: list[str] = []
    blank_run = 0
    for line in out:
        if _normalize_ws(line) == "":
            blank_run += 1
            if blank_run <= 2:
                cleaned.append("")
        else:
            blank_run = 0
            cleaned.append(line)

    return "\n".join(cleaned).strip() + "\n"


def _format_where_blocks(md_text: str) -> str:
    """Format 'where:' definition blocks so symbol and description are on one line.

    In ASTM PDFs, a 'where:' block lists symbol = description pairs.  Due to
    two-column layout extraction, symbol tokens and their '= description …'
    text may end up on separate lines:

        where: τ'st
        (blank)
        = prorated time shear value …

    This function reforms them into:

        where:
        τ'st = prorated time shear value …

    The pattern detected:
      - A 'where:' line (possibly with a symbol token appended)
      - Subsequent lines that are either symbol tokens, '= description …' lines,
        or description continuation lines (indented text without leading '=')
    """
    lines = (md_text or "").splitlines()
    result: list[str] = []
    i = 0

    where_re = re.compile(r"^where\s*:", re.IGNORECASE)
    eq_start_re = re.compile(r"^=\s+")

    while i < len(lines):
        stripped = lines[i].strip()

        if not where_re.match(stripped):
            result.append(lines[i])
            i += 1
            continue

        # Found a "where:" line.  It may have a symbol appended: "where: τ'st"
        after_colon = re.sub(r"^where\s*:\s*", "", stripped, flags=re.IGNORECASE)
        result.append("where:")
        result.append("")

        # Collect definition entries (symbol + = description)
        entries: list[tuple[str, str]] = []
        current_sym = after_colon.strip() if after_colon.strip() else ""
        current_desc = ""
        i += 1

        while i < len(lines):
            s = lines[i].strip()

            if s == "":
                i += 1
                continue

            # '= description …' line
            if eq_start_re.match(s):
                desc_part = re.sub(r"^=\s+", "", s)
                if current_sym:
                    current_desc = desc_part
                elif entries:
                    # Continuation of previous entry description
                    prev_sym, prev_desc = entries[-1]
                    entries[-1] = (prev_sym, (prev_desc + " " + desc_part).strip())
                i += 1
                continue

            # Check if this line ends an entry (new symbol token or other content)
            # Symbol tokens are short (< 15 chars) and don't start with digits
            # followed by a period (section numbers)
            if re.match(r"^\d+\.\d", s):
                # Section number – end of where block
                break
            if re.match(r"^(NOTE|FIG\.|TABLE|---)", s, re.IGNORECASE):
                break

            # If we have a pending sym + desc, save it
            if current_sym and current_desc:
                entries.append((current_sym, current_desc))
                current_sym = ""
                current_desc = ""

            if current_sym and not current_desc:
                # Previous symbol had no '=' description yet; this line
                # might be a continuation description or a new symbol.
                # If line is short and doesn't look like description text, treat as new symbol.
                if len(s) < 15 and not re.match(r"^[a-z]", s):
                    # Save incomplete entry
                    entries.append((current_sym, ""))
                    current_sym = s
                else:
                    # Description continuation
                    current_desc = s
                i += 1
                continue

            if not current_sym:
                # New symbol token
                current_sym = s
                i += 1
                continue

            # Description continuation for current entry
            current_desc = (current_desc + " " + s).strip()
            i += 1

        # Save last pending entry
        if current_sym:
            entries.append((current_sym, current_desc))

        # Emit entries
        for sym, desc in entries:
            if desc:
                result.append(f"{sym} = {desc}")
            else:
                result.append(sym)
            result.append("")

    return "\n".join(result)


def _reorder_figures_and_tables(md_text: str) -> str:
    """Ensure correct ordering: image → FIG caption, TABLE caption → table.

    Post-processes the Markdown output to:
    1. Pair each image with its FIG caption so that: image → caption.
       Handles both single swaps (caption before image) and multi-image
       blocks (N images followed by N captions, or vice-versa).
    2. Move TABLE captions that are separated from their table (|...|) to
       immediately before the table.
    """
    lines = (md_text or "").splitlines()
    fig_re = re.compile(r"^FIG\.?\s+\d", re.IGNORECASE)
    table_caption_re = re.compile(r"^TABLE\s+\d", re.IGNORECASE)
    img_re = re.compile(r"^!\[")
    table_row_re = re.compile(r"^\|")

    # Also recognise page-number-only lines that may sit between image/caption
    page_num_re = re.compile(r"^\d{1,3}$")

    # --- Pass 1: Pair images with FIG captions (image → caption order) ---
    # Scan for contiguous groups of images and FIG captions (with optional
    # blank lines between them) and interleave them: img1, caption1, img2,
    # caption2, …
    new_lines: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Detect start of an image/caption cluster
        if img_re.match(stripped) or fig_re.match(stripped):
            images: list[str] = []
            captions: list[str] = []
            j = i
            # Collect all contiguous images and captions (skip blanks & page nums)
            while j < len(lines):
                s = lines[j].strip()
                if s == "" or page_num_re.match(s):
                    j += 1
                    continue
                if img_re.match(s):
                    images.append(lines[j])
                    j += 1
                elif fig_re.match(s):
                    captions.append(lines[j])
                    j += 1
                else:
                    break

            if images and captions:
                # Pair them: N images with N captions → interleave
                n = max(len(images), len(captions))
                for k in range(n):
                    if k < len(images):
                        new_lines.append(images[k])
                        new_lines.append("")
                    if k < len(captions):
                        new_lines.append(captions[k])
                        new_lines.append("")
                i = j
                continue
            else:
                # Only images or only captions — emit as-is
                for item in images:
                    new_lines.append(item)
                    new_lines.append("")
                for item in captions:
                    new_lines.append(item)
                    new_lines.append("")
                i = j
                continue

        new_lines.append(lines[i])
        i += 1
    lines = new_lines

    # --- Pass 2: TABLE caption far from table → move caption just above table ---
    # Find all TABLE caption positions and table (|...|) block positions.
    result_lines: list[str] = []
    pending_table_captions: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if table_caption_re.match(stripped):
            # Check if next non-blank line is already a table row
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and table_row_re.match(lines[j].strip()):
                # Already adjacent — keep as-is
                result_lines.append(lines[i])
            else:
                # Save caption for later insertion before the table
                pending_table_captions.append(lines[i])
            i += 1
            continue

        if table_row_re.match(stripped) and pending_table_captions:
            # Insert pending table captions before this table block
            result_lines.append("")
            for cap in pending_table_captions:
                result_lines.append(cap)
                result_lines.append("")
            pending_table_captions.clear()

        result_lines.append(lines[i])
        i += 1

    # If any orphan table captions remain, append them
    for cap in pending_table_captions:
        result_lines.append("")
        result_lines.append(cap)

    return "\n".join(result_lines)


def _merge_orphan_kv_into_table(md_text: str) -> str:
    """Convert orphan key-value line sequences into markdown tables.

    In two-column PDF layouts, a table continuation may appear as
    alternating short lines separated by blank lines:

        Parameter
        Value
        μpw-s
        0.05
        μpw-r
        0.05

    This function scans the document for such patterns and converts
    them into markdown table rows, appending to a preceding table
    if one exists with matching column count.
    """
    lines = (md_text or "").splitlines()

    # First pass: collect non-blank short lines into groups.
    # A group is a sequence of short lines (≤40 chars) with only blank
    # lines between them, containing at least 4 lines (2 KV pairs).
    # The first two lines should look like a header ("Parameter", "Value").
    groups: list[tuple[int, int, list[str]]] = []  # (start_idx, end_idx, items)
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        # Look for a potential header line like "Parameter"
        if (s and len(s) <= 40
                and not s.startswith("|") and not s.startswith("!")
                and not s.startswith("#") and not s.startswith("---")
                and not s.startswith("<") and not s[0].isdigit()
                and not re.match(r"^\d+\.\d", s)):
            # Collect consecutive short lines (allowing blank lines between)
            start = i
            items: list[str] = [s]
            j = i + 1
            blank_run = 0
            while j < len(lines):
                sj = lines[j].strip()
                if not sj:
                    blank_run += 1
                    if blank_run > 2:
                        break
                    j += 1
                    continue
                blank_run = 0
                if (len(sj) <= 40
                        and not sj.startswith("|") and not sj.startswith("!")
                        and not sj.startswith("#") and not sj.startswith("---")
                        and not sj.startswith("<")):
                    items.append(sj)
                    j += 1
                else:
                    break

            # Need even count, at least 4 items (header + 1 data pair)
            if len(items) >= 4 and len(items) % 2 == 0:
                # First line should be non-numeric (header key like "Parameter")
                # Second line should be non-numeric (header value like "Value")
                hdr_key = items[0]
                hdr_val = items[1]
                if (not hdr_key.replace(".", "").replace("-", "").isdigit()
                        and not hdr_val.replace(".", "").replace("-", "").isdigit()):
                    # Check data pairs: keys should be non-pure-numeric
                    is_kv = True
                    for ci in range(2, len(items), 2):
                        key = items[ci]
                        if key.replace(".", "").replace("-", "").replace(" ", "").isdigit():
                            is_kv = False
                            break
                    if is_kv:
                        groups.append((start, j, items))
                        i = j
                        continue
        i += 1

    if not groups:
        return md_text

    # Second pass: rebuild the document, replacing groups with tables
    result: list[str] = []
    group_idx = 0
    i = 0
    while i < len(lines):
        if group_idx < len(groups) and i == groups[group_idx][0]:
            start, end, items = groups[group_idx]

            # Find the last table row before this group to append to
            last_table_idx = None
            for ri in range(len(result) - 1, -1, -1):
                if result[ri].startswith("|") and "---" not in result[ri]:
                    last_table_idx = ri
                    break
                elif result[ri].strip() and not result[ri].startswith("|"):
                    break  # non-table content between

            # Build KV rows (skip header pair)
            kv_rows = []
            for ci in range(2, len(items), 2):
                key = items[ci]
                val = items[ci + 1] if ci + 1 < len(items) else ""
                kv_rows.append(f"| {key} | {val} |")

            if last_table_idx is not None and result[last_table_idx].count("|") == 3:
                # Append to existing 2-column table
                insert_pos = last_table_idx + 1
                for row in reversed(kv_rows):
                    result.insert(insert_pos, row)
            else:
                # Create new standalone table
                result.append(f"| {items[0]} | {items[1]} |")
                result.append("|---|---|")
                result.extend(kv_rows)
                result.append("")

            i = end
            group_idx += 1
        else:
            result.append(lines[i])
            i += 1

    return "\n".join(result)


def _center_figures_and_tables(md_text: str) -> str:
    """Center FIG captions and TABLE captions.

    Uses the <center> tag which is supported by both Obsidian and VS Code
    markdown preview.  Avoids <p align="center"> (VS Code strips the align
    attribute) and <div> (breaks Obsidian's Markdown renderer).
    """
    lines = (md_text or "").splitlines()
    fig_re = re.compile(r"^FIG\.?\s+\d", re.IGNORECASE)
    table_caption_re = re.compile(r"^TABLE\s+\d", re.IGNORECASE)

    out: list[str] = []
    for line in lines:
        stripped = line.strip()

        # Center FIG captions
        if fig_re.match(stripped):
            out.append(f'<center>{stripped}</center>')
            continue

        # Center TABLE captions
        if table_caption_re.match(stripped):
            out.append(f'<center>{stripped}</center>')
            continue

        out.append(line)

    return "\n".join(out)


def _bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if ax1 <= bx0 or bx1 <= ax0:
        return False
    if ay1 <= by0 or by1 <= ay0:
        return False
    return True


def _md_escape_cell(value: str) -> str:
    v = (value or "").replace("\n", " ").strip()
    v = _normalize_ws(v)
    return v.replace("|", "\\|")


def _table_to_markdown(table_rows: list[list[str | None]]) -> str | None:
    if not table_rows:
        return None
    # Normalize row lengths
    col_count = max((len(r) for r in table_rows if r), default=0)
    if col_count <= 1:
        return None

    normalized: list[list[str]] = []
    for r in table_rows:
        r = list(r or [])
        r = ["" if c is None else str(c) for c in r]
        if len(r) < col_count:
            r = r + [""] * (col_count - len(r))
        elif len(r) > col_count:
            r = r[:col_count]
        normalized.append([_md_escape_cell(c) for c in r])

    # Heuristic: merge multi-row headers into a single header row when the
    # table has 3+ header rows before numeric-heavy body rows.
    numeric_like_re = re.compile(r"^\d+(?:\.\d+)?[A-Za-z]?$", re.IGNORECASE)

    def numeric_like_cells(row: list[str]) -> int:
        return sum(1 for cell in row if numeric_like_re.match(_normalize_ws(cell)))

    body_start = None
    for i, r in enumerate(normalized):
        if numeric_like_cells(r) >= 2:
            body_start = i
            break

    if body_start is not None and body_start >= 3:
        header_rows = normalized[:body_start]
        body = normalized[body_start:]
        header: list[str] = []
        for ci in range(col_count):
            parts: list[str] = []
            seen_parts: set[str] = set()
            for hr in header_rows:
                frag = _normalize_ws(hr[ci])
                if not frag:
                    continue
                key = frag.lower()
                if key in seen_parts:
                    continue
                seen_parts.add(key)
                parts.append(frag)
            header.append(_md_escape_cell(" ".join(parts)))
    else:
        header = normalized[0]
        body = normalized[1:] if len(normalized) > 1 else []
    # Markdown alignment: center every column.
    # (CommonMark/GFM accept :---: in the separator row.)
    sep = [":---:"] * col_count

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _detect_tables_from_words(
    ppage: Any,
    *,
    y_tolerance: float = 3.0,
    x_bin: float = 12.0,
    min_lines: int = 4,
    min_cols: int = 3,
    max_cols: int = 8,
    min_anchor_presence: float = 0.6,
) -> list[tuple[tuple[float, float, float, float], str]]:
    """Best-effort table detection for whitespace-aligned tables.

    Returns list of (bbox(x0, top, x1, bottom), markdown_table).
    """

    def bin_x(x: float) -> float:
        return round(x / x_bin) * x_bin

    words = ppage.extract_words() or []
    if not words:
        return []

    # Group words into lines by 'top'.
    words_sorted = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))
    lines: list[dict[str, Any]] = []
    for w in words_sorted:
        top = float(w.get("top", 0.0))
        if not lines or abs(top - float(lines[-1]["top"])) > y_tolerance:
            lines.append({"top": top, "words": [w]})
        else:
            lines[-1]["words"].append(w)

    # Precompute per-line binned x positions.
    for line in lines:
        bins = {bin_x(float(w.get("x0", 0.0))) for w in line["words"]}
        line["xbins"] = sorted(bins)

    def line_is_candidate(line: dict[str, Any]) -> bool:
        c = len(line.get("xbins") or [])
        return min_cols <= c <= max_cols

    # Find contiguous candidate segments.
    segments: list[tuple[int, int]] = []
    start = None
    for i, line in enumerate(lines):
        if line_is_candidate(line):
            if start is None:
                start = i
        else:
            if start is not None:
                if i - start >= min_lines:
                    segments.append((start, i))
                start = None
    if start is not None and len(lines) - start >= min_lines:
        segments.append((start, len(lines)))

    results: list[tuple[tuple[float, float, float, float], str]] = []
    for s, e in segments:
        seg_lines = lines[s:e]
        # Build anchor histogram.
        counts: dict[float, int] = {}
        for ln in seg_lines:
            for xb in ln["xbins"]:
                counts[xb] = counts.get(xb, 0) + 1
        # Keep anchors that appear in enough lines.
        required = max(1, int(len(seg_lines) * min_anchor_presence))
        anchors = sorted([x for x, c in counts.items() if c >= required])
        if not (min_cols <= len(anchors) <= max_cols):
            continue

        # Convert anchors to column boundaries.
        bounds: list[float] = []
        for i in range(len(anchors) - 1):
            bounds.append((anchors[i] + anchors[i + 1]) / 2)

        def col_index(x0: float) -> int:
            for i, b in enumerate(bounds):
                if x0 < b:
                    return i
            return len(anchors) - 1

        rows: list[list[str]] = []
        used_word_count = 0
        for ln in seg_lines:
            row = [""] * len(anchors)
            for w in sorted(ln["words"], key=lambda ww: float(ww.get("x0", 0.0))):
                x0 = float(w.get("x0", 0.0))
                text = (w.get("text") or "").strip()
                if not text:
                    continue
                used_word_count += 1
                ci = col_index(x0)
                row[ci] = (row[ci] + " " + text).strip() if row[ci] else text
            rows.append(row)

        # Heuristic: if this segment mostly looks like flowing text (few words
        # distributed across multiple columns), skip.
        multi_col_lines = 0
        for r in rows:
            filled = sum(1 for c in r if _normalize_ws(c))
            if filled >= 2:
                multi_col_lines += 1
        if multi_col_lines < max(min_lines, int(len(rows) * 0.5)):
            continue

        # Additional heuristics to avoid turning paragraph-like layout into tables.
        nonempty_lens: list[int] = []
        empty_cells = 0
        total_cells = len(rows) * len(anchors)
        for r in rows:
            for c in r:
                cc = _normalize_ws(c)
                if not cc:
                    empty_cells += 1
                else:
                    nonempty_lens.append(len(cc))

        empty_frac = (empty_cells / total_cells) if total_cells else 0.0
        if len(anchors) == 2 and empty_frac < 0.1:
            # Often indicates 2-column running text / footnotes rather than a table.
            if nonempty_lens:
                nonempty_lens.sort()
                median_len = nonempty_lens[len(nonempty_lens) // 2]
                if median_len >= 40 or len(rows) >= 20:
                    continue

        # Build bbox.
        x0 = min(float(w.get("x0", 0.0)) for ln in seg_lines for w in ln["words"])
        x1 = max(float(w.get("x1", 0.0)) for ln in seg_lines for w in ln["words"])
        top = min(float(w.get("top", 0.0)) for ln in seg_lines for w in ln["words"])
        bottom = max(float(w.get("bottom", 0.0)) for ln in seg_lines for w in ln["words"])

        md = _table_to_markdown(rows)
        if not md:
            continue
        results.append(((x0, top, x1, bottom), md))

    return results


def _group_words_into_lines(words: list[dict[str, Any]], y_tolerance: float) -> list[dict[str, Any]]:
    words_sorted = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))
    lines: list[dict[str, Any]] = []
    for w in words_sorted:
        top = float(w.get("top", 0.0))
        if not lines or abs(top - float(lines[-1]["top"])) > y_tolerance:
            lines.append({"top": top, "words": [w]})
        else:
            lines[-1]["words"].append(w)
    return lines


def _table_from_word_region(
    words: list[dict[str, Any]],
    *,
    y_tolerance: float = 3.0,
    x_bin: float = 10.0,
    min_cols: int = 2,
    max_cols: int = 10,
    min_lines: int = 3,
    min_anchor_presence: float = 0.5,
) -> tuple[tuple[float, float, float, float], str] | None:
    if not words:
        return None

    # Many ASTM tables include numeric values with footnote suffixes like
    # 34.2A or 33.8B.
    numeric_like_re = re.compile(r"^\d+(?:\.\d+)?[A-Za-z]?$", re.IGNORECASE)

    def bin_x(x: float) -> float:
        return round(x / x_bin) * x_bin

    def annotate_pure_nums(ls: list[dict[str, Any]]) -> None:
        for ln in ls:
            pure_nums = 0
            for w in ln["words"]:
                t = (w.get("text") or "").strip()
                if numeric_like_re.match(t):
                    pure_nums += 1
            ln["pure_nums"] = pure_nums

    lines = _group_words_into_lines(words, y_tolerance)
    if len(lines) < min_lines:
        return None
    annotate_pure_nums(lines)

    # Attempt to focus on the actual table body area by using numeric-heavy lines.
    # This reduces contamination from adjacent narrative text in multi-column layouts.
    candidate_line_words: list[dict[str, Any]] = []
    for ln in lines:
        if int(ln.get("pure_nums", 0)) >= 2:
            candidate_line_words.extend(ln["words"])

    focused_words = words
    if len(candidate_line_words) >= 10:
        x0_min = min(float(w.get("x0", 0.0)) for w in candidate_line_words)
        x1_max = max(float(w.get("x1", 0.0)) for w in candidate_line_words)
        margin = 40.0
        focused_words = [
            w
            for w in words
            if (float(w.get("x0", 0.0)) >= (x0_min - margin)) and (float(w.get("x1", 0.0)) <= (x1_max + margin))
        ]
        if not focused_words:
            focused_words = words

    # Rebuild lines after focusing.
    lines = _group_words_into_lines(focused_words, y_tolerance)
    if len(lines) < min_lines:
        return None
    annotate_pure_nums(lines)

    # Build column anchors from tabular-looking lines first, fallback to all lines.
    anchor_lines = [ln for ln in lines if int(ln.get("pure_nums", 0)) >= 2]
    if len(anchor_lines) < min_lines:
        anchor_lines = lines

    counts: dict[float, int] = {}
    for ln in anchor_lines:
        xbins = {bin_x(float(w.get("x0", 0.0))) for w in ln["words"]}
        for xb in xbins:
            counts[xb] = counts.get(xb, 0) + 1

    required = max(2, int(len(anchor_lines) * min_anchor_presence))
    anchors = sorted([x for x, c in counts.items() if c >= required])

    # Collapse nearby anchors (often multiple bins per true column).
    if anchors:
        clustered: list[list[float]] = [[anchors[0]]]
        for a in anchors[1:]:
            if abs(a - clustered[-1][-1]) <= (x_bin * 1.6):
                clustered[-1].append(a)
            else:
                clustered.append([a])
        anchors = [sorted(g)[len(g) // 2] for g in clustered]

    # If multiple leftmost anchors are just the continuation of a multi-word
    # first column (e.g., material names), merge them into a single column.
    numeric_words = [w for w in focused_words if numeric_like_re.match((w.get("text") or "").strip())]
    if numeric_words and len(anchors) >= 3:
        min_num_x0 = min(float(w.get("x0", 0.0)) for w in numeric_words)
        threshold = min_num_x0 - (x_bin * 2.0)
        left = [a for a in anchors if a < threshold]
        if len(left) > 1:
            anchors = [min(left)] + [a for a in anchors if a >= threshold]

    if not (min_cols <= len(anchors) <= max_cols):
        return None

    bounds: list[float] = []
    for i in range(len(anchors) - 1):
        bounds.append((anchors[i] + anchors[i + 1]) / 2)

    def col_index(x0: float) -> int:
        for i, b in enumerate(bounds):
            if x0 < b:
                return i
        return len(anchors) - 1

    # Determine the first clearly tabular body line (2+ numeric-like tokens).
    body_top: float | None = None
    for ln in lines:
        if int(ln.get("pure_nums", 0)) >= 2:
            body_top = float(ln.get("top", 0.0))
            break

    # 34.2A -> 34.2<sup>A</sup>
    footnote_re = re.compile(r"(\d(?:\.\d+)?)([A-Z])$")

    def clean_cell(text: str) -> str:
        # Turn footnote markers into superscripts for better MD rendering.
        return footnote_re.sub(r"\1<sup>\2</sup>", text)

    rows: list[list[str]] = []
    for ln in lines:
        row = [""] * len(anchors)
        for w in sorted(ln["words"], key=lambda ww: float(ww.get("x0", 0.0))):
            text = (w.get("text") or "").strip()
            if not text:
                continue
            x0 = float(w.get("x0", 0.0))
            ci = col_index(x0)
            row[ci] = (row[ci] + " " + text).strip() if row[ci] else text

        # Apply cell-level cleaning after merging word fragments.
        row = [clean_cell(c) for c in row]

        filled = sum(1 for c in row if _normalize_ws(c))

        # Preserve header/unit lines (often fill a single column) only before
        # the numeric-heavy body starts.
        if filled < 2:
            is_header_region = body_top is not None and float(ln.get("top", 0.0)) < body_top
            if not (is_header_region and filled >= 1):
                continue
        rows.append(row)

    # Merge continuation lines: if a row only fills col0, append to previous row col0.
    merged: list[list[str]] = []
    for r in rows:
        filled_cols = [i for i, c in enumerate(r) if _normalize_ws(c)]
        if len(filled_cols) == 1 and filled_cols[0] == 0 and merged:
            merged[-1][0] = (merged[-1][0] + " " + _normalize_ws(r[0])).strip()
            continue
        merged.append(r)
    rows = merged

    md = _table_to_markdown(rows)
    if not md:
        return None

    # ASTM B213-style tables sometimes place the Flow Rate unit on a separate
    # line like "(s/50 g)". If the unit token exists in the table region but
    # does not survive header reconstruction, patch it into the Flow Rate
    # header cell.
    try:
        focused_text = " ".join((w.get("text") or "") for w in focused_words)
        if ("s/50" in focused_text.lower()) and ("s/50" not in md.lower()):
            md_lines = md.splitlines()
            if md_lines and md_lines[0].lstrip().startswith("|"):
                raw = md_lines[0].strip().strip("|")
                cells = [c.strip() for c in raw.split("|")]
                for i, c in enumerate(cells):
                    if "flow rate" in c.lower() and "s/50" not in c.lower():
                        cells[i] = _md_escape_cell(_normalize_ws(c) + " (s/50 g)")
                        md_lines[0] = "| " + " | ".join(cells) + " |"
                        md = "\n".join(md_lines)
                        break
    except Exception:
        pass

    x0 = min(float(w.get("x0", 0.0)) for w in focused_words)
    x1 = max(float(w.get("x1", 0.0)) for w in focused_words)
    top = min(float(w.get("top", 0.0)) for w in focused_words)
    bottom = max(float(w.get("bottom", 0.0)) for w in focused_words)
    return (x0, top, x1, bottom), md


def _fitz_words_to_dicts(words: list[tuple[float, float, float, float, str, int, int, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in words:
        x0, y0, x1, y1, text = float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])
        out.append({"x0": x0, "x1": x1, "top": y0, "bottom": y1, "text": text})
    return out


# ---------------------------------------------------------------------------
# Symbol list detection
# ---------------------------------------------------------------------------

def _detect_symbol_list(page: Any) -> tuple[
    list[tuple[float, float, float, float]],
    list[tuple[float, float, str]],
]:
    """Detect *LIST OF SYMBOLS* sections and convert them to Markdown tables.

    Many ASTM standards contain an appendix (A1. LIST OF SYMBOLS) where each
    entry consists of a short symbol token (often italic) flush-left, followed
    by a description at a common indented x-position.  These entries may span
    one or two columns and may continue across page boundaries.

    Returns ``(suppress_bboxes, table_elements)`` – the same format used by
    the equation / table detection helpers – so that the main converter can
    suppress the original text blocks and insert the table instead.
    """
    import fitz  # noqa: F811 – already imported by callers

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
    if not blocks:
        return [], []

    page_w = float(page.rect.width)
    page_mid_x = page_w / 2.0

    # ── Step 1: find the LIST OF SYMBOLS heading ──
    has_symbols_heading = False
    heading_y = 0.0
    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        text = "".join(
            s["text"] for line in b.get("lines", []) for s in line.get("spans", [])
        )
        if re.search(r"LIST\s+OF\s+SYMBOLS", text, re.IGNORECASE):
            has_symbols_heading = True
            heading_y = float(b["bbox"][3])
            break

    if not has_symbols_heading:
        return [], []

    # ── Step 2: pre-detect description indent per column ──
    # Scan all blocks below the heading to find the common indent where
    # descriptions start (typically ~76.8 for left column, ~346.2 for right).
    col_indents: dict[str, float | None] = {"left": None, "right": None}
    for col_side in ("left", "right"):
        col_left = 25.0 if col_side == "left" else page_mid_x + 5.0
        col_right = page_mid_x - 5.0 if col_side == "left" else page_w
        desc_xs: list[float] = []
        for b in blocks:
            if b.get("type", 0) != 0:
                continue
            bx0 = float(b["bbox"][0])
            by0 = float(b["bbox"][1])
            if bx0 < col_left - 10 or bx0 > col_right:
                continue
            if by0 < heading_y:
                continue
            for line in b.get("lines", []):
                lx0 = float(line["bbox"][0])
                # Description lines start significantly right of col_left
                if lx0 > col_left + 25:
                    desc_xs.append(lx0)
        if desc_xs:
            # Use the most common x position (mode)
            from collections import Counter
            rounded = [round(x / 2.0) * 2.0 for x in desc_xs]
            most_common = Counter(rounded).most_common(1)
            if most_common:
                col_indents[col_side] = most_common[0][0]

    # ── Step 3: parse symbol entries from each block ──
    all_entries: list[tuple[str, str, tuple[float, float, float, float]]] = []
    suppress_bboxes: list[tuple[float, float, float, float]] = []

    def _join_span_text(spans: list[dict]) -> str:
        """Join span texts, adding space only between separate words.

        Also strips any ASTM copyright / watermark text that PDF renderers
        sometimes overlay at the bottom of pages.
        """
        if not spans:
            return ""
        parts: list[str] = []
        for sp in spans:
            t = sp.get("text", "")
            if t.strip():
                parts.append(t.strip())
        joined = " ".join(parts)
        # Remove ASTM copyright/watermark boilerplate that may appear inline
        joined = re.sub(
            r"\s*Copyright\s+by\s+ASTM\b.*$",
            "",
            joined,
            flags=re.IGNORECASE,
        )
        joined = re.sub(
            r"\s*Downloaded.{0,2}printed\s+by\s+.*$",
            "",
            joined,
            flags=re.IGNORECASE,
        )
        return joined.strip()

    for col_side in ("left", "right"):
        col_left = 25.0 if col_side == "left" else page_mid_x + 5.0
        col_right = page_mid_x - 5.0 if col_side == "left" else page_w
        col_indent = col_indents.get(col_side)
        if col_indent is None:
            continue

        sym_max_x = col_indent - 5.0

        candidate_blocks: list[dict] = []
        for b in blocks:
            if b.get("type", 0) != 0:
                continue
            bx0 = float(b["bbox"][0])
            by0 = float(b["bbox"][1])
            if bx0 < col_left - 10 or bx0 > col_right:
                continue
            if by0 < heading_y:
                continue
            # Skip watermark / copyright blocks (small font, typical boilerplate)
            block_text = "".join(
                s.get("text", "")
                for ln in b.get("lines", [])
                for s in ln.get("spans", [])
            )
            if re.search(
                r"Copyright\s+by\s+ASTM|Downloaded.{0,2}printed\s+by|No\s+further\s+reproductions",
                block_text,
                re.IGNORECASE,
            ):
                suppress_bboxes.append((
                    float(b["bbox"][0]),
                    float(b["bbox"][1]),
                    float(b["bbox"][2]),
                    float(b["bbox"][3]),
                ))
                continue
            candidate_blocks.append(b)

        for b in sorted(candidate_blocks, key=lambda bb: float(bb["bbox"][1])):
            block_lines = b.get("lines", [])
            if not block_lines:
                continue

            # Group lines by y-band (tolerance 3pt).
            y_groups: list[list[dict]] = []
            for line in block_lines:
                ly = float(line["bbox"][1])
                placed = False
                for grp in y_groups:
                    if abs(float(grp[0]["bbox"][1]) - ly) < 3.5:
                        grp.append(line)
                        placed = True
                        break
                if not placed:
                    y_groups.append([line])

            block_matched = False
            for grp in sorted(y_groups, key=lambda g: float(g[0]["bbox"][1])):
                sym_spans: list[dict] = []
                desc_spans: list[dict] = []
                grp_bbox = [9999.0, 9999.0, 0.0, 0.0]

                for line in grp:
                    lx0 = float(line["bbox"][0])
                    lx1 = float(line["bbox"][2])
                    grp_bbox[0] = min(grp_bbox[0], float(line["bbox"][0]))
                    grp_bbox[1] = min(grp_bbox[1], float(line["bbox"][1]))
                    grp_bbox[2] = max(grp_bbox[2], float(line["bbox"][2]))
                    grp_bbox[3] = max(grp_bbox[3], float(line["bbox"][3]))

                    if lx0 <= sym_max_x and lx1 < col_indent - 3:
                        # Symbol line
                        sym_spans.extend(line.get("spans", []))
                    elif lx0 >= col_indent - 8:
                        # Description line
                        desc_spans.extend(line.get("spans", []))
                    else:
                        # Ambiguous – treat as description continuation
                        desc_spans.extend(line.get("spans", []))

                if sym_spans:
                    sym = _join_span_text(sym_spans)
                    desc = _join_span_text(desc_spans)
                    all_entries.append((sym, desc, tuple(grp_bbox)))
                    block_matched = True
                elif desc_spans and all_entries:
                    # Continuation of previous description (wrapped line)
                    prev_sym, prev_desc, prev_bbox = all_entries[-1]
                    cont_desc = _join_span_text(desc_spans)
                    all_entries[-1] = (
                        prev_sym,
                        (prev_desc + " " + cont_desc).strip(),
                        (
                            min(prev_bbox[0], grp_bbox[0]),
                            min(prev_bbox[1], grp_bbox[1]),
                            max(prev_bbox[2], grp_bbox[2]),
                            max(grp_bbox[3], prev_bbox[3]),
                        ),
                    )
                    block_matched = True

            if block_matched:
                suppress_bboxes.append((
                    float(b["bbox"][0]),
                    float(b["bbox"][1]),
                    float(b["bbox"][2]),
                    float(b["bbox"][3]),
                ))

    if len(all_entries) < 2:
        return [], []

    # ── Step 3b: filter out spurious entries (page numbers, etc.) ──
    all_entries = [
        (sym, desc, bbox)
        for sym, desc, bbox in all_entries
        if not re.match(r"^\d+$", sym.strip())  # page numbers
    ]

    if len(all_entries) < 2:
        return [], []

    # ── Step 4: build the Markdown table ──
    def _escape(t: str) -> str:
        return t.replace("|", "\\|").strip()

    md_lines = ["| Symbol | Description |", "| :--- | :--- |"]
    for sym, desc, _ in all_entries:
        md_lines.append(f"| {_escape(sym)} | {_escape(desc)} |")

    md_table = "\n".join(md_lines)

    # Insert at the y position of the first entry
    first_y = all_entries[0][2][1]
    insert_y = first_y - 0.01  # just before the first entry

    return suppress_bboxes, [(insert_y, 0.0, md_table)]


def _detect_tables_near_headings_fitz(page: Any) -> list[tuple[tuple[float, float, float, float], float, str]]:
    """Return [(bbox, insert_y, md_table), ...] using PyMuPDF page coordinates."""

    # PyMuPDF words: x0, y0, x1, y1, word, block_no, line_no, word_no
    words_raw = page.get_text("words") or []
    if not words_raw:
        return []

    # Group into lines by (block_no, line_no)
    by_line: dict[tuple[int, int], list[tuple]] = {}
    for w in words_raw:
        block_no = int(w[5])
        line_no = int(w[6])
        by_line.setdefault((block_no, line_no), []).append(w)

    lines: list[dict[str, Any]] = []
    for (bn, ln), ws in by_line.items():
        ws = sorted(ws, key=lambda t: float(t[0]))
        text = " ".join(_normalize_ws(str(t[4])) for t in ws if str(t[4]).strip())
        if not text:
            continue
        y0 = min(float(t[1]) for t in ws)
        y1 = max(float(t[3]) for t in ws)
        x0 = min(float(t[0]) for t in ws)
        x1 = max(float(t[2]) for t in ws)
        lines.append({"bn": bn, "ln": ln, "y0": y0, "y1": y1, "x0": x0, "x1": x1, "text": text})

    headings = [
        l
        for l in lines
        if re.match(r"^TABLE\s+\d+\b(?!\.)", l["text"], flags=re.IGNORECASE)
        and (l["text"].split()[0].upper() == "TABLE")
    ]
    if not headings:
        return []

    headings = sorted(headings, key=lambda d: d["y0"])
    results: list[tuple[tuple[float, float, float, float], float, str]] = []

    words_dicts = _fitz_words_to_dicts(words_raw)
    page_mid_x = float(page.rect.width) / 2.0
    for i, h in enumerate(headings):
        start_y = float(h["y1"]) + 1.0
        end_y = float(headings[i + 1]["y0"]) - 1.0 if i + 1 < len(headings) else float("inf")
        # In multi-column documents, the content below a heading may continue
        # in the same column while the other column advances with different
        # sections. Restrict to the heading's column first to avoid mixing.
        # However, if the TABLE heading spans across the page midpoint, the
        # table is full-width and we should NOT restrict by column.
        column_margin = 8.0
        heading_x0 = float(h.get("x0", 0.0))
        heading_x1 = float(h.get("x1", 0.0))
        heading_center_x = (heading_x0 + heading_x1) / 2.0
        # A TABLE heading is full-width if it spans across the page midpoint
        # OR if its centre is close to the page centre (within 25% of half-
        # width). The centre check handles headings whose word-level line
        # stops just short of the midpoint due to subscripts / line breaks.
        heading_spans_full = (
            (heading_x0 < page_mid_x and heading_x1 > page_mid_x)
            or abs(heading_center_x - page_mid_x) < page_mid_x * 0.30
        )
        heading_is_right = heading_x0 > page_mid_x
        if heading_spans_full:
            # Full-width table heading: first try both columns, then check
            # if the data is actually concentrated in one column only.
            all_region = [
                w
                for w in words_dicts
                if float(w["top"]) >= start_y
                and float(w["top"]) < end_y
            ]
            # Check if words are spread across both columns or just one.
            left_words = [w for w in all_region if float(w.get("x1", 0.0)) <= page_mid_x + column_margin]
            right_words = [w for w in all_region if float(w.get("x0", 0.0)) >= page_mid_x - column_margin]
            # If both columns have significant content, it may be body text
            # mixed with a table. Try right-column only (tables often appear
            # on the right in two-column papers).
            if left_words and right_words and len(left_words) > 5 and len(right_words) > 5:
                # Try right-column-only table first
                region = right_words
            else:
                region = all_region
        elif heading_is_right:
            region = [
                w
                for w in words_dicts
                if float(w["top"]) >= start_y
                and float(w["top"]) < end_y
                and float(w.get("x0", 0.0)) >= page_mid_x - column_margin
            ]
        else:
            region = [
                w
                for w in words_dicts
                if float(w["top"]) >= start_y
                and float(w["top"]) < end_y
                and float(w.get("x1", 0.0)) <= page_mid_x + column_margin
            ]
        if not region:
            continue

        # Shrink region to the likely end of the table body by using the last
        # pure-number token position. This avoids including normal two-column
        # body text below the table.
        numeric_like_re = re.compile(r"^\d+(?:\.\d+)?[A-Za-z]?$")
        # Limit region to the likely end of the table body by finding the last
        # line that still looks tabular (2+ numeric-like tokens). This avoids
        # pulling in page numbers or footer digits.
        try:
            region_lines = _group_words_into_lines(region, y_tolerance=4.0)
            tabular_lines = []
            min_num_spread = 120.0
            for ln in region_lines:
                nnums = 0
                num_xs: list[float] = []
                for w in ln["words"]:
                    t = (w.get("text") or "").strip()
                    if numeric_like_re.match(t):
                        nnums += 1
                        num_xs.append(float(w.get("x0", 0.0)))
                ln["nnums"] = nnums
                # Table body rows typically have multiple numeric cells spread
                # across the row. This avoids misclassifying phone numbers.
                if nnums >= 3 and num_xs and (max(num_xs) - min(num_xs)) >= min_num_spread:
                    tabular_lines.append(ln)
            if len(tabular_lines) >= 3:
                last = max(tabular_lines, key=lambda d: float(d.get("top", 0.0)))
                last_bottom = max(float(w.get("bottom", w.get("top", 0.0))) for w in last["words"])
                cutoff = last_bottom + 4.0
                region = [w for w in region if float(w["top"]) <= cutoff]
                if not region:
                    continue
        except Exception:
            pass
        tbl = _table_from_word_region(
            region,
            min_cols=2,
            max_cols=10,
            x_bin=8.0,
            y_tolerance=4.0,
            min_anchor_presence=0.2,
        )
        if tbl is None:
            continue
        bbox, md = tbl

        # Quality check: reject tables where body text was mixed in.
        # In two-column PDFs, the heading detector may grab words from
        # both columns, producing a wide table with many empty cells.
        md_lines = [l for l in md.splitlines() if l.startswith("|") and "---" not in l]
        if md_lines:
            col_count = md_lines[0].count("|") - 1
            total_cells = 0
            empty_cells = 0
            for ml in md_lines:
                parts = ml.split("|")[1:-1]  # strip leading/trailing empty
                for p in parts:
                    total_cells += 1
                    if not p.strip():
                        empty_cells += 1
            fill_ratio = (total_cells - empty_cells) / max(total_cells, 1)
            # Real tables: mostly filled. Garbage from mixed columns: many empty cells.
            if col_count > 5 and fill_ratio < 0.65:
                continue

        # Attach table footnotes (A/B/...) that appear immediately below the
        # table, and expand the suppressed bbox to avoid duplicated text.
        notes: list[str] = []
        notes_bottom = float(bbox[3])
        try:
            scan_end_y = min(float(end_y), float(bbox[3]) + 220.0)
            foot_candidates: list[dict[str, Any]] = []
            for l in lines:
                y0 = float(l.get("y0", 0.0))
                if y0 < (float(bbox[3]) + 0.5) or y0 >= scan_end_y:
                    continue
                if not heading_spans_full:
                    if heading_is_right:
                        if float(l.get("x0", 0.0)) < (page_mid_x - column_margin):
                            continue
                    else:
                        if float(l.get("x1", 0.0)) > (page_mid_x + column_margin):
                            continue
                foot_candidates.append(l)
            foot_candidates.sort(key=lambda d: float(d.get("y0", 0.0)))

            note_re = re.compile(r"^([A-Z])\s+", re.IGNORECASE)
            section_re = re.compile(r"^\d+(?:\.\d+)+\b")

            current: str | None = None
            last_y1: float | None = None
            for l in foot_candidates:
                t = _normalize_ws(str(l.get("text") or ""))
                if not t:
                    if current:
                        break
                    continue
                if section_re.match(t):
                    break

                match = note_re.match(t)
                if match and len(t.split()[0]) == 1:
                    if current:
                        notes.append(current.strip())
                    # A -> <sup>A</sup>
                    marker = match.group(1)
                    rest = t[len(marker) :].lstrip()
                    current = f"<sup>{marker}</sup> {rest}"
                elif current is not None:
                    if last_y1 is not None and (float(l.get("y0", 0.0)) - last_y1) > 12.0:
                        break
                    current = (current + " " + t).strip()
                else:
                    break
                last_y1 = float(l.get("y1", l.get("y0", 0.0)))
                notes_bottom = max(notes_bottom, float(l.get("y1", l.get("y0", 0.0))))
            if current:
                notes.append(current.strip())
        except Exception:
            notes = []

        if notes:
            md = md + "\n\n" + "\n".join(notes)
            x0, top, x1, _bottom = bbox
            bbox = (x0, top, x1, notes_bottom)
        insert_y = start_y + 0.01
        results.append((bbox, insert_y, md))

    return results


def _detect_tables_near_headings(
    ppage: Any,
    *,
    y_tolerance: float = 3.0,
) -> list[tuple[tuple[float, float, float, float], str]]:
    """Detect tables by anchoring on explicit 'TABLE n' headings.

    ASTM docs frequently label tables as 'TABLE 1', 'TABLE 2', etc.
    This approach is more accurate than trying to infer tables page-wide.
    """

    words = ppage.extract_words() or []
    if not words:
        return []

    # Build line groups; find lines that start with 'TABLE' and a number.
    lines = _group_words_into_lines(words, y_tolerance)

    heading_lines: list[dict[str, Any]] = []
    for ln in lines:
        texts = [str(w.get("text") or "") for w in ln["words"]]
        joined = " ".join(_normalize_ws(t) for t in texts if t).strip()
        if not joined:
            continue
        if re.match(r"^TABLE\s+\d+\b(?!\.)", joined, flags=re.IGNORECASE) and joined.split()[0].upper() == "TABLE":
            ln["joined"] = joined
            heading_lines.append(ln)

    if not heading_lines:
        return []

    # For each heading, take words below it until the next heading (or page bottom).
    results: list[tuple[tuple[float, float, float, float], str]] = []
    heading_tops = sorted((float(h["top"]) for h in heading_lines))
    for i, h in enumerate(sorted(heading_lines, key=lambda d: float(d["top"]))):
        h_top = float(h["top"])
        # Define start a bit below heading line.
        h_bottom = max(float(w.get("bottom", h_top)) for w in h["words"]) + 2.0
        next_top = heading_tops[i + 1] if i + 1 < len(heading_tops) else float("inf")

        region_words = [
            w
            for w in words
            if float(w.get("top", 0.0)) >= h_bottom and float(w.get("top", 0.0)) < next_top - 2.0
        ]
        # Heuristic: limit to a reasonable chunk to avoid swallowing the whole page.
        if not region_words:
            continue

        # Try to build a table from this region. Prefer 2+ cols here.
        tbl = _table_from_word_region(region_words, min_cols=2, y_tolerance=y_tolerance)
        if tbl is None:
            continue
        bbox, md = tbl
        results.append((bbox, md))

    return results


def _detect_and_render_equations(
    page: Any,
    page_index: int,
    images_dir: Path,
    rel_images_prefix: str,
) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, str]]]:
    """Detect equation regions containing fraction layouts or math bracket
    fonts and render them as cropped page images.

    Returns
    -------
    eq_bboxes : list of (x0, y0, x1, y1)
        Bounding boxes of detected equation regions (used to skip these
        blocks during normal text extraction).
    eq_elements : list of (y0, x0, image_markdown)
        Elements to insert into the page output.
    """
    try:
        import fitz  # noqa: F811
    except Exception:
        return [], []

    d = page.get_text("dict")
    blocks = d.get("blocks", [])

    # --- Phase 1: analyse each text block --------------------------------
    block_info: dict[int, dict] = {}
    eq_candidate_indices: set[int] = set()

    for bi, block in enumerate(blocks):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox", (0, 0, 0, 0))
        total_text = ""
        has_math_pi = False
        has_greek_math = False
        n_lines = 0

        for line in block.get("lines", []):
            n_lines += 1
            for span in line.get("spans", []):
                text = span.get("text", "")
                font = span.get("font", "")
                total_text += text
                if "MathematicalPi" in font:
                    has_math_pi = True
                if "Universal-Greek" in font or "GreekwithMath" in font:
                    has_greek_math = True

        total_len = len(total_text.strip())
        total_stripped = total_text.strip()
        block_info[bi] = {
            "bbox": bbox,
            "text": total_stripped,
            "text_len": total_len,
            "has_math_pi": has_math_pi,
            "has_greek_math": has_greek_math,
            "n_lines": n_lines,
        }

        # Equation candidate: short block with math fonts, but NOT
        # figure captions, table headers, notes, or "where:" blocks.
        _is_caption = bool(re.match(
            r"^(FIG\.|TABLE\s|NOTE\s|where:)", total_stripped, re.IGNORECASE
        ))
        if (has_math_pi or has_greek_math) and total_len < 50 and not _is_caption:
            eq_candidate_indices.add(bi)

    if not eq_candidate_indices:
        return [], []

    # --- Phase 2: group nearby equation candidates -----------------------
    eq_groups: list[set[int]] = []
    used: set[int] = set()

    for seed in sorted(eq_candidate_indices):
        if seed in used:
            continue
        info = block_info[seed]
        group = {seed}
        y_min = info["bbox"][1]
        y_max = info["bbox"][3]
        x_min = info["bbox"][0]
        x_max = info["bbox"][2]

        # Iteratively expand the group with nearby blocks
        changed = True
        while changed:
            changed = False
            for bi, bi_info in block_info.items():
                if bi in group:
                    continue
                bb = bi_info["bbox"]
                bi_y_mid = (bb[1] + bb[3]) / 2.0

                # Must overlap vertically (within 15pt margin)
                if bi_y_mid < y_min - 15 or bi_y_mid > y_max + 15:
                    continue
                # Must be somewhat close horizontally
                if bb[2] < x_min - 200 or bb[0] > x_max + 200:
                    continue

                # Include if: equation candidate, or short text that
                # could be a denominator / equation number / sub/superscript
                # Exclude figure/table captions and note blocks.
                is_eq_candidate = bi in eq_candidate_indices
                is_short_fragment = bi_info["text_len"] < 15
                _bi_text = bi_info.get("text", "")
                _bi_is_caption = bool(re.match(
                    r"^(FIG\.|TABLE\s|NOTE\s|where:)", _bi_text, re.IGNORECASE
                ))
                if (is_eq_candidate or is_short_fragment) and not _bi_is_caption:
                    group.add(bi)
                    y_min = min(y_min, bb[1])
                    y_max = max(y_max, bb[3])
                    x_min = min(x_min, bb[0])
                    x_max = max(x_max, bb[2])
                    changed = True

        used.update(group)
        eq_groups.append(group)

    if not eq_groups:
        return [], []

    # --- Phase 3: render each group as a cropped page image --------------
    eq_bboxes: list[tuple[float, float, float, float]] = []
    eq_elements: list[tuple[float, float, str]] = []

    for gi, group in enumerate(eq_groups):
        rects = [block_info[bi]["bbox"] for bi in group]
        # Tight bbox (for excluding overlapping text blocks)
        tight_x0 = min(r[0] for r in rects)
        tight_y0 = min(r[1] for r in rects)
        tight_x1 = max(r[2] for r in rects)
        tight_y1 = max(r[3] for r in rects)

        # Padded bbox (for image rendering)
        render_x0 = tight_x0 - 5
        render_y0 = tight_y0 - 5
        render_x1 = tight_x1 + 5
        render_y1 = tight_y1 + 5

        # Clip to page
        pw, ph = float(page.rect.width), float(page.rect.height)
        render_x0, render_y0 = max(0, render_x0), max(0, render_y0)
        render_x1, render_y1 = min(pw, render_x1), min(ph, render_y1)

        clip = fitz.Rect(render_x0, render_y0, render_x1, render_y1)
        pix = page.get_pixmap(clip=clip, dpi=250)
        img_name = f"eq_p{page_index + 1}_{gi + 1}.png"
        img_path = images_dir / img_name
        pix.save(str(img_path))

        rel_path = f"{rel_images_prefix}/{img_name}"
        # Use the TIGHT bbox for text exclusion (no padding)
        # to avoid accidentally excluding nearby text blocks.
        eq_bboxes.append((tight_x0, tight_y0, tight_x1, tight_y1))
        eq_elements.append((float(tight_y0), float(tight_x0), f"![]({rel_path})"))

    return eq_bboxes, eq_elements


def _extract_text_with_superscripts(page: Any) -> list[tuple[float, float, float, float, str]]:
    """Extract text blocks from a PyMuPDF page, applying superscript and
    special-font fixups inline.

    Returns list of (x0, y0, x1, y1, text) tuples – one per logical block.
    Superscript spans (flags & 1) are wrapped in <sup>…</sup> when they
    contain short numeric/letter tokens (footnote markers).
    Characters from the Universal-GreekwithMathPi font are mapped to their
    correct Unicode equivalents (e.g. '5' → '=', '6' → '±').
    """

    _GREEK_MATH_MAP: dict[str, str] = {
        "!": ")",
        "~": "(",
        "1": "+",
        "2": "−",
        "3": "·",
        "4": "≤",
        "5": "=",
        "6": "±",
    }

    d = page.get_text("dict")
    results: list[tuple[float, float, float, float, str]] = []

    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox", (0, 0, 0, 0))
        line_texts: list[tuple[float, float, str]] = []  # (y, x, text)
        prev_line_y: float | None = None

        for line in block.get("lines", []):
            line_bbox = line.get("bbox", [0, 0, 0, 0])
            line_y = line_bbox[1]
            line_x = line_bbox[0]
            span_parts: list[str] = []

            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                flags = span.get("flags", 0)
                font = span.get("font", "")

                # Fix characters from mathematical symbol fonts
                if "Universal-Greek" in font or "GreekwithMath" in font:
                    mapped = "".join(_GREEK_MATH_MAP.get(c, c) for c in text)
                    text = mapped

                # Wrap superscript spans in <sup> tags
                is_super = bool(flags & 1)
                stripped = text.strip()
                size = span.get("size", 12.0)
                # Also treat very small single-digit spans as superscript
                # footnote markers (e.g., size=5.0 without super flag)
                is_small_footnote = (
                    not is_super
                    and size <= 6.0
                    and stripped
                    and len(stripped) <= 2
                    and stripped.isdigit()
                )
                if (is_super or is_small_footnote) and stripped and len(stripped) <= 3:
                    # Only wrap the non-whitespace portion
                    leading = text[:len(text) - len(text.lstrip())]
                    trailing = text[len(text.rstrip()):]
                    text = f"{leading}<sup>{stripped}</sup>{trailing}"

                span_parts.append(text)

            joined = "".join(span_parts).strip()
            if joined:
                line_texts.append((line_y, line_x, joined))

        # ── Detect inline tabular data ──
        # When consecutive lines in a block share the same y-coordinate in
        # pairs (two lines per row), they represent a two-column inline table
        # (e.g. ρb / σp,1 value tables in ASTM standards).  Group them and
        # emit a Markdown table, then emit the remaining lines as a separate
        # sub-block.
        sub_blocks: list[tuple[float, float, float, float, str]] = []
        i = 0
        n_lines = len(line_texts)
        while i < n_lines:
            # Look for a run of paired same-y lines (at least 2 rows = 4 lines)
            run_start = i
            paired_rows: list[tuple[str, str]] = []
            while i + 1 < n_lines:
                y_a = line_texts[i][0]
                y_b = line_texts[i + 1][0]
                if abs(y_a - y_b) < 1.5:
                    paired_rows.append((line_texts[i][2], line_texts[i + 1][2]))
                    i += 2
                else:
                    break

            if len(paired_rows) >= 2:
                # Emit any preceding non-table lines as a text sub-block
                if run_start > 0:
                    preceding = "\n".join(t for _, _, t in line_texts[:run_start])
                    if preceding.strip():
                        sub_blocks.append((bbox[0], bbox[1], bbox[2], bbox[3], preceding))

                # Build a Markdown table
                hdr = paired_rows[0]
                rows = paired_rows[1:]
                md_table_lines = [
                    f"| {hdr[0]} | {hdr[1]} |",
                    f"|---|---|",
                ]
                for left, right in rows:
                    md_table_lines.append(f"| {left} | {right} |")
                table_md = "\n".join(md_table_lines)
                sub_blocks.append((bbox[0], line_texts[run_start][0], bbox[2], bbox[3], table_md))

                # Remaining lines after the table run
                remaining = "\n".join(t for _, _, t in line_texts[i:])
                if remaining.strip():
                    rem_y = line_texts[i][0] if i < n_lines else bbox[3]
                    sub_blocks.append((bbox[0], rem_y, bbox[2], bbox[3], remaining))
                break  # done with this block
            else:
                # Not a table run — rewind the partial pairs
                i = run_start
                break

        if sub_blocks:
            results.extend(sub_blocks)
        else:
            # Detect indented lines: if a line's x-coordinate is 15+ pt
            # to the right of the block's leftmost x, prefix it with a tab
            # character so _rejoin_block_lines can preserve it.
            if line_texts:
                block_left_x = min(x for _, x, _ in line_texts)
                assembled_lines: list[str] = []
                for _, lx, lt in line_texts:
                    if lx - block_left_x >= 15:
                        assembled_lines.append("\t" + lt)
                    else:
                        assembled_lines.append(lt)
                block_text = "\n".join(assembled_lines)
            else:
                block_text = ""
            if block_text.strip():
                results.append((bbox[0], bbox[1], bbox[2], bbox[3], block_text))

    return results


def _fix_equation_where_block(text: str) -> str:
    """Format 'where:' variable definition blocks with proper line breaks.

    Converts patterns like:
        where: M = mass of ... , and V = volume of ...
    Into:
        where:<br>M = mass of ... , and<br>V = volume of ...
    """
    # Match 'where:' followed by variable definitions
    where_re = re.compile(
        r"(where\s*:)\s+"
        r"([A-Z])\s*=\s*(.*?)\s*,?\s+and\s+"
        r"([A-Z])\s*=\s*(.*?)(?:\.|$)",
        re.IGNORECASE,
    )
    m = where_re.search(text)
    if m:
        replacement = (
            f"{m.group(1)}\n"
            f"{m.group(2)} = {m.group(3).strip()}, and\n"
            f"{m.group(4)} = {m.group(5).strip()}."
        )
        text = text[:m.start()] + replacement + text[m.end():]
    return text


def _convert_with_pymupdf_layout(pdf_path: Path, out_md: Path, images_root: Path) -> None:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "PyMuPDF is required for fallback conversion. Install with: pip install pymupdf"
        ) from exc

    pdf_stem = pdf_path.stem
    # Replace spaces with underscores in image folder name to avoid
    # broken image paths in markdown renderers (Obsidian, VS Code).
    safe_stem = pdf_stem.replace(" ", "_")
    images_dir = images_root / safe_stem / "extracted"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    rel_images_prefix = f"images/{safe_stem}/extracted"

    def save_extracted_image(doc: "fitz.Document", xref: int) -> tuple[str, str]:
        extracted = doc.extract_image(xref)
        ext = (extracted.get("ext") or "png").lower()
        if ext == "jpeg":
            ext = "jpg"
        img_name = f"xref-{xref}.{ext}"
        img_path = images_dir / img_name
        img_bytes = extracted["image"]
        if not img_path.exists():
            img_path.write_bytes(img_bytes)
        digest = hashlib.sha1(img_bytes).hexdigest()
        return f"{rel_images_prefix}/{img_name}", digest

    parts: list[str] = [f"# {pdf_stem}", ""]

    # Filter out per-page header identifiers like "B213 −20" when they match
    # the PDF stem (the stem is already used as the document title).
    stem_id_re: re.Pattern[str] | None = None
    if "-" in pdf_stem:
        left, right = pdf_stem.split("-", 1)
        left = re.escape(left.strip())
        right = re.escape(right.strip())
        dash = r"[-\u2212\u2013\u2014]"
        stem_id_re = re.compile(rf"^{left}\s*{dash}\s*{right}$", re.IGNORECASE)

    # Optional table extraction (best-effort). If pdfplumber isn't installed or
    # fails to detect tables, we still emit text+images.
    try:
        import pdfplumber  # type: ignore
    except Exception:
        pdfplumber = None  # type: ignore

    plumber_pdf = None
    if pdfplumber is not None:
        try:
            plumber_pdf = pdfplumber.open(str(pdf_path))
        except Exception:
            plumber_pdf = None

    with fitz.open(pdf_path) as doc:
        # ── Pre-scan: count how many pages each image digest appears on ──
        # Images repeated across multiple pages are watermarks/logos.
        _digest_page_count: dict[str, int] = {}
        for _pre_pi in range(doc.page_count):
            _pre_page = doc.load_page(_pre_pi)
            _seen_on_page: set[str] = set()
            for _pre_img in _pre_page.get_images(full=True):
                _pre_xref = _pre_img[0]
                try:
                    _pre_data = doc.extract_image(_pre_xref)
                    _pre_digest = hashlib.sha1(_pre_data["image"]).hexdigest()
                except Exception:
                    continue
                if _pre_digest not in _seen_on_page:
                    _seen_on_page.add(_pre_digest)
                    _digest_page_count[_pre_digest] = _digest_page_count.get(_pre_digest, 0) + 1

        seen_center_watermark_hashes: set[str] = set()
        deferred_watermark_md: str | None = None
        for page_index in range(doc.page_count):
            page_no = page_index + 1
            page = doc.load_page(page_index)

            table_bboxes: list[tuple[float, float, float, float]] = []
            table_elements: list[tuple[float, float, str]] = []

            # Prefer fitz-based TABLE heading detection to keep coordinate systems consistent.
            try:
                detected_fitz = _detect_tables_near_headings_fitz(page)
                for bbox, insert_y, md in detected_fitz:
                    x0, top, x1, bottom = bbox
                    table_bboxes.append((x0, top, x1, bottom))
                    table_elements.append((insert_y, x0, md))
            except Exception:
                detected_fitz = []

            # Fallback to word-based detector (pdfplumber) only if no heading-based tables found.
            if not table_elements and plumber_pdf is not None and page_index < len(plumber_pdf.pages):
                try:
                    ppage = plumber_pdf.pages[page_index]
                    detected = _detect_tables_from_words(ppage)
                    for bbox, md in detected:
                        x0, top, x1, bottom = bbox
                        table_bboxes.append((x0, top, x1, bottom))
                        table_elements.append((top, x0, md))
                except Exception:
                    table_bboxes = []
                    table_elements = []

            # Detect equation regions with fraction layouts / math fonts
            # and render them as cropped page images.
            eq_bboxes: list[tuple[float, float, float, float]] = []
            eq_elements: list[tuple[float, float, str]] = []
            try:
                eq_bboxes, eq_elements = _detect_and_render_equations(
                    page, page_index, images_dir, rel_images_prefix
                )
            except Exception:
                eq_bboxes, eq_elements = [], []

            # Detect symbol-list sections (A1. LIST OF SYMBOLS)
            # and convert them to uniform Markdown tables.
            sym_bboxes: list[tuple[float, float, float, float]] = []
            sym_elements: list[tuple[float, float, str]] = []
            try:
                sym_bboxes, sym_elements = _detect_symbol_list(page)
            except Exception:
                sym_bboxes, sym_elements = [], []

            # Collect text blocks using superscript-aware extraction.
            elements: list[tuple[float, float, str]] = []
            # Store (x0, y0, x1, y1) for each element for column detection.
            element_extents: dict[int, tuple[float, float, float, float]] = {}
            extracted_blocks = _extract_text_with_superscripts(page)
            for bx0, by0, bx1, by1, text in extracted_blocks:
                block_bbox = (float(bx0), float(by0), float(bx1), float(by1))
                # Skip blocks overlapping with detected tables
                if table_bboxes:
                    if any(_bbox_intersects(block_bbox, tb) for tb in table_bboxes):
                        continue
                # Skip blocks overlapping with detected symbol lists
                if sym_bboxes:
                    if any(_bbox_intersects(block_bbox, sb) for sb in sym_bboxes):
                        continue
                # Skip blocks whose vertical CENTER falls inside an
                # equation region (stricter than plain intersection to
                # avoid excluding large text blocks that merely touch
                # the equation bbox edge).
                if eq_bboxes:
                    block_cy = (float(by0) + float(by1)) / 2.0
                    in_eq = False
                    for ebx0, eby0, ebx1, eby1 in eq_bboxes:
                        if eby0 <= block_cy <= eby1 and not (block_bbox[2] < ebx0 or block_bbox[0] > ebx1):
                            in_eq = True
                            break
                    if in_eq:
                        continue
                text = (text or "").strip()
                if text:
                    text = _rejoin_block_lines(text)
                    text = _fix_equation_where_block(text)
                    normalized = _normalize_ws(text)
                    if normalized.lower() == "astm":
                        continue
                    if stem_id_re is not None and stem_id_re.match(normalized):
                        continue
                    idx = len(elements)
                    elements.append((float(by0), float(bx0), text))
                    element_extents[idx] = (float(bx0), float(by0), float(bx1), float(by1))

            # Collect embedded images with their placement rectangles.
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                if not rects:
                    continue
                try:
                    rel_path, digest = save_extracted_image(doc, xref)
                except Exception:
                    continue
                for r in rects:
                    # Watermark detection: an image whose digest appears on
                    # 2+ pages is a repeated logo / watermark.  Keep the
                    # first occurrence (deferred to document top) and
                    # suppress all subsequent ones.
                    is_repeated = _digest_page_count.get(digest, 1) >= 2

                    if is_repeated:
                        if digest in seen_center_watermark_hashes:
                            continue
                        seen_center_watermark_hashes.add(digest)
                        if deferred_watermark_md is None:
                            deferred_watermark_md = f"![]({rel_path})"
                        continue
                    idx = len(elements)
                    elements.append((float(r.y0), float(r.x0), f"![]({rel_path})"))
                    element_extents[idx] = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))

            # Insert tables and equation images as elements too.
            if table_elements:
                elements.extend(table_elements)
            if eq_elements:
                elements.extend(eq_elements)
            if sym_elements:
                elements.extend(sym_elements)

            # ── Two-column layout detection & column-aware sorting ──
            # ASTM standards typically use a two-column layout.  Naive
            # (y, x) sorting interleaves left and right columns.  We
            # detect whether the page is two-column by checking if most
            # text blocks fall clearly on one side of the page midpoint,
            # then sort left-column elements first, followed by right.
            # Full-width elements (tables / images spanning both columns)
            # are placed at their y-position relative to the column they
            # appear after.

            page_mid_x = page_w / 2.0
            col_margin = 20.0  # tolerance around the mid-line

            # Classify each element as LEFT, RIGHT or FULL.
            left_els: list[tuple[float, float, str]] = []
            right_els: list[tuple[float, float, str]] = []
            full_els: list[tuple[float, float, str]] = []

            for ei, (y0, x0, content) in enumerate(elements):
                # Check if the element is a detected table (tables store
                # markdown which starts with '|' or 'TABLE').
                is_table_el = content.lstrip().startswith("|") or re.match(r"^TABLE\s+\d", content, re.IGNORECASE)

                # Use stored extents for width info
                ext = element_extents.get(ei)
                block_x1 = ext[2] if ext else x0

                block_cx = (x0 + block_x1) / 2.0
                block_width = block_x1 - x0

                # Full-width: element spans across the midpoint significantly
                if block_width > page_w * 0.55 or is_table_el:
                    full_els.append((y0, x0, content))
                elif block_cx < page_mid_x - col_margin:
                    left_els.append((y0, x0, content))
                elif block_cx > page_mid_x + col_margin:
                    right_els.append((y0, x0, content))
                else:
                    # Element centred on the page midpoint — treat as
                    # full-width (e.g. FIG captions, centred headings).
                    full_els.append((y0, x0, content))

            # Determine if this is actually a two-column page
            is_two_col = len(left_els) >= 3 and len(right_els) >= 3

            if is_two_col:
                left_els.sort(key=lambda t: (t[0], t[1]))
                right_els.sort(key=lambda t: (t[0], t[1]))
                full_els.sort(key=lambda t: (t[0], t[1]))

                # Merge: left column first, then right column.
                # Full-width elements are placed based on their vertical
                # position relative to the column content:
                #  - ABOVE both columns → before left column
                #  - BELOW both columns → after right column
                #  - Between → after left, before right
                ordered: list[tuple[float, float, str]] = []

                left_min_y = left_els[0][0] if left_els else float("inf")
                left_max_y = max(e[0] for e in left_els) if left_els else 0.0
                right_min_y = min(e[0] for e in right_els) if right_els else float("inf")
                right_max_y = max(e[0] for e in right_els) if right_els else 0.0

                above_full: list[tuple[float, float, str]] = []
                middle_full: list[tuple[float, float, str]] = []
                below_full: list[tuple[float, float, str]] = []

                for fe in full_els:
                    fy = fe[0]
                    if fy < min(left_min_y, right_min_y) - 5.0:
                        above_full.append(fe)
                    elif fy > max(left_max_y, right_max_y) - 5.0:
                        below_full.append(fe)
                    else:
                        middle_full.append(fe)

                ordered.extend(above_full)
                ordered.extend(left_els)
                ordered.extend(middle_full)
                ordered.extend(right_els)
                ordered.extend(below_full)

                elements = ordered
            else:
                elements.sort(key=lambda t: (t[0], t[1]))

            # Emit elements without page boundary headers for cleaner output.
            # Detect elements whose x0 is indented relative to the column
            # left margin and add em-space prefix to each line.
            if is_two_col:
                left_x_margin = min(e[1] for e in left_els) if left_els else 0.0
                right_x_margin = min(e[1] for e in right_els) if right_els else page_mid_x
                col_indent_threshold = 15.0  # points

            if page_index > 0:
                parts.append("---")
                parts.append("")
            for _, x0_el, content in elements:
                # Add indentation for blocks indented relative to column margin
                if is_two_col and not content.startswith("![")\
                        and not content.startswith("|")\
                        and not content.startswith("<"):
                    if x0_el < page_mid_x:
                        col_left = left_x_margin
                    else:
                        col_left = right_x_margin
                    if x0_el - col_left >= col_indent_threshold:
                        # Indent each line of the content
                        indented_lines = []
                        for cline in content.split("\n"):
                            if cline.strip():
                                if not cline.startswith("\u2003"):
                                    cline = "\u2003" + cline
                            indented_lines.append(cline)
                        content = "\n".join(indented_lines)
                parts.append(content)
                parts.append("")

        # Put a single watermark/logo (if any) at the top of the document
        # instead of repeating it on many pages.
        if deferred_watermark_md:
            parts.insert(2, deferred_watermark_md)
            parts.insert(3, "")

    if plumber_pdf is not None:
        try:
            plumber_pdf.close()
        except Exception:
            pass

    md_text = "\n".join(parts).strip() + "\n"
    md_text = _dedupe_repeated_disclaimers(md_text)
    md_text = _format_where_blocks(md_text)
    md_text = _reorder_figures_and_tables(md_text)
    md_text = _merge_orphan_kv_into_table(md_text)
    md_text = _apply_unit_superscripts(md_text)
    md_text = _center_figures_and_tables(md_text)
    out_md.write_text(md_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert all PDFs in a directory to Markdown (in-place). "
            "Uses MinerU when possible, otherwise falls back to PyMuPDF."
        )
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        type=Path,
        required=True,
        help="Directory containing PDF files",
    )
    parser.add_argument(
        "--backend",
        "-b",
        type=str,
        default="hybrid-auto-engine",
        choices=["pipeline", "vlm", "hybrid-auto-engine"],
        help="MinerU backend (default: hybrid-auto-engine)",
    )
    parser.add_argument(
        "--language",
        "-l",
        type=str,
        default="en",
        help="OCR language (default: en)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .md outputs if they already exist",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help=(
            "Convert only selected PDFs (match by filename or stem). "
            "Can be provided multiple times, e.g. --only B213-20"
        ),
    )
    return parser.parse_args()


def _find_mineru_cli() -> Path | None:
    """Locate the MinerU CLI executable.

    Prefer the CLI that ships with the current Python environment (venv).
    """
    scripts_dir = Path(sys.executable).parent
    exe_name = "mineru.exe" if os.name == "nt" else "mineru"
    candidate = scripts_dir / exe_name
    if candidate.exists():
        return candidate

    which = shutil.which("mineru")
    if which:
        return Path(which)

    return None


def _rewrite_image_paths(md_text: str, pdf_stem: str) -> str:
    # MinerU outputs MD that references images under a sibling ./images directory.
    # When we move the MD next to the original PDF, we also relocate images to
    # ./images/<safe_stem>/, so update links accordingly.
    safe_stem = pdf_stem.replace(" ", "_")
    replacements = {
        "(./images/": f"(images/{safe_stem}/",
        "(images/": f"(images/{safe_stem}/",
        "src=\"./images/": f"src=\"images/{safe_stem}/",
        "src='./images/": f"src='images/{safe_stem}/",
        "src=\"images/": f"src=\"images/{safe_stem}/",
        "src='images/": f"src='images/{safe_stem}/",
    }
    out = md_text
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _copy_tree(src_dir: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src_path in src_dir.rglob("*"):
        rel = src_path.relative_to(src_dir)
        dst_path = dst_dir / rel
        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)


def main() -> int:
    args = parse_args()

    if not args.input_dir.exists():
        print(f"Error: input dir not found: {args.input_dir}")
        return 1

    pdf_files = sorted(args.input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"Error: no PDFs found in: {args.input_dir}")
        return 1

    if args.only:
        wanted: set[str] = set()
        for item in args.only:
            item = (item or "").strip()
            if not item:
                continue
            p = Path(item)
            if p.suffix.lower() == ".pdf":
                wanted.add(p.name.lower())
                wanted.add(p.stem.lower())
            else:
                wanted.add(item.lower())
        pdf_files = [p for p in pdf_files if p.name.lower() in wanted or p.stem.lower() in wanted]
        if not pdf_files:
            print("Error: no PDFs matched --only filters")
            return 1

    mineru_cli = _find_mineru_cli()
    if not mineru_cli:
        print("Warning: MinerU CLI not found; will use PyMuPDF fallback for all PDFs.")

    images_root = args.input_dir / "images"
    images_root.mkdir(parents=True, exist_ok=True)

    success = 0
    skipped = 0
    failed = 0

    print(f"Found {len(pdf_files)} PDFs")
    print(f"Backend: {args.backend}")
    print(f"Language: {args.language}")
    print(f"Output: in-place next to each PDF")
    if mineru_cli:
        print(f"MinerU CLI: {mineru_cli}")
    else:
        print("MinerU CLI: (not found; using PyMuPDF)")
    print("-" * 60)

    with tempfile.TemporaryDirectory(prefix="mineru_convert_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for idx, pdf_path in enumerate(pdf_files, 1):
            out_md = pdf_path.with_suffix(".md")
            pdf_stem = pdf_path.stem

            if out_md.exists() and not args.overwrite:
                skipped += 1
                print(f"[{idx}/{len(pdf_files)}] Skip (exists): {out_md.name}")
                continue

            try:
                print(f"[{idx}/{len(pdf_files)}] Converting: {pdf_path.name}")
                converted = False

                if mineru_cli:
                    cmd = [
                        str(mineru_cli),
                        "-p",
                        str(pdf_path),
                        "-o",
                        str(tmp_dir),
                        "-m",
                        "auto",
                        "-b",
                        args.backend,
                        "-l",
                        args.language,
                    ]
                    env = os.environ.copy()
                    # MinerU 2.7.x may crash/bug when torch isn't installed and it tries
                    # to auto-detect GPU. Forcing device mode avoids that code path.
                    env.setdefault("MINERU_DEVICE_MODE", "cpu")
                    env.setdefault("MINERU_VIRTUAL_VRAM_SIZE", "0")

                    completed = subprocess.run(cmd, capture_output=True, text=True, env=env)
                    if completed.returncode == 0:
                        md_candidates = list(tmp_dir.glob(f"{pdf_stem}/**/{pdf_stem}.md"))
                        if md_candidates:
                            md_src = md_candidates[0]
                            images_src = md_src.parent / "images"

                            md_text = md_src.read_text(encoding="utf-8", errors="replace")
                            md_text = _rewrite_image_paths(md_text, pdf_stem)
                            md_text = _dedupe_repeated_disclaimers(md_text)
                            md_text = _format_where_blocks(md_text)
                            md_text = _reorder_figures_and_tables(md_text)
                            md_text = _apply_unit_superscripts(md_text)
                            md_text = _center_figures_and_tables(md_text)
                            out_md.write_text(md_text, encoding="utf-8")

                            if images_src.exists():
                                images_dst = images_root / pdf_stem
                                _copy_tree(images_src, images_dst)

                            converted = True
                        else:
                            print("  ! MinerU ran but output MD not found; falling back to PyMuPDF")
                    else:
                        if completed.stderr and _mineru_error_hints_torch_missing(completed.stderr):
                            print("  ! MinerU failed (torch missing); falling back to PyMuPDF")
                        else:
                            print("  ! MinerU failed; falling back to PyMuPDF")
                            if completed.stderr:
                                print(completed.stderr.strip())

                if not converted:
                    _convert_with_pymupdf_layout(pdf_path, out_md, images_root)
                    converted = True

                success += 1
                print(f"  [OK] Wrote: {out_md.name}")
            except Exception as exc:
                failed += 1
                print(f"  [FAIL] Failed: {exc}")

    print("-" * 60)
    print(f"Done. success={success}, skipped={skipped}, failed={failed}")

    if failed:
        return 2
    if skipped:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
