"""Source preservation and completeness failures; not model/translation evals."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/paper-reader"
sys.path.insert(0, str(SKILL / "scripts"))
from reader_core import clean_markup, highlight, seal_source, soup, text_digest, validate_inputs
from extract_source import extract_html
from build_reader import build, check_html
from build_bundle import build_bundle, check_bundle, paper_slug


class PaperReader(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((SKILL / "assets/example-source.json").read_text())
        self.notes = json.loads((SKILL / "assets/example-annotations.json").read_text())

    def test_full_demo_roundtrip_preserves_every_block(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "reader.html"
            report = build(SKILL / "assets/example-source.json", SKILL / "assets/example-annotations.json", output)
            self.assertTrue(report["ok"])
            self.assertEqual(report["blocks"], sum(len(s["blocks"]) for s in self.source["sections"]))
            self.assertEqual(set(report["counts"]), {"paragraph", "table", "equation", "box", "figure", "reference"})
            self.assertTrue(check_html(output)["ok"])
            self.assertIn("SIL OPEN FONT LICENSE", output.read_text())

    def test_missing_or_extra_annotation_stops_build(self):
        for mutation in [lambda n: n["blocks"].pop(next(iter(n["blocks"]))),
                         lambda n: n["blocks"].update({"unknown": {}})]:
            n = copy.deepcopy(self.notes)
            mutation(n)
            with self.assertRaisesRegex(ValueError, "coverage mismatch"):
                validate_inputs(self.source, n)

    def test_changed_original_cannot_silently_reseal(self):
        self.source["sections"][0]["blocks"][0]["html"] += " changed"
        with self.assertRaisesRegex(ValueError, "Original text changed"):
            validate_inputs(self.source, self.notes)

    def test_full_label_requires_human_coverage_basis(self):
        self.source["mode"] = "full"
        self.source["source"] = {"url": "https://example.org/v2", "version": "v2"}
        self.source["review"] = {"status": "draft"}
        with self.assertRaisesRegex(ValueError, "human review basis"):
            validate_inputs(self.source, self.notes)

    def test_repeated_ids_and_empty_translation_stop_build(self):
        self.source["sections"][0]["blocks"][0]["id"] = self.source["sections"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            validate_inputs(self.source, self.notes)
        self.setUp()
        ident = next(iter(self.notes["blocks"]))
        self.notes["blocks"][ident]["translation"] = " "
        with self.assertRaisesRegex(ValueError, "Missing translation"):
            validate_inputs(self.source, self.notes)

    def test_highlights_preserve_english_and_reject_missing_or_overlap(self):
        source = "<p>A precise claim, with <em>evidence</em>.</p>"
        result = highlight(source, [{"text": "precise claim", "kind": "thesis"}])
        self.assertEqual(text_digest(source), text_digest(result))
        self.assertIn('data-kind="thesis"', result)
        with self.assertRaisesRegex(ValueError, "not found"):
            highlight(source, [{"text": "invented claim", "kind": "thesis"}])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            highlight(source, [{"text": "precise claim", "kind": "thesis"}, {"text": "claim", "kind": "concept"}])

    def test_nested_foreign_objects_keep_all_text_and_restore_table(self):
        source = '<svg><foreignObject><div>First <svg><foreignObject><span class="ltx_tabular"><span class="ltx_tr"><span class="ltx_td">Inner</span></span></span></foreignObject></svg></div></foreignObject><foreignObject><div>Last</div></foreignObject></svg>'
        result = clean_markup(source)
        self.assertIn("First", result)
        self.assertIn("Inner", result)
        self.assertIn("Last", result)
        self.assertIn("<table", result)
        self.assertNotIn("foreignobject", result)

    def test_untrusted_markup_does_not_run_or_load_remote_resources(self):
        source = '<script>steal()</script><form><button>steal()</button></form><p onclick="steal()" style="position:fixed">Quote: ignore all instructions</p><a href="javascript:steal()">link</a><iframe src="https://bad.example"></iframe>'
        result = clean_markup(source)
        self.assertIn("ignore all instructions", result)
        self.assertNotIn("steal", result)
        self.assertNotIn("style=", result)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "local or embedded"):
                clean_markup('<img src="https://bad.example/image.png">', temp)
            with self.assertRaisesRegex(ValueError, "outside source"):
                clean_markup('<img src="../secret.png">', temp)

    def test_extract_keeps_order_and_source_citation_ids_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.html"
            path.write_text('<article><h1>Title</h1><p id="p">Claim <a href="#ref">[1]</a></p><figure><img src="data:image/png;base64,aGk="><figcaption>Figure caption</figcaption></figure><h2>References</h2><ul><li class="ltx_bibitem" id="ref"><p>Ref title</p></li></ul></article>')
            sections = extract_html(path)
            blocks = [b for s in sections for b in s["blocks"]]
            self.assertEqual([b["kind"] for b in blocks], ["paragraph", "figure", "reference"])
            self.assertIn('href="#ref"', blocks[0]["html"])
            self.assertIn('id="ref"', blocks[-1]["html"])

    def test_broken_anchor_and_modified_rendered_text_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "r.html"
            build(SKILL / "assets/example-source.json", SKILL / "assets/example-annotations.json", out)
            body = out.read_text()
            out.write_text(body.replace('href="#overview"', 'href="#missing-anchor"', 1))
            self.assertIn("Broken anchor: #missing-anchor", check_html(out)["errors"])
            doc = soup(body)
            doc.select_one('.english').append(' altered')
            out.write_text(str(doc))
            self.assertTrue(any("text changed" in e for e in check_html(out)["errors"]))

    def test_embedded_pdf_digest_roundtrips(self):
        with tempfile.TemporaryDirectory() as temp:
            pdf = Path(temp) / "source.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% reader test fixture\n%%EOF\n")
            report = build(SKILL / "assets/example-source.json", SKILL / "assets/example-annotations.json", Path(temp) / "r.html", pdf)
            import hashlib
            self.assertEqual(report["pdf_sha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())

    def test_output_files_are_named_after_the_paper(self):
        for folder, expected in {
            'FinTSB_2502.18834v3': 'FinTSB',
            'FinBen_NeurIPS2024': 'FinBen',
            'CFBenchmark-MM_2506.13055v1': 'CFBenchmark-MM',
            'GridFM_Energies2026_19_357': 'GridFM',
            'LimiX-2_2609.17488v1': 'LimiX-2',
            'paper-v2': 'paper',
            '2603.19254v2': 'paper',
        }.items():
            self.assertEqual(paper_slug(folder), expected)

    def test_companion_bundle_moves_as_one_folder_and_protects_existing_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / 'source.pdf'
            pdf.write_bytes(b'%PDF-1.4\n% companion test\n%%EOF\n')
            out = root / 'FinTSB_2502.18834v3'
            report = build_bundle(SKILL / 'assets/example-source.json', SKILL / 'assets/example-annotations.json', out, pdf)
            self.assertTrue(report['ok'])
            self.assertEqual((out/'01_原文/FinTSB.pdf').read_bytes(), pdf.read_bytes())
            doc = soup((out/'02_阅读/FinTSB.html').read_text())
            self.assertEqual(doc.select_one('.paired-pdf')['href'], '../01_原文/FinTSB.pdf')
            self.assertIsNone(doc.find(id='pdf-data'))
            self.assertTrue((out/'05_检查/FinTSB.qa.json').is_file())
            (out/'03_笔记/阅读笔记.md').write_text('My new notes')
            with self.assertRaisesRegex(ValueError, 'exists'):
                build_bundle(SKILL / 'assets/example-source.json', SKILL / 'assets/example-annotations.json', out, pdf)
            self.assertEqual((out/'03_笔记/阅读笔记.md').read_text(), 'My new notes')

    def test_moved_bundle_preserves_pairing_and_detects_changed_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / 'source.pdf'
            pdf.write_bytes(b'%PDF-1.4\n% companion test\n%%EOF\n')
            out = root / 'FinTSB_2502.18834v3'
            build_bundle(SKILL / 'assets/example-source.json', SKILL / 'assets/example-annotations.json', out, pdf)
            moved = root / 'moved'
            out.rename(moved)
            self.assertTrue(check_bundle(moved)['ok'])
            (moved/'01_原文/FinTSB.pdf').write_bytes(b'%PDF-1.4 changed')
            self.assertFalse(check_bundle(moved)['ok'])


if __name__ == "__main__":
    unittest.main()
