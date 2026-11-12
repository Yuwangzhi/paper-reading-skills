#!/usr/bin/env python3
"""Build a provisional paper inventory, or seal a manually corrected one."""
from pathlib import Path
import argparse
import hashlib
import html
import json

from reader_core import clean_markup, seal_source, soup

DEFAULT_BLOCKS = "p,figure,table,.ltx_equation,.ltx_equationgroup,.ltx_framed,.ltx_listing,.ltx_bibitem,blockquote,pre"


def extract_html(path, content_selector="article", block_selector=DEFAULT_BLOCKS):
    doc = soup(path.read_text(encoding="utf-8"))
    root = doc.select_one(content_selector) or doc.body or doc
    for tag in list(root.select("script,style,nav,header,footer,.ltx_authors,.ltx_note_mark")):
        tag.decompose()
    candidates = root.select(block_selector)
    candidate_ids = {id(tag) for tag in candidates}
    picked = {id(tag) for tag in candidates if not any(id(p) in candidate_ids for p in tag.parents)}
    sections = [{"id": "section-001", "title": "Front matter / 前置内容", "blocks": []}]
    occupied = set()
    for tag in root.descendants:
        if not getattr(tag, "name", None):
            continue
        if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"} and not any(id(p) in picked for p in tag.parents):
            title = tag.get_text(" ", strip=True)
            if title:
                sections.append({"id": "section-%03d" % (len(sections) + 1), "title": title, "blocks": []})
        elif id(tag) in picked:
            classes = set(tag.get("class", []))
            kind = "paragraph"
            if tag.name == "figure":
                kind = "table" if "ltx_table" in classes else "figure"
            elif tag.name == "table":
                kind = "equation" if any("equation" in c for c in classes) else "table"
            elif any("equation" in c for c in classes):
                kind = "equation"
            elif "ltx_bibitem" in classes:
                kind = "reference"
            elif tag.name in {"blockquote", "pre"} or classes & {"ltx_framed", "ltx_listing"}:
                kind = "box"
            original_id = tag.get("id")
            ident = "block-%04d" % (len(occupied) + 1)
            occupied.add(ident)
            # Keep source IDs inside original markup, preserving citation links.
            sections[-1]["blocks"].append({"id": ident, "kind": kind,
                "locator": "HTML #" + original_id if original_id else "HTML " + ident,
                "html": clean_markup(str(tag))})
    return [s for s in sections if s["blocks"]]


def extract_pdf(path):
    try:
        import fitz
    except ImportError as exc:
        raise SystemExit("PDF extraction needs PyMuPDF: python -m pip install 'PyMuPDF>=1.24,<2'") from exc
    sections = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc):
            blocks = []
            for block_index, block in enumerate(page.get_text("blocks")):
                if block[6] != 0 or not block[4].strip():
                    continue
                blocks.append({"id": "p%03d-b%03d" % (page_index + 1, block_index + 1),
                    "kind": "paragraph", "locator": "PDF p.%d block %d — order/segmentation unverified" % (page_index + 1, block_index + 1),
                    "html": "<p>" + html.escape(block[4].strip()) + "</p>"})
            if blocks:
                sections.append({"id": "page-%03d" % (page_index + 1),
                                 "title": "Page %d / 待校对逻辑顺序" % (page_index + 1), "blocks": blocks})
    return sections


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--seal", type=Path, help="Update text hashes after correcting source extraction")
    p.add_argument("--url", default="")
    p.add_argument("--version", default="unverified")
    p.add_argument("--content-selector", default="article")
    p.add_argument("--block-selector", default=DEFAULT_BLOCKS)
    args = p.parse_args()
    if args.seal:
        source = json.loads(args.seal.read_text(encoding="utf-8"))
        args.seal.write_text(json.dumps(seal_source(source), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Sealed text hashes; human coverage review is still required.")
        return
    if not args.input or not args.output:
        p.error("provide --input and --output, or --seal")
    path = args.input.resolve()
    sections = extract_pdf(path) if path.suffix.lower() == ".pdf" else extract_html(path, args.content_selector, args.block_selector)
    source = {"schema_version": 1, "mode": "excerpt", "title": path.stem,
        "title_zh": "", "course": "论文精读 / 研究讨论",
        "source": {"url": args.url, "version": args.version, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        "review": {"status": "draft", "basis": "自动提取初稿：段落顺序、图表、公式、脚注及附录尚待逐项核对"},
        "sections": sections}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve local asset paths when inventory is saved beside a different directory.
    for section in sections:
        for block in section["blocks"]:
            doc = soup(block["html"])
            if doc.find("img"):
                block["html"] = clean_markup(block["html"], path.parent)
    args.output.write_text(json.dumps(seal_source(source), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Draft inventory:", sum(len(s["blocks"]) for s in sections), "blocks; NOT full coverage verified.")


if __name__ == "__main__":
    main()
