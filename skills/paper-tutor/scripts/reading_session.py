#!/usr/bin/env python3
"""Store guided-reading records, resume them later, schedule reviews and export notes (stdlib only)."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys


STATIONS = ("定位", "骨架", "一小块", "小题", "换讲法", "复习", "一页笔记")
SESSION_NAME = "精读会话.json"
NOTE_NAME = "精读笔记.md"
RESULTS = ("correct", "wrong", "shaky")
CONFIDENCES = ("high", "medium", "low")
STATUSES = ("pending", "in_progress", "done")
SESSION_DIRS = ("06_精读", "精读")  # looked up next to the paper, in this order
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
    # Optional additions; version-1 states written before them stay readable.
    require_text(state.get("familiarity", ""), "familiarity")
    require_text(state.get("note_digest", ""), "note_digest")
    covered = state.get("covered", [])
    require(isinstance(covered, list) and all(isinstance(row, dict) for row in covered),
            "Session covered must be an array of objects")
    ids = [row.get("id") for row in covered]
    require(all(isinstance(ident, str) and re.fullmatch(r"C-\d{3,}", ident) for ident in ids)
            and len(ids) == len(set(ids)), "Invalid or duplicate C ID in covered")
    for row in covered:
        require_int(row.get("station"), "covered.station", maximum=6)
        for key in ("location", "point", "user_words", "recorded_at"):
            require_text(row.get(key, ""), f"covered.{key}", empty=key not in ("location", "point"))

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
            if attempt.get("choice") is not None:
                require_int(attempt["choice"], "attempt.choice", 1, 3)

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


def progress_lines(state):
    """Where the reader stopped; only recorded facts, so a later sitting can pick up from here."""
    names = {station["id"]: station["name"] for station in state["stations"]}
    answered = sum(1 for item in state["review_queue"] if item["last_result"])
    covered = state.get("covered", [])
    lines = ["**读到哪了**（下次说“接着读”，从这里接上，不重问已答过的问题）",
             f"- 进度：读到「{names[state['current_station']]}」；已讲 {len(covered)} 小块；"
             f"已答 {answered} / {len(state['question_log'])} 题",
             f"- 目标：{inline(state['goal'])}；熟悉程度：{inline(state.get('familiarity', ''))}"]
    lines.append("- 已讲：" + ("" if covered else EMPTY))
    for row in covered:
        own = f"（我的说法：{inline(row['user_words'])}）" if row.get("user_words", "").strip() else ""
        lines.append(f"  - 「{names[row['station']]}」{inline(row['location'])}：{inline(row['point'])}{own}")
    points = [station for station in state["stations"] if station["memory_point"].strip()]
    lines.append("- 记忆点：" + ("" if points else EMPTY))
    lines += [f"  - {station['name']}：{inline(station['memory_point'])}" for station in points]
    return lines + [""]


def render_note(state, progress=True):
    """Render only supplied notes and actual answers, never paper claims inferred by code."""
    notes = state["notes"]
    chain = notes.get("argument_chain", {})
    lines = [f"# {inline(state['paper']['title'] or state['paper']['slug'])} · 精读笔记", ""]
    if progress:  # False reproduces notes written before the progress block existed.
        lines += progress_lines(state)
    lines += ["## 一句话主张", notes["claim"].strip() or EMPTY, "", "## 论证链",
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


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_owned(note, state):
    """True while the note is still what this script wrote; any hand edit makes it the user's."""
    try:
        if note.is_symlink() or not note.is_file():
            return False
        text = note.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if digest(text) == state.get("note_digest"):
        return True
    # Notes written before the digest existed: the empty template or a full export.
    blank = {**state, "glossary": [], "review_queue": [], "question_log": [],
             "notes": {"claim": "", "evidence": [], "caveats": [], "borrow": [], "open_questions": []}}
    return text in (render_note(state, progress=False), render_note(blank, progress=False))


