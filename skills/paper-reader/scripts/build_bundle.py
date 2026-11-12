#!/usr/bin/env python3
"""Build a per-paper folder: PDF, offline HTML, note, reproducible inputs and QA."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import tempfile

from build_reader import build, check_html
from reader_core import blocks_of, soup


VERSION_TOKEN = r"(?:v\d+|\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z]+(?:19|20)\d{2}(?:_\d+)*)"
VERSION_SUFFIX = re.compile(r"(?:[-_]" + VERSION_TOKEN + r")+$")
BARE_VERSION = re.compile(r"^" + VERSION_TOKEN + r"$")


def paper_slug(value):
    """Return the file stem for one paper, keeping its original case: FinTSB_2502.18834v3 -> FinTSB."""
    text = str(value).strip()
    if BARE_VERSION.match(text):
        return "paper"
    while VERSION_SUFFIX.search(text):
        text = VERSION_SUFFIX.sub("", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r'[\\/:*?"<>|]+', "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text or "paper"


def bundle_slug(destination, source, explicit=None):
    """Name files after the paper; explicit slug, source metadata, then folder name."""
    for candidate in (explicit, source.get("slug"), source.get("short_name")):
        if candidate:
            return paper_slug(candidate)
    return paper_slug(Path(destination).name)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_bundle(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "05_检查/bundle_manifest.json").read_text(encoding="utf-8"))
    entry = manifest.get("entry") or ("02_阅读/" + (manifest.get("slug") or "reading") + ".html")
    errors = []
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if root not in path.parents or not path.is_file() or digest(path) != item["sha256"]:
            errors.append("Missing or changed bundle file: " + item["path"])
    report = check_html(root / entry)
    errors.extend(report["errors"])
    doc = soup((root / entry).read_text(encoding="utf-8"))
    record = json.loads(doc.find(id="reader-manifest").string).get("paired_pdf")
    if record:
        pdf = (root / "02_阅读" / record["path"]).resolve()
        if root not in pdf.parents or not pdf.is_file() or digest(pdf) != record["sha256"]:
            errors.append("Paired PDF mismatch")
    return {"ok": not errors, "errors": errors, "files": len(manifest["files"]), "blocks": report["blocks"]}


def build_bundle(source_path, annotations_path, destination, pdf=None, embed_pdf=False, source_html=None, slug=None):
    source_path, annotations_path = Path(source_path).resolve(), Path(annotations_path).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Output folder exists; choose a new version instead of overwriting: " + str(destination))
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if embed_pdf and not pdf:
        raise ValueError("--embed-pdf needs --pdf")
    stem = bundle_slug(destination, source, slug)
    html_name, pdf_name, qa_name = stem + ".html", stem + ".pdf", stem + ".qa.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reader-bundle-", dir=destination.parent) as temp:
        root = Path(temp) / "bundle"
        for folder in ["01_原文", "02_阅读", "03_笔记", "04_生成", "05_检查"]:
            (root / folder).mkdir(parents=True)
        shutil.copy2(source_path, root / "04_生成/source.json")
        shutil.copy2(annotations_path, root / "04_生成/annotations.json")
        for block in blocks_of(source):
            for image in soup(block["html"]).find_all("img"):
                src = image.get("src", "")
                if src.startswith("data:"):
                    continue
                if re.match(r"[A-Za-z]+:", src) or src.startswith("//"):
                    raise ValueError("Save remote source images locally before bundling")
                image_path = (source_path.parent / src).resolve()
                if source_path.parent not in image_path.parents or not image_path.is_file():
                    raise ValueError("Asset missing or outside source folder: " + src)
                target = root / "04_生成" / image_path.relative_to(source_path.parent)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image_path, target)
        if source_html:
            shutil.copy2(source_html, root / "01_原文/source.html")
        paired_pdf = None
        pdf_link = None
        if pdf:
            paired_pdf = root / "01_原文" / pdf_name
            pdf_link = "../01_原文/" + pdf_name
            shutil.copy2(pdf, paired_pdf)
        output = root / "02_阅读" / html_name
        build(root / "04_生成/source.json", root / "04_生成/annotations.json", output,
              paired_pdf if embed_pdf else None, pdf_link)
        shutil.move(output.with_suffix(".qa.json"), root / "05_检查" / qa_name)
        (root / "03_笔记/阅读笔记.md").write_text(
            "# " + source["title"] + "\n\n[双语批注 HTML](../02_阅读/" + html_name + ")" +
            (" · [原论文 PDF](../01_原文/" + pdf_name + ")" if pdf else "") +
            "\n\n## 核心主张\n\n## 证据与边界\n\n## 对我的研究有什么用\n\n## 待核查\n", encoding="utf-8")
        (root / "00_导读.md").write_text(
            "# " + source["title"] + "\n\n[打开批注 HTML](02_阅读/" + html_name + ") · [阅读笔记](03_笔记/阅读笔记.md)" +
            (" · [打开原 PDF](01_原文/" + pdf_name + ")" if pdf else "\n\n原 PDF 尚未收集；不得声称已交付配套 PDF。") +
            "\n\n每篇论文一个文件夹。01 是来源，02 是阅读成品，03 是自己的笔记，04 保留源清单与译注，05 保留检查记录。复制整个文件夹可保持相对链接。\n\n" +
            "重建：在本文件夹运行 `python <paper-reader>/scripts/build_reader.py --source 04_生成/source.json --annotations 04_生成/annotations.json --output 02_阅读/" + html_name +
            (" --pdf-link ../01_原文/" + pdf_name if pdf else "") + "`。检查记录重新生成在 HTML 旁，归入 05_检查即可。\n", encoding="utf-8")
        manifest = {"schema_version": 1, "title": source["title"], "mode": source["mode"],
                    "slug": stem, "entry": "02_阅读/" + html_name, "files": [
            {"path": p.relative_to(root).as_posix(), "sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file()]}
        (root / "05_检查/bundle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = check_bundle(root)
        if not report["ok"]:
            raise ValueError("Bundle validation failed: " + str(report["errors"]))
        root.rename(destination)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path)
    p.add_argument("--annotations", type=Path)
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--slug", help="File stem for the HTML/PDF, e.g. fintsb; defaults to the output folder name")
    p.add_argument("--pdf", type=Path)
    p.add_argument("--source-html", type=Path)
    p.add_argument("--embed-pdf", action="store_true")
    p.add_argument("--check", type=Path)
    args = p.parse_args()
    try:
        if args.check:
            report = check_bundle(args.check)
        elif args.source and args.annotations and args.output_dir:
            report = build_bundle(args.source, args.annotations, args.output_dir, args.pdf, args.embed_pdf, args.source_html, args.slug)
        else:
            p.error("provide --source, --annotations and --output-dir; or --check")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise SystemExit(1)
    except (ValueError, KeyError, OSError) as exc:
        p.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
