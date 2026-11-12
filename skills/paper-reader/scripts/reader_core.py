"""Shared source-preservation and offline HTML mechanics; no model calls."""
from pathlib import Path
import base64
import hashlib
import re

from bs4 import BeautifulSoup, NavigableString

KINDS = {"thesis": "核心论点", "concept": "关键概念", "evidence": "实证证据",
         "concession": "让步与反驳", "method": "方法说明"}
BLOCK_KINDS = {"paragraph", "figure", "table", "equation", "box", "reference"}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
FORBIDDEN = "script,style,iframe,object,embed,link,meta,base,form,input,button,textarea,select"
HTML_TAGS = set("p div span a em strong b i u s sub sup br hr blockquote pre code ul ol li dl dt dd table caption thead tbody tfoot tr td th figure figcaption img h1 h2 h3 h4 h5 h6 small abbr cite q mark".split())
MATH_TAGS = set("math mrow mi mn mo ms mtext mspace mfrac msqrt mroot msub msup msubsup munder mover munderover mtable mtr mtd mfenced menclose mstyle mpadded mphantom semantics mmultiscripts mprescripts none".split())
SVG_TAGS = set("svg g path rect circle ellipse line polyline polygon text tspan defs marker clippath title desc".split())
ATTRS = set("id class href title lang dir colspan rowspan scope alt src width height viewbox xmlns d fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin x y x1 x2 y1 y2 cx cy r rx ry points transform text-anchor font-size font-family dominant-baseline opacity clip-path marker-end marker-start orient refx refy markerwidth markerheight display mathvariant mathsize stretchy fence separator lspace rspace encoding columnalign rowalign columnspacing rowspacing accent accentunder linethickness notation open close separators".split())


def soup(markup):
    return BeautifulSoup(markup, "html.parser")


def reflow_frames(doc):
    """Keep ALL nested foreignObject contents; replace span table geometry."""
    for svg in reversed(list(doc.find_all("svg"))):
        frames = [f for f in svg.find_all("foreignobject") if f.find_parent("svg") is svg]
        if frames:
            inner = "".join((f.find(class_="ltx_foreignobject_content") or f).decode_contents()
                            for f in frames)
            svg.replace_with(soup('<div class="reflowed-box">' + inner + '</div>'))
    mapping = {"ltx_tabular": "table", "ltx_tr": "tr", "ltx_td": "td", "ltx_th": "th",
               "ltx_thead": "thead", "ltx_tbody": "tbody", "ltx_itemize": "ul",
               "ltx_enumerate": "ol", "ltx_item": "li", "ltx_para": "div", "ltx_p": "div",
               "ltx_minipage": "div", "ltx_inline-block": "div"}
    for tag in doc.find_all("span"):
        for cls in tag.get("class", []):
            if cls in mapping:
                tag.name = mapping[cls]
                break


def clean_markup(markup, asset_root=None):
    doc = soup(markup)
    for tag in list(doc.select(FORBIDDEN + ",annotation,annotation-xml")):
        if tag.name is not None:
            tag.decompose()
    reflow_frames(doc)
    for tag in list(doc.find_all(True)):
        if tag.name not in HTML_TAGS | MATH_TAGS | SVG_TAGS:
            tag.unwrap()
            continue
        for key in list(tag.attrs):
            if key.lower() not in ATTRS or key.lower().startswith("on"):
                del tag.attrs[key]
        if tag.name == "mark":
            tag.unwrap()  # annotation colors are supplied separately
            continue
        for key in ["fill", "stroke", "clip-path", "marker-end", "marker-start"]:
            value = tag.get(key, "")
            if "url(" in value and not re.fullmatch(r"url\(#[A-Za-z][\w.:-]*\)", value):
                del tag.attrs[key]
        if tag.name == "a":
            href = tag.get("href", "")
            if href.startswith(("https://", "http://")):
                tag["rel"] = "noopener noreferrer"
                tag["target"] = "_blank"
            elif not href.startswith("#"):
                tag.attrs.pop("href", None)
        if tag.name == "img":
            src = tag.get("src", "")
            allowed = re.fullmatch(r"data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=\s]+)", src)
            if allowed:
                base64.b64decode(allowed.group(2), validate=True)
            elif asset_root is not None:
                if re.match(r"[a-zA-Z]+:", src) or src.startswith("//"):
                    raise ValueError("Image must be local or embedded: " + src[:100])
                root = Path(asset_root).resolve()
                image_path = (root / src).resolve()
                if root not in image_path.parents or not image_path.is_file():
                    raise ValueError("Image is missing or outside source directory: " + src)
                mime = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}.get(image_path.suffix.lower())
                if mime is None:
                    raise ValueError("Use PNG/JPEG/GIF/WebP, or sanitized inline SVG: " + src)
                tag["src"] = "data:image/" + mime + ";base64," + base64.b64encode(image_path.read_bytes()).decode()
            tag["alt"] = tag.get("alt", "")
            tag["loading"] = "lazy"
    return str(doc)