def find_session(paper):
    paper = Path(paper).resolve()
    hits = [paper.parent / name / SESSION_NAME for name in SESSION_DIRS]
    hits = [path for path in hits if path.is_file()]
    require(hits, f"No saved session next to {paper.name} (looked in {' and '.join(SESSION_DIRS)}); "
                  "start one with init")
    return hits[0]


def due_date(item, question):
    """Cross-day date: the exported one, else last answer + the schedule's day offset."""
    if item["next_date"]:
        return item["next_date"]
    stamps = [attempt.get("recorded_at", "") for attempt in question.get("attempts", [])]
    if not (item["last_result"] and stamps and stamps[-1]):
        return ""
    try:
        answered = datetime.fromisoformat(stamps[-1]).date()
    except ValueError:
        return ""
    return (answered + timedelta(days=schedule(item["last_result"], item["confidence"])[2])).isoformat()


def resume_view(state, session):
    """Read-only briefing for a new sitting: what is known, what is open, what not to repeat."""
    session = Path(session).resolve()
    today = date.today().isoformat()
    questions = {row["id"]: row for row in state["question_log"]}
    unanswered, due, later = [], [], 0
    for item in state["review_queue"]:
        question = questions[item["question_id"]]
        base = {key: item[key] for key in ("question_id", "concept", "question", "options", "answer")}
        if not item["last_result"]:
            unanswered.append({**base, "station": question["station"]})
            continue
        when = due_date(item, question)
        reason = ("wrong_or_shaky" if item["priority"] > 0 else
                  "date" if when and when <= today else
                  "station" if item["next_station"] <= state["current_station"] else "")
        if reason:
            due.append({**base, "last_result": item["last_result"], "confidence": item["confidence"],
                        "priority": item["priority"], "due_date": when, "reason": reason})
        else:
            later += 1
    due.sort(key=lambda row: (-row["priority"], row["due_date"] or "9999", row["question_id"]))

    stored = state["paper"]["path"]
    found = stored if stored and Path(stored).is_file() else ""
    name = PureWindowsPath(stored).name  # also splits paths saved on Windows
    for folder in (session.parent.parent, session.parent):
        if not found and name and (folder / name).is_file():
            found = str(folder / name)
    missing = [key for key in ("goal", "familiarity") if not state.get(key, "").strip()]
    covered = state.get("covered", [])
    notes = state["notes"]
    empty_notes = [key for key in ("claim", "evidence", "caveats", "borrow", "open_questions")
                   if not notes[key]]
    if not any(notes.get("argument_chain", {}).get(key, "").strip() for key, _ in CHAIN):
        empty_notes.append("argument_chain")

    steps = ["不运行 init；先用两三句话告诉用户上次读到哪、记住了什么。"]
    steps.append(f"只补问缺的校准项：{'、'.join(missing)}；已记录的不重问。" if missing
                 else "校准已记录：不再问熟悉程度和阅读目标。")
    if unanswered:
        steps.append("先把没答完的题原样再问一次：" + "、".join(row["question_id"] for row in unanswered)
                     + "；答完用 record --choice 记录。")
    if due:
        steps.append("再回顾到期的题，一次一道：" + "、".join(row["question_id"] for row in due) + "。")
    if covered:
        steps.append("covered 里的小块已讲过，不重讲；用户说忘了才简短回顾。然后从 current_station 接着走。")
    elif state["question_log"] or state["glossary"]:
        steps.append("这份会话没有记下讲过哪些小块：照实说明，请用户用一两句话说说还记得什么，"
                     "再决定从哪接，不默认从头讲，也不补写没发生过的记录。")
    if not found:
        steps.append("记录里的论文路径在本机找不到：先请用户确认论文文件，再继续。")
    return {"session": str(session), "paper": state["paper"], "paper_file": found,
            "goal": state["goal"], "familiarity": state.get("familiarity", ""),
            "missing_profile": missing, "current_station": state["current_station"],
            "stations": state["stations"], "covered": covered, "unanswered": unanswered,
            "due_reviews": due, "later_reviews": later,
            "glossary_terms": [card["term"] for card in state["glossary"]],
            "empty_notes": empty_notes, "updated_at": state["updated_at"], "next_steps": steps}


