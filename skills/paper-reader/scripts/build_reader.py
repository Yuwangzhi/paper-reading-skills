#!/usr/bin/env python3
"""Render a source inventory and human-authored annotations to one offline HTML."""
from pathlib import Path
from collections import Counter
import argparse
import base64
import hashlib
import html
import json
import re

from reader_core import KINDS, blocks_of, clean_markup, highlight, soup, text_digest, validate_inputs

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MODE_LABELS = {"full": "全文批注", "excerpt": "节选批注 · 非全文", "summary": "分析者撰写的文献总结 · 非原文", "demo": "自写教学示例 · 非研究结果"}


def esc(value):
    return html.escape(str(value), quote=True)


def json_script(value):
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def check_html(path):
    doc = soup(Path(path).read_text(encoding="utf-8"))
    errors = []
    meta_tag = doc.find(id="reader-manifest")
    if meta_tag is None:
        return {"ok": False, "errors": ["Missing embedded source manifest"]}
    manifest = json.loads(meta_tag.string)
    all_ids = [t["id"] for t in doc.select("[id]")]
    ids = set(all_ids)
    if len(ids) != len(all_ids):
        errors.append("Duplicate DOM IDs")
    for tag in doc.select("[href]"):
        href = tag["href"]
        if href.startswith("#") and href[1:] not in ids:
            errors.append("Broken anchor: " + href)
    for tag in doc.select("[src]"):
        if not tag["src"].startswith("data:"):
            errors.append("Non-embedded resource: " + tag["src"][:80])
    for tag in doc.find_all(True):
        if any(key.lower().startswith("on") for key in tag.attrs):
            errors.append("Event handler in generated HTML")
    for tag in doc.select("link,iframe,object,embed,base,style[src]"):
        errors.append("Unexpected network-capable resource tag: " + tag.name)
    css = "\n".join(t.get_text() for t in doc.find_all("style"))
    if re.search(r"@import|url\(\s*['\"]?(?!data:|#)", css, re.I):
        errors.append("External CSS resource")
    rows = doc.select(".reading-row")
    expected = {b["id"]: b for b in manifest["blocks"]}
    actual = {r.get("data-block-id"): r for r in rows}
    if set(expected) != set(actual) or len(rows) != len(expected):
        errors.append("Rendered block inventory mismatch")
    for ident, row in actual.items():
        en = row.select_one(".english")
        zh = row.select_one(".translation")
        if ident not in expected:
            continue
        if en is None or text_digest(str(en)) != expected[ident]["text_sha256"]:
            errors.append("Rendered original text changed: " + ident)
        if zh is None or not zh.get_text(strip=True) or zh.find("mark"):
            errors.append("Translation missing or highlighted: " + ident)
        if len(row.select(".annotation dd")) != 3 or any(not d.get_text(strip=True) for d in row.select(".annotation dd")):
            errors.append("Incomplete annotation: " + ident)
    pdf = doc.find(id="pdf-data")
    pdf_hash = None
    if pdf:
        pdf_hash = hashlib.sha256(base64.b64decode(pdf.string)).hexdigest()
        if pdf_hash != manifest.get("pdf_sha256"):
            errors.append("Embedded PDF hash mismatch")
    return {"ok": not errors, "errors": errors, "mode": manifest["mode"],
            "counts": dict(Counter(b["kind"] for b in expected.values())),
            "blocks": len(rows), "source_sha256": manifest.get("source", {}).get("sha256"),
            "pdf_sha256": pdf_hash, "scope": "inventory-to-HTML structural verification; not semantic or visual review"}


def render_translation(text):
    """Render Chinese translation paragraphs, preserving bullets and numbering."""
    out = []
    for para in str(text).split("\n\n"):
        lines = [line.strip() for line in para.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith(("- ", "• ")) for line in lines):
            out.append("<ul>" + "".join("<li>" + esc(line[2:].strip()) + "</li>" for line in lines) + "</ul>")
        elif all(re.match(r"^\d+[.)]\s", line) for line in lines):
            out.append("<ol>" + "".join("<li>" + esc(re.sub(r"^\d+[.)]\s*", "", line)) + "</li>" for line in lines) + "</ol>")
        else:
            out.append("<p>" + esc(para).replace("\n", "<br>") + "</p>")
    return "".join(out)


