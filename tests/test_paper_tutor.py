"""Deterministic state/CLI checks, not model teaching or learning-quality evaluation."""
import ast
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/paper-tutor/scripts/reading_session.py"
spec = importlib.util.spec_from_file_location("tutor_reading_session", SCRIPT)
tutor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tutor)
HEADINGS = ("一句话主张", "论证链", "主张-证据-最容易被质疑的地方-可借鉴",
            "术语卡", "错题本", "待问清单", "下次复习")


class PaperTutor(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="paper-tutor-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paper = self.root / "演示.html"
        self.paper.write_text("<h1>Self-written teaching fixture</h1>", encoding="utf-8")
        self.output = self.root / "06_精读"
        self.session = self.output / tutor.SESSION_NAME
        self.note = self.output / tutor.NOTE_NAME

    def cli(self, *args, ok=True):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                code = tutor.main([str(arg) for arg in args])
            except SystemExit as exc:  # argparse rejects invalid CLI values with code 2.
                code = exc.code
        if ok:
            self.assertEqual(code, 0, error.getvalue())
        else:
            self.assertNotEqual(code, 0)
            self.assertTrue(error.getvalue().strip())
            self.assertNotIn("Traceback", error.getvalue())
        return output.getvalue(), error.getvalue()

    def init(self):
        return self.cli("init", "--output-dir", self.output, "--paper", self.paper,
                        "--slug", "Demo", "--title", "演示论文", "--goal", "能讲清楚")

    def state(self):
        return json.loads(self.session.read_text(encoding="utf-8"))

    def write_state(self, state):
        self.session.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def question(self, station=0):
        output, _ = self.cli("add-question", "--session", self.session, "--station", station,
                             "--concept", "对照实验", "--question", "[§3 表1，判断] 证据支持哪一步？",
                             "--options", " A | B | C ", "--answer", 2)
        return output.strip()

    def record(self, qid, result="wrong", confidence="low", note="", choice=None):
        extra = () if choice is None else ("--choice", choice)
        return self.cli("record", "--session", self.session, "--id", qid,
                        "--result", result, "--confidence", confidence, "--note", note, *extra)

    def resume(self, *source):
        output, _ = self.cli("resume", *(source or ("--session", self.session)))
        return json.loads(output)

    def test_init_creates_seven_stations_and_empty_note_without_inventing_answers(self):
        self.init()
        state = self.state()
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["paper"]["version"], "")
        self.assertEqual(state["paper"]["path"], str(self.paper.resolve()))
        self.assertEqual(state["goal"], "能讲清楚")
        self.assertEqual([s["id"] for s in state["stations"]], list(range(7)))
        self.assertTrue(all(s["status"] == "pending" for s in state["stations"]))
        for field in ("glossary", "question_log", "review_queue"):
            self.assertEqual(state[field], [])
        note = self.note.read_text(encoding="utf-8")
        for heading in HEADINGS:
            self.assertIn("## " + heading, note)
        self.assertIn("暂未记录", note)
        before = self.session.read_bytes()
        self.cli("init", "--output-dir", self.output, "--paper", self.paper, "--slug", "Demo", ok=False)
        self.assertEqual(self.session.read_bytes(), before)

    def test_init_rejects_missing_source_and_existing_note_without_partial_state(self):
        self.cli("init", "--output-dir", self.output, "--paper", self.root / "missing.pdf",
                 "--slug", "Demo", ok=False)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        self.note.write_text("自己的笔记", encoding="utf-8")
        self.cli("init", "--output-dir", self.output, "--paper", self.paper, "--slug", "Demo", ok=False)
        self.assertFalse(self.session.exists())
        self.assertEqual(self.note.read_text(encoding="utf-8"), "自己的笔记")

    def test_add_glossary_preserves_definition_plain_example_and_review_question(self):
        self.init()
        for expected in ("G-001", "G-002"):
            output, _ = self.cli("add-glossary", "--session", self.session, "--term", "SC",
                                 "--definition", "论文定义", "--plain", "一句人话",
                                 "--example", "一个例子", "--review-question", "有什么不同？")
            self.assertEqual(output.strip(), expected)
        self.assertEqual(self.state()["glossary"][0], {
            "id": "G-001", "term": "SC", "definition": "论文定义", "plain": "一句人话",
            "example": "一个例子", "review_question": "有什么不同？"})

    def test_question_has_three_options_linked_review_and_unknown_user_answer(self):
        self.init()
        qid = self.question(2)
        state = self.state()
        self.assertEqual(qid, "Q-001")
        question, review = state["question_log"][0], state["review_queue"][0]
        self.assertEqual(question["options"], ["A", "B", "C"])
        self.assertIsNone(question["correct"])
        self.assertIsNone(question["user_answer"])
        self.assertEqual(review["id"], "R-001")
        self.assertEqual(review["question_id"], qid)
        self.assertEqual(review["last_result"], "")
        self.assertEqual(state["current_station"], 2)
        self.assertEqual(state["stations"][2]["status"], "in_progress")
        self.assertEqual(state["stations"][0]["status"], "pending")

    def test_wrong_schedules_from_actual_current_station_not_original_question_station(self):
        self.init()
        qid = self.question(0)
        state = self.state()
        state["current_station"] = 4
        self.write_state(state)
        self.record(qid, note="选了 1；把相关当成因果")
        state = self.state()
        self.assertEqual(state["review_queue"][0]["next_station"], 5)
        self.assertEqual(state["review_queue"][0]["priority"], 2)
        self.assertIs(state["question_log"][0]["correct"], False)
        self.assertIsNone(state["question_log"][0]["user_answer"])
        self.assertEqual(state["question_log"][0]["attempts"][0]["station"], 4)

    def test_correct_high_schedules_five_stations_later(self):
        self.init()
        qid = self.question(2)
        self.record(qid, "correct", "high")
        state = self.state()
        self.assertEqual(state["review_queue"][0]["next_station"], 7)
        self.assertEqual(state["review_queue"][0]["priority"], -1)
        self.assertIs(state["question_log"][0]["correct"], True)

    def test_all_schedule_combinations_and_shaky_is_not_wrong(self):
        self.init()
        for result, confidence, offset, priority in (("wrong", "high", 1, 2),
                                                    ("shaky", "medium", 1, 1),
                                                    ("correct", "low", 2, 0),
                                                    ("correct", "medium", 3, 0),
                                                    ("correct", "high", 5, -1)):
            with self.subTest(result=result, confidence=confidence):
                qid = self.question(2)
                self.record(qid, result, confidence)
                state = self.state()
                self.assertEqual(state["review_queue"][-1]["next_station"], 2 + offset)
                self.assertEqual(state["review_queue"][-1]["priority"], priority)
                if result == "shaky":
                    self.assertIsNone(state["question_log"][-1]["correct"])

    def test_review_due_filtering_priority_and_status_are_read_only(self):
        self.init()
        expected = []
        for result, confidence in (("shaky", "low"), ("correct", "high"), ("wrong", "low")):
            qid = self.question(2)
            self.record(qid, result, confidence)
            expected.append(qid)
        before = self.session.read_bytes()
        due, _ = self.cli("review", "--session", self.session, "--station", 2)
        self.assertEqual(json.loads(due), [])
        due, _ = self.cli("review", "--session", self.session, "--station", 3)
        self.assertEqual([row["question_id"] for row in json.loads(due)], [expected[2], expected[0]])
        all_due, _ = self.cli("review", "--session", self.session, "--station", 99)
        self.assertEqual(len(json.loads(all_due)), 3)
        status, _ = self.cli("status", "--session", self.session)
        self.assertEqual(json.loads(status), self.state())
        self.assertEqual(self.session.read_bytes(), before)

    def test_export_seven_headings_real_notes_and_retains_wrong_before_correct(self):
        self.init()
        self.cli("add-glossary", "--session", self.session, "--term", "SC",
                 "--definition", "定义", "--plain", "人话", "--example", "例子")
        qid = self.question()
        self.record(qid, note="实际选了 1；混淆比较口径")
        self.record(qid, "correct", "high")
        state = self.state()
        state["notes"].update(claim="用户自己的主张", evidence=["表1 | 同口径"], caveats=["样本边界"],
                               borrow=["借用对照"], open_questions=["其他数据集呢？"],
                               argument_chain={"question": "目标问题", "gap": "缺口", "design": "设计",
                                               "evidence": "证据", "boundary": "边界"})
        self.write_state(state)
        path, _ = self.cli("export", "--session", self.session, "--force")
        self.assertEqual(Path(path.strip()), self.note)
        text = self.note.read_text(encoding="utf-8")
        self.assertEqual([line for line in text.splitlines() if line.startswith("## ")],
                         ["## " + heading for heading in HEADINGS])
        for value in ("用户自己的主张", "表1 \\| 同口径", "目标问题", "SC", "论文里的定义：定义",
                      "一句人话：人话", "实际选了 1；混淆比较口径", "其他数据集呢？"):
            self.assertIn(value, text)
        self.assertEqual(len(self.state()["question_log"][0]["attempts"]), 2)

    def test_export_no_overwrite_numbered_fallback_custom_target_and_force(self):
        self.init()
        self.note.write_text("自己改过的笔记", encoding="utf-8")
        before = self.note.read_bytes()
        first, error = self.cli("export", "--session", self.session)
        second, _ = self.cli("export", "--session", self.session)
        self.assertEqual(Path(first.strip()).name, "精读笔记_自动生成.md")
        self.assertIn("preserved", error)
        self.assertEqual(Path(second.strip()).name, "精读笔记_自动生成_2.md")
        self.assertEqual(self.note.read_bytes(), before)
        custom = self.output / "custom.md"
        custom.write_text("用户笔记", encoding="utf-8")
        third, _ = self.cli("export", "--session", self.session, "--output", custom)
        self.assertEqual(Path(third.strip()).name, "精读笔记_自动生成_3.md")
        self.assertEqual(custom.read_text(encoding="utf-8"), "用户笔记")
        self.cli("export", "--session", self.session, "--output", custom, "--force")
        self.assertIn("## 一句话主张", custom.read_text(encoding="utf-8"))

    def test_export_assigns_one_three_seven_days_only_to_answered_items(self):
        self.init()
        for result, confidence in (("wrong", "high"), ("shaky", "low"), ("correct", "low"),
                                   ("correct", "medium"), ("correct", "high")):
            qid = self.question()
            self.record(qid, result, confidence)
        self.question()
        self.cli("export", "--session", self.session)
        items = self.state()["review_queue"]
        for row, days in zip(items, (1, 1, 3, 3, 7)):
            self.assertEqual(row["next_date"], (date.today() + timedelta(days=days)).isoformat())
        self.assertEqual(items[-1]["next_date"], "")

    def test_repeat_export_keeps_old_date_and_new_record_clears_it(self):
        self.init()
        qid = self.question()
        self.record(qid)
        state = self.state()
        state["review_queue"][0]["next_date"] = "2000-01-01"
        self.write_state(state)
        self.cli("export", "--session", self.session)
        self.cli("export", "--session", self.session)
        self.assertEqual(self.state()["review_queue"][0]["next_date"], "2000-01-01")
        self.record(qid, "correct", "high")
        self.assertEqual(self.state()["review_queue"][0]["next_date"], "")

    def test_export_cannot_overwrite_state_source_or_original_reading_note_even_with_force(self):
        self.init()
        original_note = self.root / "03_笔记/阅读笔记.md"
        original_note.parent.mkdir()
        original_note.write_text("原来的笔记", encoding="utf-8")
        for target in (self.session, self.paper, original_note):
            before = target.read_bytes()
            self.cli("export", "--session", self.session, "--output", target, "--force", ok=False)
            self.assertEqual(target.read_bytes(), before)

    def test_bad_json_schema_and_missing_file_fail_readably_without_mutation(self):
        self.cli("status", "--session", self.session, ok=False)
        self.init()
        for content in ("{broken", "[]", '{"version": 99}', json.dumps({**self.state(), "glossary": [42]})):
            self.session.write_text(content, encoding="utf-8")
            before = self.session.read_bytes()
            self.cli("status", "--session", self.session, ok=False)
            self.assertEqual(self.session.read_bytes(), before)

    def test_invalid_options_stations_and_choices_fail_before_saving(self):
        self.init()
        for station, options, answer in ((-1, "A|B|C", 1), (7, "A|B|C", 1),
                                          (0, "A|B", 1), (0, "A||C", 1), (0, "A|B|C", 4)):
            before = self.session.read_bytes()
            self.cli("add-question", "--session", self.session, "--station", station,
                     "--concept", "C", "--question", "Q", "--options", options, "--answer", answer, ok=False)
            self.assertEqual(self.session.read_bytes(), before)
        self.cli("review", "--session", self.session, "--station", -1, ok=False)

    def test_unknown_question_id_fails_without_saving(self):
        self.init()
        before = self.session.read_bytes()
        self.cli("record", "--session", self.session, "--id", "Q-999", "--result", "wrong",
                 "--confidence", "low", ok=False)
        self.assertEqual(self.session.read_bytes(), before)

    def test_optional_fields_in_old_version_one_state_can_be_read_and_history_retained(self):
        self.init()
        qid = self.question()
        self.record(qid, note="旧记录的错误原因")
        state = self.state()
        del state["notes"]["argument_chain"]
        del state["question_log"][0]["attempts"]
        self.write_state(state)
        self.cli("status", "--session", self.session)
        self.record(qid, "correct", "high")
        self.cli("export", "--session", self.session, "--force")
        self.assertIn("旧记录的错误原因", self.note.read_text(encoding="utf-8"))

    def test_failed_export_does_not_persist_dates(self):
        self.init()
        self.record(self.question())
        blocker = self.root / "file-not-directory"
        blocker.write_text("keep", encoding="utf-8")
        before = self.session.read_bytes()
        self.cli("export", "--session", self.session, "--output", blocker / "out.md", ok=False)
        self.assertEqual(self.session.read_bytes(), before)
        self.assertEqual(blocker.read_text(encoding="utf-8"), "keep")

    def test_each_turn_is_recorded_and_the_note_follows_without_export(self):
        self.init()
        self.cli("profile", "--session", self.session, "--familiarity", "读过但忘了", "--paper-version", "v2")
        self.cli("station", "--session", self.session, "--id", 0, "--status", "done",
                 "--memory-point", "先读 §3 和表 4")
        output, _ = self.cli("cover", "--session", self.session, "--station", 2, "--location", "§3.3 表 2",
                             "--point", "SC 要先找错再改错", "--user-words", "像校对")
        self.assertEqual(output.strip(), "C-001")
        for _ in range(2):  # repeating a command must not duplicate the entry
            self.cli("note", "--session", self.session, "--claim", "我的说法", "--chain-gap", "缺口",
                     "--add-evidence", "表 4", "--add-open-question", "换数据集呢？")
        state = self.state()
        self.assertEqual((state["familiarity"], state["paper"]["version"]), ("读过但忘了", "v2"))
        self.assertEqual(state["stations"][0]["status"], "done")
        self.assertEqual((state["current_station"], state["stations"][2]["status"]), (2, "in_progress"))
        self.assertEqual(state["covered"][0]["point"], "SC 要先找错再改错")
        self.assertEqual((state["notes"]["evidence"], state["notes"]["open_questions"]), (["表 4"], ["换数据集呢？"]))
        self.assertEqual(state["notes"]["argument_chain"]["gap"], "缺口")
        text = self.note.read_text(encoding="utf-8")
        for value in ("读到「一小块」", "已讲 1 小块", "读过但忘了", "§3.3 表 2", "像校对",
                      "先读 §3 和表 4", "我的说法", "换数据集呢？"):
            self.assertIn(value, text)
        self.assertEqual([line for line in text.splitlines() if line.startswith("## ")],
                         ["## " + heading for heading in HEADINGS])

    def test_record_choice_is_kept_and_a_contradiction_is_rejected(self):
        self.init()
        qid = self.question(2)
        before = self.session.read_bytes()
        for choice, result in ((2, "wrong"), (1, "correct")):  # the stored key is 2
            self.cli("record", "--session", self.session, "--id", qid, "--result", result,
                     "--confidence", "low", "--choice", choice, ok=False)
            self.assertEqual(self.session.read_bytes(), before)
        self.record(qid, "wrong", "low", "把误报当成漏报", choice=1)
        self.record(qid, "correct", "high", choice=2)
        question = self.state()["question_log"][0]
        self.assertEqual(question["user_answer"], 2)
        self.assertEqual([attempt["choice"] for attempt in question["attempts"]], [1, 2])

    def test_resume_is_read_only_and_says_what_not_to_ask_again(self):
        self.init()
        self.cli("profile", "--session", self.session, "--familiarity", "没看过")
        self.cli("cover", "--session", self.session, "--station", 1, "--location", "摘要", "--point", "骨架")
        wrong, right, open_question = self.question(1), self.question(1), self.question(2)
        self.record(wrong, "wrong", "low", choice=1)
        self.record(right, "correct", "high", choice=2)
        before = self.session.read_bytes(), self.note.read_bytes()
        view = self.resume()
        self.assertEqual(view, self.resume("--paper", self.paper))
        self.assertEqual((self.session.read_bytes(), self.note.read_bytes()), before)
        self.assertEqual(view["missing_profile"], [])
        self.assertEqual((view["goal"], view["familiarity"], view["current_station"]), ("能讲清楚", "没看过", 2))
        self.assertEqual([row["question_id"] for row in view["unanswered"]], [open_question])
        self.assertEqual([(row["question_id"], row["reason"]) for row in view["due_reviews"]],
                         [(wrong, "wrong_or_shaky")])
        self.assertEqual(view["later_reviews"], 1)
        self.assertEqual(view["paper_file"], str(self.paper.resolve()))
        steps = "\n".join(view["next_steps"])
        for value in ("不运行 init", "不再问熟悉程度和阅读目标", open_question, wrong, "不重讲"):
            self.assertIn(value, steps)

    def test_resume_brings_back_reviews_by_date_when_the_session_was_never_exported(self):
        self.init()
        qid = self.question(2)
        self.record(qid, "correct", "high", choice=2)
        self.assertEqual(self.resume()["due_reviews"], [])
        state = self.state()
        state["question_log"][0]["attempts"][0]["recorded_at"] = (
            date.today() - timedelta(days=7)).isoformat() + "T09:00:00"
        self.write_state(state)
        row = self.resume()["due_reviews"][0]
        self.assertEqual((row["question_id"], row["reason"], row["due_date"]),
                         (qid, "date", date.today().isoformat()))

    def test_resume_finds_a_sibling_session_and_a_moved_paper_and_fails_readably_without_one(self):
        self.cli("resume", "--paper", self.paper, ok=False)
        self.cli("resume", ok=False)
        self.output = self.root / "精读"
        self.session, self.note = self.output / tutor.SESSION_NAME, self.output / tutor.NOTE_NAME
        self.init()
        self.assertEqual(self.resume("--paper", self.paper)["session"], str(self.session.resolve()))
        state = self.state()
        state["paper"]["path"] = "D:\\documents\\papers\\演示.html"  # saved on another machine
        self.write_state(state)
        view = self.resume()
        self.assertEqual(Path(view["paper_file"]), self.paper)
        self.assertEqual(view["missing_profile"], ["familiarity"])

    def test_session_saved_before_these_fields_resumes_and_its_untouched_note_is_refreshed(self):
        self.init()
        self.cli("add-glossary", "--session", self.session, "--term", "SC", "--definition", "定义", "--plain", "人话")
        qid = self.question(1)
        self.record(qid, "correct", "medium")
        state = self.state()
        for key in ("familiarity", "covered", "note_digest"):
            del state[key]
        blank = {**state, "glossary": [], "review_queue": [], "question_log": []}
        self.note.write_text(tutor.render_note(blank, progress=False), encoding="utf-8")  # the old empty template
        self.write_state(state)
        view = self.resume()
        self.assertEqual((view["covered"], view["missing_profile"]), ([], ["familiarity"]))
        self.assertIn("没有记下讲过哪些小块", "\n".join(view["next_steps"]))
        self.cli("profile", "--session", self.session, "--familiarity", "读过但忘了")
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("**SC**", text)
        self.assertIn("已答 1 / 1 题", text)

    def test_hand_edited_note_is_never_touched_and_export_writes_a_copy(self):
        self.init()
        self.note.write_text(self.note.read_text(encoding="utf-8") + "\n我自己加的一行\n", encoding="utf-8")
        before = self.note.read_bytes()
        _, error = self.cli("profile", "--session", self.session, "--familiarity", "比较熟")
        self.assertIn("left untouched", error)
        path, _ = self.cli("export", "--session", self.session)
        self.assertEqual(Path(path.strip()).name, "精读笔记_自动生成.md")
        self.assertEqual(self.note.read_bytes(), before)
        self.assertEqual(self.state()["familiarity"], "比较熟")

    def test_untouched_note_is_refreshed_in_place_by_export_even_after_a_manual_json_edit(self):
        self.init()
        state = self.state()
        state["notes"]["claim"] = "直接改 JSON 写入的主张"
        self.write_state(state)
        path, error = self.cli("export", "--session", self.session)
        self.assertEqual((Path(path.strip()), error), (self.note, ""))
        self.assertIn("直接改 JSON 写入的主张", self.note.read_text(encoding="utf-8"))
        self.assertEqual(list(self.output.iterdir()).__len__(), 2)

    def test_new_commands_reject_bad_input_without_saving(self):
        self.init()
        before = self.session.read_bytes()
        for args in (("profile",), ("note",), ("station", "--id", 0), ("station", "--id", 7, "--status", "done"),
                     ("station", "--id", 0, "--status", "finished"), ("note", "--add-evidence", " "),
                     ("cover", "--station", 9, "--location", "§1", "--point", "P"),
                     ("cover", "--station", 1, "--location", " ", "--point", "P")):
            self.cli(args[0], "--session", self.session, *args[1:], ok=False)
            self.assertEqual(self.session.read_bytes(), before)

    def test_help_and_each_subcommand_help_return_zero_headlessly(self):
        commands = ("init", "resume", "status", "profile", "station", "cover", "note",
                    "add-glossary", "add-question", "record", "review", "export")
        for command in (None, *commands):
            result = subprocess.run([sys.executable, str(SCRIPT), *([command] if command else []), "--help"],
                                    capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)
            if command is None:
                for expected in commands:
                    self.assertIn(expected, result.stdout)

    def test_script_uses_only_standard_library_and_python_39_syntax(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))
        allowed = {"__future__", "argparse", "datetime", "hashlib", "json", "pathlib", "re", "sys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split(".")[0] in allowed for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