def init_session(args):
    paper = Path(args.paper).resolve()
    require(paper.is_file(), f"Paper file not found: {paper}")
    require(paper.suffix.lower() in (".pdf", ".html", ".htm"), "Paper must be a PDF or HTML file")
    require_text(args.slug, "slug", empty=False)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session, note = output / SESSION_NAME, output / NOTE_NAME
    for path in (session, note):
        require(not path.exists() and not path.is_symlink(),
                f"Already exists; refusing to overwrite: {path} (continue it with resume)")
    timestamp = now()
    state = {"version": 1,
             "paper": {"title": args.title or args.slug, "slug": args.slug, "path": str(paper), "version": ""},
             "goal": args.goal, "familiarity": "", "created_at": timestamp, "updated_at": timestamp,
             "current_station": 0,
             "stations": [{"id": index, "name": name, "status": "pending", "memory_point": ""}
                          for index, name in enumerate(STATIONS)],
             "glossary": [], "review_queue": [], "question_log": [], "covered": [],
             "notes": {"claim": "", "evidence": [], "caveats": [], "borrow": [], "open_questions": [],
                       "argument_chain": {key: "" for key, _ in CHAIN}}}
    state["note_digest"] = digest(render_note(state))
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
    # The default note is ours to refresh until the user edits it; other targets need --force.
    default = session.parent / NOTE_NAME
    replace = args.force or (same_path(target, default) and is_owned(target, state))
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
            with target.open("w" if replace else "x", encoding="utf-8") as stream:
                stream.write(render_note(state))
            break
        except FileExistsError:
            counter += 1
            suffix = "" if counter == 1 else f"_{counter}"
            target = requested.parent / f"精读笔记_自动生成{suffix}.md"
            require(not any(same_path(target, path) for path in protected), "Fallback target is a protected file")
    if same_path(target, default):
        state["note_digest"] = digest(render_note(state))
        changed = True
    if changed:
        save_session(session, state)
    if target != requested:
        print(f"Existing note preserved: {requested}", file=sys.stderr)
    print(target.resolve())