def build(source_path, annotations_path, output, pdf_path=None, pdf_link=None):
    source_path, annotations_path, output = map(Path, [source_path, annotations_path, output])
    source = json.loads(source_path.read_text(encoding="utf-8"))
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    blocks = validate_inputs(source, annotations)
    # Prefix source IDs to keep source citation anchors separate from UI IDs.
    source_ids = {}
    normalized = {}
    for block in blocks:
        doc = soup(clean_markup(block["html"], source_path.parent))
        for tag in doc.select("[id]"):
            ident = tag["id"]
            if ident in source_ids:
                raise ValueError("Repeated source HTML ID: " + ident)
            source_ids[ident] = "src-" + ident
        normalized[block["id"]] = doc
    for doc in normalized.values():
        for tag in doc.select("[id]"):
            tag["id"] = source_ids[tag["id"]]
        for tag in doc.select("a[href^='#']"):
            target = tag["href"][1:]
            tag["href"] = "#" + source_ids.get(target, "MISSING-" + target)
        # Local SVG definitions and their paint references must stay paired.
        for tag in doc.find_all(True):
            for attr in ["fill", "stroke", "clip-path", "marker-end", "marker-start"]:
                if attr in tag.attrs:
                    tag[attr] = re.sub(r"url\(#([\w.:-]+)\)", lambda m: "url(#" + source_ids.get(m[1], m[1]) + ")", tag[attr])
    counts = Counter(b["kind"] for b in blocks)
    manifest = {"schema_version": 1, "title": source["title"], "mode": source["mode"],
                "source": source.get("source", {}), "review": source.get("review", {}),
                "blocks": [{key: b.get(key) for key in ["id", "kind", "locator", "text_sha256"]} for b in blocks]}
    pdf_data = ""
    paired_pdf = ""
    if pdf_link:
        if re.match(r"[A-Za-z]+:", pdf_link) or Path(pdf_link).is_absolute():
            raise ValueError("--pdf-link must be a relative local PDF path")
        paired_path = output.parent / pdf_link
        if not paired_path.is_file() or not paired_path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Paired PDF missing or invalid: " + pdf_link)
        manifest["paired_pdf"] = {"path": pdf_link, "sha256": hashlib.sha256(paired_path.read_bytes()).hexdigest()}
        paired_pdf = '<a class="paired-pdf" href="' + esc(pdf_link) + '" target="_blank" rel="noopener noreferrer">打开配套 PDF ↗</a>'
    if pdf_path:
        pdf_bytes = Path(pdf_path).read_bytes()
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("--pdf input is not a PDF")
        manifest["pdf_sha256"] = hashlib.sha256(pdf_bytes).hexdigest()
        pdf_data = '<script id="pdf-data" type="application/octet-stream">' + base64.b64encode(pdf_bytes).decode() + '</script>'
    parts = []
    for section in source["sections"]:
        parts.append('<section class="paper-section" id="sec-' + esc(section["id"]) + '"><h2>' + esc(section["title"]) + '</h2>')
        for block in section["blocks"]:
            note = annotations["blocks"][block["id"]]
            original = highlight(str(normalized[block["id"]]), note.get("highlights", []))
            if text_digest(original) != block["text_sha256"]:
                raise ValueError("Sanitizing/highlighting changed text: " + block["id"])
            zh = render_translation(note["translation"])
            caption = "分析者英文总结" if source["mode"] == "summary" else "英文原文"
            if source["mode"] == "demo":
                caption = "自写英文示例"
            parts.append(f'''<div class="reading-row" id="row-{esc(block['id'])}" data-block-id="{esc(block['id'])}" data-kind="{esc(block['kind'])}">
<div class="source-cell"><div class="row-meta">{esc(block['kind'])} · {esc(block.get('locator', block['id']))} <a href="#row-{esc(block['id'])}" aria-label="定位本段">#</a></div>
<div class="english-label">{caption}</div><div class="english" lang="en">{original}</div>
<div class="translation" lang="zh-CN"><span class="translation-label">中文译文</span>{zh}</div></div>
<aside class="annotation {esc(note['dimension'])}" aria-label="本段中文批注"><span class="note-kicker">{KINDS[note['dimension']]} · 论证批注</span><dl>
<dt>这段在做什么</dt><dd>{esc(note['function'])}</dd><dt>在全文里的作用</dt><dd>{esc(note['role'])}</dd><dt>读的时候要留意</dt><dd>{esc(note['critique'])}</dd></dl></aside></div>''')
        parts.append('</section>')
    overview = annotations["overview"]
    chain = "".join('<div class="chain-step"><span>' + name + '</span><p>' + esc(overview[key]) + '</p></div>'
                    for key, name in [("question", "01 问题"), ("thesis", "02 论点"), ("evidence", "03 证据"), ("rebuttal", "04 反驳 / 边界"), ("conclusion", "05 结论")])
    summary = '<section id="overview" class="overview"><p class="eyebrow">ARGUMENT MAP</p><h2>把全文连成一条论证链</h2><div class="argument-chain">' + chain + '</div><div class="core-claim"><span>核心主张 · 一句话</span><p>' + esc(overview["thesis"]) + '</p></div><div class="strength-grid"><div><h3>论证最强处</h3><p>' + esc(overview["strongest"]) + '</p></div><div><h3>论证最弱处</h3><p>' + esc(overview["weakest"]) + '</p></div></div></section>'
    fonts = (ASSETS / "fonts/fonts.css").read_text(encoding="utf-8")
    fonts = re.sub(r"ASSET:([^\s)]+)", lambda m: "data:font/woff2;base64," + base64.b64encode((ASSETS / "fonts" / m[1]).read_bytes()).decode(), fonts)
    css = fonts + "\n" + (ASSETS / "reader.css").read_text(encoding="utf-8")
    js = (ASSETS / "reader.js").read_text(encoding="utf-8")
    font_licenses = '<details class="font-licenses"><summary>字体版权与 OFL 许可</summary>' + ''.join(
        '<h3>' + esc(name) + '</h3><pre>' + esc((ASSETS / "fonts" / filename).read_text(encoding="utf-8")) + '</pre>'
        for name, filename in [("Lora", "Lora-OFL.txt"), ("IBM Plex Sans", "IBMPlexSans-OFL.txt")]) + '</details>'
    toc = "".join('<a href="#sec-' + esc(s["id"]) + '">' + esc(s["title"]) + '</a>' for s in source["sections"]) + '<a href="#overview">论证总览</a>'
    legend = "".join('<button type="button" class="legend-button" data-kind="' + kind + '" aria-pressed="true"><span class="swatch ' + kind + '"></span>' + name + '</button>' for kind, name in KINDS.items())
    url = source.get("source", {}).get("url", "")
    source_link = '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">核对来源 ↗</a>' if url else ''
    count_text = " · ".join(kind + " " + str(n) for kind, n in counts.items())
    metrics = ('<div class="hero-metrics">'
               '<div><strong>' + str(len(blocks)) + '</strong><span>阅读单元</span></div>'
               '<div><strong>' + str(counts.get("figure", 0) + counts.get("table", 0) + counts.get("equation", 0)) + '</strong><span>图表 / 公式</span></div>'
               '<div><strong>' + str(counts.get("box", 0)) + '</strong><span>原文框 / 案例</span></div>'
               '</div>')
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(source['title'])} · 双语批注</title><style>{css}</style></head>
<body id="top"><div class="reading-progress" aria-hidden="true"></div><header class="hero"><div class="hero-inner"><div class="hero-top"><span class="brand">PAPER READER</span><span class="scope-badge">{MODE_LABELS[source['mode']]}</span></div><p class="course">{esc(source.get('course') or '论文精读 / 研究讨论')}</p><h1>{esc(source['title'])}</h1><p class="subtitle">{esc(source.get('title_zh', ''))}</p><p class="hero-description">左边读英文原文和中文意译，右边看这一段为什么放在这里、哪里值得追问。</p><div class="legend" aria-label="高亮颜色图例">{legend}</div>{metrics}<div class="hero-stats">{len(source['sections'])} 节 · {esc(source.get('source', {}).get('version', '自写演示'))}</div></div></header>
<nav class="section-nav" aria-label="章节导航"><div class="section-links">{toc}</div><div class="reader-tools"><label class="sr-only" for="reader-search">搜索原文、译文与批注</label><input id="reader-search" type="search" placeholder="搜索原文、译文、批注"><label class="sr-only" for="reading-mode">语言模式</label><select id="reading-mode"><option value="bi">英中对照</option><option value="en">只看英文</option><option value="zh">只看中文</option></select>{paired_pdf}<button type="button" id="download-pdf" {'hidden' if not pdf_path else ''}>下载原 PDF</button></div></nav>
<main><div class="column-labels"><span>原文与中文译文</span><span>中文批注 · 功能 / 位置 / 核查</span></div><p id="search-status" role="status" hidden></p>{''.join(parts)}{summary}<footer class="source-footer"><p>{MODE_LABELS[source['mode']]} · {esc(count_text)}</p><p>{esc(source.get('review', {}).get('basis', ''))}</p><p>来源版本：{esc(source.get('source', {}).get('version', '自写演示'))} {source_link}</p><p>输入源 SHA-256：{esc(source.get('source', {}).get('sha256') or '演示，无外部论文')}</p><p>结构检查只核对清单到页面，不替代全文语义与视觉核查。字体：Lora / IBM Plex Sans（OFL），中文使用系统字体。</p>{font_licenses}<a href="#top">返回顶部 ↑</a></footer></main><script id="reader-manifest" type="application/json">{json_script(manifest)}</script>{pdf_data}<script>{js}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
    report = check_html(output)
    with output.with_suffix(".qa.json").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["ok"]:
        raise ValueError("Generated HTML failed checks: " + "; ".join(report["errors"]))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path)
    p.add_argument("--annotations", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--pdf", type=Path)
    p.add_argument("--pdf-link", help="Relative path to a separately stored companion PDF")
    p.add_argument("--check", type=Path)
    args = p.parse_args()
    try:
        if args.check:
            report = check_html(args.check)
        elif args.source and args.annotations and args.output:
            report = build(args.source, args.annotations, args.output, args.pdf, args.pdf_link)
        else:
            p.error("provide --source, --annotations and --output; or --check")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise SystemExit(1)
    except (ValueError, KeyError, OSError) as exc:
        p.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
