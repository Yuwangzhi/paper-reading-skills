#!/usr/bin/env python3
"""Store guided-reading records, schedule reviews and export notes (stdlib only)."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import sys


STATIONS = ("定位", "骨架", "一小块", "小题", "换讲法", "复习", "一页笔记")
SESSION_NAME = "精读会话.json"
NOTE_NAME = "精读笔记.md"
RESULTS = ("correct", "wrong", "shaky")
CONFIDENCES = ("high", "medium", "low")
CHAIN = (("question", "问题"), ("gap", "缺口"), ("design", "设计"),
         ("evidence", "证据"), ("boundary", "边界"))
EMPTY = "暂未记录"


def now():
    return datetime.now().isoformat(timespec="seconds")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_text(value, label, empty=True):
    require(isinstance(value, str) and (empty or bool(value.strip())),
            f"{label} must be {'a string' if empty else 'non-empty text'}")


def require_int(value, label, minimum=0, maximum=None):
    require(type(value) is int and value >= minimum
            and (maximum is None or value <= maximum),
            f"{label} must be an integer >= {minimum}"
            + (f" and <= {maximum}" if maximum is not None else ""))


def validate_session(state):
    """Check fields used by commands before mutating a user-editable JSON file."""
    require(isinstance(state, dict), "Session must be a JSON object")
    require(type(state.get("version")) is int and state["version"] == 1,
            "Unsupported or missing session version (expected 1)")
    for key in ("paper", "notes"):
        require(isinstance(state.get(key), dict), f"Session {key} must be an object")
    for key in ("title", "slug", "path", "version"):
        require_text(state["paper"].get(key), f"paper.{key}")
    for key in ("goal", "created_at", "updated_at"):
        require_text(state.get(key), key)
    require_int(state.get("current_station"), "current_station", maximum=6)
    for key in ("stations", "glossary", "review_queue", "question_log"):
        require(isinstance(state.get(key), list), f"Session {key} must be an array")
        require(all(isinstance(row, dict) for row in state[key]),
                f"Session {key} entries must be objects")
    require(len(state["stations"]) == 7, "Session needs 7 stations (0-6)")
    for index, station in enumerate(state["stations"]):
        require_int(station.get("id"), "station.id", maximum=6)
        require(station["id"] == index, "Station IDs must be ordered 0-6")
        for key in ("name", "status", "memory_point"):
            require_text(station.get(key), f"station.{key}")

    for key, prefix in (("glossary", "G"), ("question_log", "Q"), ("review_queue", "R")):
        ids = [row.get("id") for row in state[key]]
        require(all(isinstance(ident, str) and re.fullmatch(prefix + r"-\d{3,}", ident)
                    for ident in ids), f"Invalid {prefix} ID in {key}")
        require(len(ids) == len(set(ids)), f"Duplicate IDs in {key}")
    for card in state["glossary"]:
        for key in ("term", "definition", "plain", "example", "review_question"):
            require_text(card.get(key), f"glossary.{key}", empty=key not in
                         ("term", "definition", "plain"))

    questions = {row["id"]: row for row in state["question_log"]}
    for question in questions.values():
        require_int(question.get("station"), "question.station", maximum=6)
        for key in ("concept", "question", "note"):
            require_text(question.get(key), f"question.{key}", empty=key == "note")
        require(isinstance(question.get("options"), list) and len(question["options"]) == 3,
                "A question needs exactly 3 options")
        for option in question["options"]:
            require_text(option, "question option", empty=False)
        require_int(question.get("answer"), "question.answer", 1, 3)
        choice = question.get("user_answer")
        if choice is not None:
            require_int(choice, "question.user_answer", 1, 3)
        require(question.get("correct") is None or type(question["correct"]) is bool,
                "question.correct must be true, false or null")
        require(question.get("confidence") in ("", *CONFIDENCES),
                "Invalid question confidence")
        require(isinstance(question.get("attempts", []), list), "question.attempts must be an array")
        for attempt in question.get("attempts", []):
            require(isinstance(attempt, dict) and attempt.get("result") in RESULTS,
                    "Invalid attempt result")
            require(attempt.get("confidence") in CONFIDENCES, "Invalid attempt confidence")
            for key in ("note", "recorded_at"):
                require_text(attempt.get(key), f"attempt.{key}")
            if attempt.get("station") is not None:
                require_int(attempt["station"], "attempt.station", maximum=6)

    referenced = []
    for item in state["review_queue"]:
        qid = item.get("question_id")
        require(isinstance(qid, str) and qid in questions, "Review refers to an unknown question ID")
        referenced.append(qid)
        for key in ("concept", "question", "options", "answer"):
            require(item.get(key) == questions[qid][key], f"Review {key} differs from question {qid}")
        require(item.get("last_result") in ("", *RESULTS), "Invalid review result")
        require(item.get("confidence") in ("", *CONFIDENCES), "Invalid review confidence")
        require(not item["last_result"] or item["confidence"] in CONFIDENCES,
                "An answered review needs confidence")
        require_int(item.get("next_station"), "review.next_station")
        require_int(item.get("priority"), "review.priority", -1, 2)
        require_text(item.get("notes"), "review.notes")
        require_text(item.get("next_date"), "review.next_date")
        if item["next_date"]:
            date.fromisoformat(item["next_date"])
    require(len(referenced) == len(set(referenced)) and set(referenced) == set(questions),
            "Each question needs exactly one review entry")

    require_text(state["notes"].get("claim"), "notes.claim")
    for key in ("evidence", "caveats", "borrow", "open_questions"):
        require(isinstance(state["notes"].get(key), list), f"notes.{key} must be an array")
        for value in state["notes"][key]:
            require_text(value, f"notes.{key} entry")
    chain = state["notes"].get("argument_chain", {})
    require(isinstance(chain, dict), "notes.argument_chain must be an object")
    for key, _ in CHAIN:
        require_text(chain.get(key, ""), f"notes.argument_chain.{key}")
    return state


def load_session(path):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Cannot read session JSON {path}: {exc}") from exc
    return validate_session(state)


def json_text(state):
    return json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def save_session(path, state):
    validate_session(state)
    state["updated_at"] = now()
    Path(path).write_text(json_text(state), encoding="utf-8")


def next_id(rows, prefix):
    number = max((int(row["id"].split("-")[1]) for row in rows), default=0) + 1
    return f"{prefix}-{number:03d}"


def schedule(result, confidence):
    """Return station offset, priority and post-session day offset."""
    if result == "wrong":
        return 1, 2, 1
    if result == "shaky":
        return 1, 1, 1
    return {"low": (2, 0, 3), "medium": (3, 0, 3), "high": (5, -1, 7)}[confidence]


def history(question, item):
    if question.get("attempts"):
        return list(question["attempts"])
    # Older version-1 states may have only the latest result. Do not invent its time/station.
    if item["last_result"]:
        return [{"result": item["last_result"], "confidence": item["confidence"],
                 "note": question["note"], "station": None, "recorded_at": ""}]
    return []


def inline(value):
    return (value.strip() or EMPTY).replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def render_note(state):
    """Render only supplied notes and actual answers, never paper claims inferred by code."""
    notes = state["notes"]
    chain = notes.get("argument_chain", {})
    lines = [f"# {inline(state['paper']['title'] or state['paper']['slug'])} · 精读笔记", "",
             "## 一句话主张", notes["claim"].strip() or EMPTY, "", "## 论证链",
             "问题 → 缺口 → 设计 → 证据 → 边界"]
    if any(chain.get(key, "").strip() for key, _ in CHAIN):
        lines += [f"- {label}：{inline(chain.get(key, ''))}" for key, label in CHAIN]
    else:
        lines.append(EMPTY)
    # Independent column summaries, not an invented row-wise evidence correspondence.
    cells = [inline(notes["claim"])] + ["<br>".join(inline(x) for x in notes[key]) or EMPTY
                                      for key in ("evidence", "caveats", "borrow")]
    lines += ["", "## 主张-证据-最容易被质疑的地方-可借鉴",
              "| 主张 | 证据 | 最容易被质疑的地方 | 可借鉴 |", "|---|---|---|---|",
              "| " + " | ".join(cells) + " |", "",
              "各列独立汇总，不表示条目之间已核实配对。", "", "## 术语卡"]
    for card in state["glossary"]:
        lines += [f"**{inline(card['term'])}**",
                  f"- 论文里的定义：{inline(card['definition'])}",
                  f"- 一句人话：{inline(card['plain'])}",
                  f"- 例子：{inline(card['example'])}",
                  f"- 复习问法：{inline(card['review_question'])}", ""]
    if not state["glossary"]:
        lines.append(EMPTY)
    lines += ["", "## 错题本"]
    items = {item["question_id"]: item for item in state["review_queue"]}
    mistakes = []
    for question in state["question_log"]:
        for attempt in history(question, items[question["id"]]):
            if attempt["result"] in ("wrong", "shaky"):
                result = "答错" if attempt["result"] == "wrong" else "不稳"
                mistakes.append(f"- {question['id']}（{result}）：{inline(question['question'])}；"
                                f"实际选择 / 原因：{inline(attempt['note'])}")
    lines += mistakes or [EMPTY]
    lines += ["", "## 待问清单"]
    lines += [f"- {inline(text)}" for text in notes["open_questions"]] or [EMPTY]
    lines += ["", "## 下次复习"]
    for item in sorted(state["review_queue"], key=lambda row: (-row["priority"], row["next_station"], row["id"])):
        when = item["next_date"] or ("未答，尚未排跨日日期" if not item["last_result"] else EMPTY)
        lines.append(f"- {item['question_id']} · {inline(item['concept'])}：站号 {item['next_station']}；{when}")
    if not state["review_queue"]:
        lines.append(EMPTY)
    return "\n".join(lines).rstrip() + "\n"


def init_session(args):
    paper = Path(args.paper).resolve()
    require(paper.is_file(), f"Paper file not found: {paper}")
    require(paper.suffix.lower() in (".pdf", ".html", ".htm"), "Paper must be a PDF or HTML file")
    require_text(args.slug, "slug", empty=False)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session, note = output / SESSION_NAME, output / NOTE_NAME
    for path in (session, note):
        require(not path.exists() and not path.is_symlink(), f"Already exists; refusing to overwrite: {path}")
    timestamp = now()
    state = {"version": 1,
             "paper": {"title": args.title or args.slug, "slug": args.slug, "path": str(paper), "version": ""},
             "goal": args.goal, "created_at": timestamp, "updated_at": timestamp, "current_station": 0,
             "stations": [{"id": index, "name": name, "status": "pending", "memory_point": ""}
                          for index, name in enumerate(STATIONS)],
             "glossary": [], "review_queue": [], "question_log": [],
             "notes": {"claim": "", "evidence": [], "caveats": [], "borrow": [], "open_questions": [],
                       "argument_chain": {key: "" for key, _ in CHAIN}}}
    validate_session(state)
    created = []
    try:
        for path, text in ((session, json_text(state)), (note, render_note(state))):
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(text)
    except OSError:
        for path in created:
            path.unlink()
        raise
    print(session)
    print(note)


def same_path(left, right):
    return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))


def export_session(args, state):
    session = Path(args.session).resolve()
    target = Path(args.output) if args.output else session.parent / NOTE_NAME
    paper = Path(state["paper"]["path"])
    if not paper.is_absolute():
        paper = session.parent / paper
    protected = (session, paper, session.parent.parent / "03_笔记" / "阅读笔记.md")
    # Check protected files before suffix validation, including aliases/hard links.
    require(not any(same_path(target, path) for path in protected),
            "Export target cannot be the session, source paper or 03_笔记/阅读笔记.md")
    require(target.suffix.lower() == ".md", "Export target must have a .md extension")
    require(not target.is_dir(), f"Export target is a directory: {target}")
    changed = False
    for item in state["review_queue"]:
        if item["last_result"] and not item["next_date"]:
            days = schedule(item["last_result"], item["confidence"])[2]
            item["next_date"] = (date.today() + timedelta(days=days)).isoformat()
            changed = True
    target.parent.mkdir(parents=True, exist_ok=True)
    requested = target
    counter = 0
    while True:
        try:
            # Exclusive creation prevents accidental overwrite even if another file appears meanwhile.
            with target.open("w" if args.force else "x", encoding="utf-8") as stream:
                stream.write(render_note(state))
            break
        except FileExistsError:
            counter += 1
            suffix = "" if counter == 1 else f"_{counter}"
            target = requested.parent / f"精读笔记_自动生成{suffix}.md"
            require(not any(same_path(target, path) for path in protected), "Fallback target is a protected file")
    if changed:
        save_session(session, state)
    if target != requested:
        print(f"Existing note preserved: {requested}", file=sys.stderr)
    print(target.resolve())


def run_command(args):
    if args.command == "init":
        init_session(args)
        return
    state = load_session(args.session)
    if args.command == "status":
        print(json_text(state), end="")
        return
    if args.command == "review":
        require_int(args.station, "station")
        due = [item for item in state["review_queue"] if item["next_station"] <= args.station]
        due.sort(key=lambda row: (-row["priority"], row["next_station"], row["id"]))
        print(json_text(due), end="")
        return
    if args.command == "export":
        export_session(args, state)
        return
    if args.command == "add-glossary":
        for key in ("term", "definition", "plain"):
            require_text(getattr(args, key), key, empty=False)
        ident = next_id(state["glossary"], "G")
        state["glossary"].append({"id": ident, "term": args.term, "definition": args.definition,
                                  "plain": args.plain, "example": args.example,
                                  "review_question": args.review_question})
    elif args.command == "add-question":
        require_int(args.station, "station", maximum=6)
        options = [text.strip() for text in args.options.split("|")]
        require(len(options) == 3 and all(options), "--options needs exactly 3 non-empty choices separated by |")
        for key in ("concept", "question"):
            require_text(getattr(args, key), key, empty=False)
        ident = next_id(state["question_log"], "Q")
        content = {"concept": args.concept, "question": args.question, "options": options, "answer": args.answer}
        state["question_log"].append({"id": ident, "station": args.station, **content,
                                     "user_answer": None, "correct": None, "confidence": "", "note": "", "attempts": []})
        state["review_queue"].append({"id": next_id(state["review_queue"], "R"), "question_id": ident,
                                     **content, "last_result": "", "confidence": "", "next_station": args.station,
                                     "next_date": "", "priority": 0, "notes": ""})
        state["current_station"] = args.station
        state["stations"][args.station]["status"] = "in_progress"
    elif args.command == "record":
        question = next((row for row in state["question_log"] if row["id"] == args.id), None)
        require(question is not None, f"Question ID not found: {args.id}")
        item = next(row for row in state["review_queue"] if row["question_id"] == args.id)
        attempts = history(question, item)
        attempts.append({"result": args.result, "confidence": args.confidence, "note": args.note,
                         "station": state["current_station"], "recorded_at": now()})
        question.update(correct={"correct": True, "wrong": False, "shaky": None}[args.result],
                        user_answer=None, confidence=args.confidence, note=args.note, attempts=attempts)
        offset, priority, _ = schedule(args.result, args.confidence)
        item.update(last_result=args.result, confidence=args.confidence, notes=args.note,
                    next_station=state["current_station"] + offset, priority=priority, next_date="")
        ident = question["id"]
    save_session(args.session, state)
    print(ident)


def build_parser():
    parser = argparse.ArgumentParser(description="Manage paper-tutor sessions; no model, network or PDF generation.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new session and empty one-page note")
    init.add_argument("--output-dir", required=True)
    init.add_argument("--paper", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--title", default="")
    init.add_argument("--goal", default="")
    for name, help_text in (("status", "Print the full session JSON"),
                            ("add-glossary", "Add a glossary card"),
                            ("add-question", "Add a three-option question and review entry"),
                            ("record", "Record an actual result and schedule review"),
                            ("review", "Print station-due entries, highest priority first"),
                            ("export", "Export Markdown, preserving existing notes by default")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--session", required=True)
        if name == "add-glossary":
            for field in ("term", "definition", "plain"):
                command.add_argument("--" + field, required=True)
            command.add_argument("--example", default="")
            command.add_argument("--review-question", default="")
        elif name == "add-question":
            command.add_argument("--station", type=int, required=True)
            for field in ("concept", "question", "options"):
                command.add_argument("--" + field, required=True)
            command.add_argument("--answer", type=int, choices=(1, 2, 3), required=True)
        elif name == "record":
            command.add_argument("--id", required=True)
            command.add_argument("--result", choices=RESULTS, required=True)
            command.add_argument("--confidence", choices=CONFIDENCES, required=True)
            command.add_argument("--note", default="")
        elif name == "review":
            command.add_argument("--station", type=int, required=True)
        elif name == "export":
            command.add_argument("--output")
            command.add_argument("--force", action="store_true")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_command(args)
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