def run_command(args):
    if args.command == "init":
        init_session(args)
        return
    if args.command == "resume":
        session = args.session or find_session(args.paper)
        print(json_text(resume_view(load_session(session), session)), end="")
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
    # Decided before the change: the note mirrors the session unless the user has edited it.
    note = Path(args.session).resolve().parent / NOTE_NAME
    ours = is_owned(note, state) or not (note.exists() or note.is_symlink())
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
        if args.choice is not None and args.result != "shaky":
            require((args.choice == question["answer"]) == (args.result == "correct"),
                    f"--choice {args.choice} contradicts --result {args.result} "
                    f"(the stored key is {question['answer']})")
        attempts = history(question, item)
        attempt = {"result": args.result, "confidence": args.confidence, "note": args.note,
                   "station": state["current_station"], "recorded_at": now()}
        if args.choice is not None:
            attempt["choice"] = args.choice
        attempts.append(attempt)
        question.update(correct={"correct": True, "wrong": False, "shaky": None}[args.result],
                        user_answer=args.choice, confidence=args.confidence, note=args.note,
                        attempts=attempts)
        offset, priority, _ = schedule(args.result, args.confidence)
        item.update(last_result=args.result, confidence=args.confidence, notes=args.note,
                    next_station=state["current_station"] + offset, priority=priority, next_date="")
        ident = question["id"]
    elif args.command == "profile":
        require(any(value is not None for value in (args.familiarity, args.goal, args.paper_version)),
                "profile needs --familiarity, --goal or --paper-version")
        for key in ("familiarity", "goal"):
            if getattr(args, key) is not None:
                state[key] = getattr(args, key)
        if args.paper_version is not None:
            state["paper"]["version"] = args.paper_version
        ident = "profile"
    elif args.command == "station":
        require_int(args.id, "id", maximum=6)
        require(args.status is not None or args.memory_point is not None,
                "station needs --status or --memory-point")
        station = state["stations"][args.id]
        if args.status is not None:
            station["status"] = args.status
        if args.memory_point is not None:
            station["memory_point"] = args.memory_point
        if args.status == "in_progress":
            state["current_station"] = args.id
        ident = f"station {args.id}"
    elif args.command == "cover":
        require_int(args.station, "station", maximum=6)
        for key in ("location", "point"):
            require_text(getattr(args, key), key, empty=False)
        rows = state.setdefault("covered", [])
        ident = next_id(rows, "C")
        rows.append({"id": ident, "station": args.station, "location": args.location,
                     "point": args.point, "user_words": args.user_words, "recorded_at": now()})
        state["current_station"] = args.station
        if state["stations"][args.station]["status"] == "pending":
            state["stations"][args.station]["status"] = "in_progress"
    elif args.command == "note":
        notes, given = state["notes"], args.claim is not None
        if args.claim is not None:
            notes["claim"] = args.claim
        chain = notes.setdefault("argument_chain", {})
        for key, _ in CHAIN:
            value = getattr(args, "chain_" + key)
            if value is not None:
                chain[key], given = value, True
        for key, flag in (("evidence", "add_evidence"), ("caveats", "add_caveat"),
                          ("borrow", "add_borrow"), ("open_questions", "add_open_question")):
            for text in getattr(args, flag) or []:
                require_text(text, "--" + flag.replace("_", "-"), empty=False)
                given = True
                if text not in notes[key]:  # repeating a command does not duplicate the entry
                    notes[key].append(text)
        require(given, "note needs at least one field to record")
        ident = "notes"
    if ours:
        state["note_digest"] = digest(render_note(state))
    save_session(args.session, state)
    if ours:
        try:
            note.write_text(render_note(state), encoding="utf-8")
        except OSError as exc:
            print(f"Session saved; note not refreshed: {exc}", file=sys.stderr)
    else:
        print(f"Hand-edited note left untouched: {note} (export writes a separate copy)", file=sys.stderr)
    print(ident)


def build_parser():
    parser = argparse.ArgumentParser(description="Save and resume paper-tutor sessions; no model, network or PDF generation.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new session and empty one-page note")
    init.add_argument("--output-dir", required=True)
    init.add_argument("--paper", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--title", default="")
    init.add_argument("--goal", default="")
    resume = commands.add_parser("resume", help="Brief a new sitting from the saved session; read-only")
    source = resume.add_mutually_exclusive_group(required=True)
    source.add_argument("--session")
    source.add_argument("--paper", help="Paper file; looks for a session folder next to it")
    for name, help_text in (("status", "Print the full session JSON"),
                            ("profile", "Record the reader's familiarity, goal or the paper version"),
                            ("station", "Mark a station's status or memory point"),
                            ("cover", "Record one explained piece so it is not explained again"),
                            ("note", "Record the reader's own claim, chain, evidence, doubts and questions"),
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
            command.add_argument("--choice", type=int, choices=(1, 2, 3))
        elif name == "profile":
            for field in ("familiarity", "goal", "paper-version"):
                command.add_argument("--" + field)
        elif name == "station":
            command.add_argument("--id", type=int, required=True)
            command.add_argument("--status", choices=STATUSES)
            command.add_argument("--memory-point")
        elif name == "cover":
            command.add_argument("--station", type=int, required=True)
            for field in ("location", "point"):
                command.add_argument("--" + field, required=True)
            command.add_argument("--user-words", default="")
        elif name == "note":
            command.add_argument("--claim")
            for key, _ in CHAIN:
                command.add_argument("--chain-" + key)
            for field in ("evidence", "caveat", "borrow", "open-question"):
                command.add_argument("--add-" + field, action="append")
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