def plain_text(markup):
    # Same normalization before/after highlighting; annotation TeX is removed once.
    return soup(clean_markup(markup)).get_text(" ", strip=True)


def text_digest(markup):
    normalized = re.sub(r"\s+", "", plain_text(markup))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def blocks_of(source):
    return [b for section in source["sections"] for b in section["blocks"]]


def seal_source(source):
    for block in blocks_of(source):
        block["text_sha256"] = text_digest(block["html"])
    return source


def highlight(markup, specs):
    doc = soup(markup)
    hits = [0] * len(specs)
    for spec in specs:
        if not spec.get("text") or spec.get("kind") not in KINDS:
            raise ValueError("Highlight needs exact text and a supported kind")
    for node in list(doc.find_all(string=True)):
        if node.find_parent(["math", "svg", "mark"]):
            continue
        value = str(node)
        matches = []
        for i, spec in enumerate(specs):
            for match in re.finditer(re.escape(spec["text"]), value):
                if any(match.start() < end and start < match.end() for start, end, _ in matches):
                    raise ValueError("Overlapping highlights: " + spec["text"])
                matches.append((match.start(), match.end(), i))
        if not matches:
            continue
        offset = 0
        for start, end, i in sorted(matches):
            node.insert_before(NavigableString(value[offset:start]))
            mark = doc.new_tag("mark")
            mark["data-kind"] = specs[i]["kind"]
            mark["title"] = KINDS[specs[i]["kind"]]
            mark.string = value[start:end]
            node.insert_before(mark)
            hits[i] += 1
            offset = end
        node.insert_before(NavigableString(value[offset:]))
        node.extract()
    if any(count == 0 for count in hits):
        raise ValueError("Highlight not found in one text node: " + "; ".join(specs[i]["text"] for i, n in enumerate(hits) if not n))
    return str(doc)


def validate_inputs(source, annotations):
    if source.get("schema_version") != 1 or annotations.get("schema_version") != 1:
        raise ValueError("Only schema_version 1 is supported")
    mode = source.get("mode")
    if mode not in {"full", "excerpt", "summary", "demo"}:
        raise ValueError("mode must be full/excerpt/summary/demo")
    if not source.get("title") or not source.get("sections"):
        raise ValueError("Title and nonempty sections required")
    origin = source.get("source", {})
    if mode != "demo" and (not origin.get("version") or not (origin.get("url") or origin.get("sha256"))):
        raise ValueError("Record exact source version plus URL or input SHA-256")
    if origin.get("url") and not origin["url"].startswith(("https://", "http://")):
        raise ValueError("Source URL must use HTTP(S)")
    review = source.get("review", {})
    if mode == "full" and (review.get("status") != "verified" or not review.get("basis", "").strip()):
        raise ValueError("Full reading needs a verified inventory with human review basis")
    used = {"overview", "top", "reader-manifest", "pdf-data"}
    blocks = blocks_of(source)
    if not blocks:
        raise ValueError("Source inventory is empty")
    for item in list(source["sections"]) + blocks:
        ident = item.get("id", "")
        if not ID_RE.fullmatch(ident) or ident in used:
            raise ValueError("Invalid or duplicate ID: " + ident)
        used.add(ident)
    for section in source["sections"]:
        if not section.get("title") or not section.get("blocks"):
            raise ValueError("Every section needs title and blocks")
    ids = {b["id"] for b in blocks}
    supplied = set(annotations.get("blocks", {}))
    if ids != supplied:
        raise ValueError("Annotation coverage mismatch; missing=" + str(sorted(ids - supplied)) + "; extra=" + str(sorted(supplied - ids)))
    for block in blocks:
        if block.get("kind") not in BLOCK_KINDS or not block.get("html", "").strip():
            raise ValueError("Invalid block: " + block["id"])
        if block.get("text_sha256") != text_digest(block["html"]):
            raise ValueError("Original text changed or not sealed: " + block["id"])
        note = annotations["blocks"][block["id"]]
        for field in ["translation", "function", "role", "critique"]:
            if not isinstance(note.get(field), str) or not note[field].strip():
                raise ValueError("Missing " + field + " for " + block["id"])
        if note.get("dimension") not in KINDS:
            raise ValueError("Unknown annotation dimension: " + block["id"])
    for key in ["question", "thesis", "evidence", "rebuttal", "conclusion", "strongest", "weakest"]:
        if not annotations.get("overview", {}).get(key, "").strip():
            raise ValueError("Missing argument overview: " + key)
    return blocks
