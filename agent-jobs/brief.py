#!/usr/bin/env python3
"""Morning brief: calendar (.ics) + Obsidian to-do file + email digest -> brief.md (+ optional ntfy).

No AI model involved: everything here is plain parsing, so the brief is the same every time
for the same files. It warns you when the calendar or to-do file looks out of date.

Usage:
  brief.py               # write brief.md (and push to ntfy if NTFY_URL is set)
  brief.py --print       # print the brief, write nothing, send nothing
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import agentdb  # the agent page; does nothing unless AGENT_DB is set
import icalendar
import recurring_ical_events

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/London"))
CAL_DIR = Path(os.path.expanduser(os.environ.get("CAL_DIR", "~/Sync/calendar")))
TODO_FILES = [Path(os.path.expanduser(p)) for p in
              os.environ.get("TODO_FILES", "~/Obsidian/Tasks/To-do.md").split(":") if p]
EMAIL_DIGEST = Path(os.path.expanduser(os.environ.get("EMAIL_DIGEST", "~/email-agent/digest.jsonl")))
EMAIL_PUSHED = EMAIL_DIGEST.with_name("pushed.jsonl")  # written by triage: urgent/work alerts already pushed
PUSHED_HOURS = int(os.environ.get("PUSHED_HOURS", "24"))
OUT_FILE = Path(os.path.expanduser(os.environ.get("BRIEF_FILE", "~/agent-data/brief.md")))
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "7"))
UNDATED_SHOWN = int(os.environ.get("UNDATED_SHOWN", "5"))
STALE_HOURS = int(os.environ.get("STALE_HOURS", "36"))
# Extra .ics files read every time (e.g. UK bank holidays); all *.ics in this folder are used.
EXTRA_ICS_DIR = Path(os.path.expanduser(os.environ.get("EXTRA_ICS_DIR", "~/agent/extra-calendars")))
# "Later" section: events after the next LOOKAHEAD_DAYS and up to LATER_DAYS ahead,
# shown only if the title contains one of these words.
LATER_DAYS = int(os.environ.get("LATER_DAYS", "45"))
LATER_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "LATER_KEYWORDS",
    "flight,appointment,doctor,dentist,barber,interview,birthday,holiday,deadline,exam,wedding",
).split(",") if k.strip()]
# From this day of the month on, preview next month's events and dated tasks.
MONTH_PREVIEW_FROM_DAY = int(os.environ.get("MONTH_PREVIEW_FROM_DAY", "20"))
# Time tracking: the newest SimpleTimeTracker CSV export in this folder (synced by Syncthing).
TIME_DIR = Path(os.path.expanduser(os.environ.get("TIME_DIR", "~/Sync/timetracker")))
# Old activity names folded into new ones, optionally adding a tag, e.g. after merging activities:
# "Calisthenics=Exercise:Calisthenics; Sport/Gym=Exercise:Gym". Separate entries with ";".
TIME_ACTIVITY_MAP = {
    old.strip(): (new.partition(":")[0].strip(), new.partition(":")[2].strip())
    for old, _, new in (e.partition("=") for e in os.environ.get("TIME_ACTIVITY_MAP", "").split(";"))
    if old.strip() and new.strip()
}
# Old tag names shown under their new name, e.g. "Sport/Gym=Gym". Separate entries with ";".
TIME_TAG_MAP = {
    old.strip(): new.strip()
    for old, _, new in (e.partition("=") for e in os.environ.get("TIME_TAG_MAP", "").split(";"))
    if old.strip() and new.strip()
}
# Local model that writes the "Summary" bullets at the top. Leave OLLAMA_URL empty to skip it.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "")
MODEL = os.environ.get("MODEL", "qwen3.5:9b")
# Off by default: email summaries are untrusted text, and keeping them out of the brief lets
# a chat model that reads the brief safely have write tools (notes, tasks).
EMAIL_IN_BRIEF = os.environ.get("EMAIL_IN_BRIEF", "no").lower() in ("1", "yes", "true")
# "no": only show the queue and leave it for the 08:00 digest (the brief can then mount it read-only).
EMAIL_CONSUME = os.environ.get("EMAIL_CONSUME", "yes").lower() in ("1", "yes", "true")
# Email triage health file (written by triage.py); the brief warns if triage is broken or stale.
EMAIL_HEALTH = os.environ.get("EMAIL_HEALTH", "")
EMAIL_STALE_HOURS = float(os.environ.get("EMAIL_STALE_HOURS", "2"))
NTFY_URL = os.environ.get("NTFY_URL", "")
# With AGENT_PUSH_LINK set, the push is one line plus a link to the page; "full" sends the whole brief.
BRIEF_PUSH = os.environ.get("BRIEF_PUSH", "link").lower()
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()  # a pasted secret often ends in a newline

DUE_RE = re.compile(r"(?:📅|\[due::\s*)\s*(\d{4}-\d{2}-\d{2})\]?")
SCHED_RE = re.compile(r"(?:⏳|\[scheduled::\s*)\s*(\d{4}-\d{2}-\d{2})\]?")
TASK_RE = re.compile(r"^\s*[-*]\s+\[(.)\]\s+(.*)$")
META_RE = re.compile(r"\s*(?:[📅⏳🛫✅➕❌]\s*\d{4}-\d{2}-\d{2}|\[\w+::[^\]]*\]|[⏫🔼🔽🔺⏬]|🔁[^📅⏳🛫✅]*)")
HIGH = ("🔺", "⏫")
# Obsidian Tasks priorities, highest first; 3 = no priority set.
PRIORITY = {"🔺": 0, "⏫": 1, "🔼": 2, "🔽": 4, "⏬": 5}
PRIO_ICON = {v: k for k, v in PRIORITY.items()}
STRAY_DATE_RE = re.compile(r"\s*[📅📆🗓⏳🛫]\ufe0f?(?!\s*\d)")   # a date emoji left with no date after it
HEADING_RE = re.compile(r"^#{2,6}\s+(.*?)\s*#*$")   # "## Inbox"; the "# To-do" title is not a section


# ---------- calendar ----------

def newest_ics(folder: Path):
    files = sorted(folder.glob("*.ics"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _as_date(v):
    return v.date() if isinstance(v, datetime) else v


def fix_broken_repeat(ev):
    """A repeat rule that ends before the event starts (a known Fossify export quirk)
    would hide the event entirely; treat it as a one-off event instead."""
    rule = ev.get("RRULE")
    if not rule or not rule.get("UNTIL") or not ev.get("DTSTART"):
        return
    try:
        if _as_date(rule["UNTIL"][0]) < _as_date(ev.decoded("DTSTART")):
            del ev["RRULE"]
    except Exception:
        pass


def load_events(ics_path: Path, start: datetime, end: datetime):
    """Expand events (including repeats) between start and end.

    Fossify exports sometimes contain repeat rules that end before they start, and
    duplicate UIDs. Each event is expanded on its own, so one bad event is skipped
    instead of breaking the whole calendar.
    """
    cal = icalendar.Calendar.from_ical(ics_path.read_bytes())
    groups = defaultdict(lambda: {"masters": [], "overrides": []})
    for ev in cal.walk("VEVENT"):
        fix_broken_repeat(ev)
        key = str(ev.get("UID", id(ev)))
        groups[key]["overrides" if ev.get("RECURRENCE-ID") else "masters"].append(ev)

    out, skipped = [], 0
    for g in groups.values():
        batches = [[m] for m in g["masters"]] or [[]]
        batches[0] += g["overrides"]  # attach modified occurrences to the first master
        for batch in batches:
            mini = icalendar.Calendar()
            for ev in batch:
                mini.add_component(ev)
            try:
                out.extend(recurring_ical_events.of(mini).between(start, end))
            except Exception:
                skipped += len(batch)
    return out, skipped


# Calendar tasks this many days in the past and not ticked off still show as overdue.
CAL_TASK_OVERDUE_DAYS = int(os.environ.get("CAL_TASK_OVERDUE_DAYS", "14"))


def load_calendar_tasks(ics_path: Path, today: date, end: datetime):
    """Open Fossify tasks (VTODO, date in DTSTART, done = STATUS:COMPLETED) as to-do items.

    A repeating task shows only its next occurrence: Fossify does not export which past
    occurrences were ticked off, so they would all look overdue.
    """
    cal = icalendar.Calendar.from_ical(ics_path.read_bytes())
    start = datetime.combine(today - timedelta(days=CAL_TASK_OVERDUE_DAYS), datetime.min.time(), TZ)
    out, skipped = [], 0
    for todo in cal.walk("VTODO"):
        if str(todo.get("STATUS", "")).upper() == "COMPLETED" or not todo.get("DTSTART"):
            continue
        fix_broken_repeat(todo)
        mini = icalendar.Calendar()
        mini.add_component(todo)
        try:
            occurrences = recurring_ical_events.of(mini, components=["VTODO"]).between(start, end)
        except Exception:
            skipped += 1
            continue
        if todo.get("RRULE"):
            day_of = lambda o: _as_date(to_local(o.decoded("DTSTART")))
            occurrences = sorted((o for o in occurrences if day_of(o) >= today), key=day_of)[:1]
        for occ in occurrences:
            s = to_local(occ.decoded("DTSTART"))
            day = s.date() if isinstance(s, datetime) else s
            title = str(occ.get("SUMMARY", "(no title)")).strip()
            out.append({"text": (f"{s:%H:%M} " if isinstance(s, datetime) else "") + title,
                        "due": day, "high": False, "prio": 3, "section": "", "file": ics_path.name, "line": 0})
    return out, skipped


def to_local(value):
    if isinstance(value, datetime):
        return value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)
    return value  # all-day date


def event_rows(events):
    rows = []
    for ev in events:
        s = to_local(ev.decoded("DTSTART"))
        e = to_local(ev.decoded("DTEND")) if ev.get("DTEND") else None
        all_day = not isinstance(s, datetime)
        day = s if all_day else s.date()
        rows.append({
            "day": day,
            "sort": datetime.combine(day, datetime.min.time(), TZ) if all_day else s,
            "time": "All day" if all_day else s.strftime("%H:%M") + (f"–{e:%H:%M}" if e and e != s else ""),
            "title": str(ev.get("SUMMARY", "(no title)")).strip(),
            "where": str(ev.get("LOCATION", "")).strip(),
            "end": e if (not all_day and isinstance(e, datetime)) else None,
        })
    rows.sort(key=lambda r: (r["sort"], r["title"]))
    # drop exact duplicates (same title at same time), common with duplicate UIDs
    seen, unique = set(), []
    for r in rows:
        k = (r["sort"], r["title"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


# ---------- tasks ----------

def parse_tasks(paths):
    tasks, missing = [], []
    for p in paths:
        if not p.exists():
            missing.append(str(p))
            continue
        section = ""
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            h = HEADING_RE.match(line)
            if h:
                section = h.group(1)  # "## Computer science": shown next to the task
                continue
            m = TASK_RE.match(line)
            if not m or m.group(1) != " ":
                continue  # not a task, or done [x] / cancelled [-]
            text = m.group(2)
            due = DUE_RE.search(text) or SCHED_RE.search(text)
            prio = min((v for k, v in PRIORITY.items() if k in text), default=3)
            tasks.append({
                "text": STRAY_DATE_RE.sub("", META_RE.sub("", text)).strip(),
                "due": date.fromisoformat(due.group(1)) if due else None,
                "high": prio <= 1, "prio": prio, "section": section,
                "file": p.name, "line": n,
            })
    return tasks, missing


DONE_RE = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")


def done_counts(paths, today: date):
    """Tasks ticked off (Obsidian Tasks adds '✅ date') in the last 7 days vs the 7 before."""
    recent = before = 0
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            m = TASK_RE.match(line)
            d = DONE_RE.search(line)
            if m and m.group(1).lower() == "x" and d:
                age = (today - date.fromisoformat(d.group(1))).days
                if 0 <= age < 7:
                    recent += 1
                elif 7 <= age < 14:
                    before += 1
    return recent, before


# ---------- time tracking (SimpleTimeTracker CSV export) ----------
# Header written by the app: activity name,time started,time ended,comment,categories,
# record tags,duration,duration minutes. Times are local "yyyy-MM-dd HH:mm:ss", UTC
# "yyyy-MM-ddTHH:mm:ssZ" or with an offset, depending on the export setting.

def parse_stt_time(value: str):
    value = value.strip()
    if value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)


TAG_VALUE_RE = re.compile(r"^(.*?)\s*\((-?\d+(?:[.,]\d+)?)\)$")  # "PullUps (130)"


def parse_tags(value: str):
    """Record tags as (name, value or None); the app writes a tag value as "Name (130)"."""
    out = []
    for item in (t.strip() for t in (value or "").split(",")):
        if not item:
            continue
        m = TAG_VALUE_RE.match(item)
        out.append((m.group(1), float(m.group(2).replace(",", "."))) if m else (item, None))
    return out


def load_time_records(folder: Path):
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
    if not files:
        return None, []
    text = files[0].read_text(encoding="utf-8-sig")
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            start = parse_stt_time(row["time started"])
            end = parse_stt_time(row["time ended"])
        except (KeyError, ValueError):
            continue
        activity = row.get("activity name", "").strip()
        tags = [(TIME_TAG_MAP.get(n, n), v) for n, v in parse_tags(row.get("record tags", ""))]
        if activity in TIME_ACTIVITY_MAP:
            activity, extra = TIME_ACTIVITY_MAP[activity]
            if extra and extra not in {n for n, _ in tags}:
                tags.insert(0, (extra, None))
        records.append({"activity": activity, "tags": tags, "start": start,
                        "minutes": max(0, int((end - start).total_seconds() // 60))})
    return files[0], records


def time_comparison(records, now: datetime):
    """Minutes per activity: this week so far vs all of last week (Mon–Sun), plus this
    week's tags per activity. Monday: last week vs the week before.

    Returns the label, the names of the two weeks and whether both weeks are complete,
    and rows of (activity, [current, previous], {tag: [minutes, value total or None]}).
    """
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time(), TZ)
    if now.weekday() == 0:
        cur = (monday - timedelta(days=7), monday)
        label, words = "last week vs the week before", ("last week", "the week before", True)
    else:
        cur = (monday, now)
        label, words = f"this week so far (Mon–{now:%a}) vs all of last week", ("this week", "last week", False)
    prev = (cur[0] - timedelta(days=7), cur[0])
    totals = defaultdict(lambda: [0, 0])          # activity -> [current, previous]
    tags = defaultdict(dict)                      # activity -> {tag: [minutes, value]}
    for r in records:
        slot = 0 if cur[0] <= r["start"] < cur[1] else 1 if prev[0] <= r["start"] < prev[1] else None
        if slot is None:
            continue
        totals[r["activity"]][slot] += r["minutes"]
        if slot == 0:
            for name, value in r["tags"]:
                t = tags[r["activity"]].setdefault(name, [0, None])
                t[0] += r["minutes"]
                if value is not None:
                    t[1] = (t[1] or 0) + value
    rows = sorted(((a, mins, tags.get(a, {})) for a, mins in totals.items()),
                  key=lambda row: (-row[1][0], -row[1][1]))
    return label, words, rows


def hm(minutes: int) -> str:
    """2h 30m, 2h, 15m."""
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h and m else f"{h}h" if h else f"{m}m"


def trend(cur: int, prev: int, complete: bool) -> str:
    """▲/▼ against the other week. Mid-week only "▲" (part of a week always looks like less)."""
    if not complete:
        return " ▲" if cur > prev else ""
    if cur and prev and cur != prev:
        return f" {'▲' if cur > prev else '▼'} {round((cur - prev) * 100 / prev):+d}%"
    return " ▲" if cur > prev else " ▼" if cur < prev else ""


def tag_text(tags) -> str:
    """'Calisthenics 2h, PullUps&PushUps 1h (105)', biggest first."""
    items = sorted(tags.items(), key=lambda kv: -kv[1][0])[:5]
    return ", ".join(f"{name} {hm(m)}" + (f" ({value:g})" if value is not None else "") for name, (m, value) in items)


def time_line(name: str, cur: int, prev: int, words) -> str:
    """Plain sentence for the model, e.g. "Exercise: 2h 30m this week, 1h 30m last week ▲"."""
    now_word, before_word, complete = words
    return f"{name}: {hm(cur)} {now_word}, {hm(prev)} {before_word}{trend(cur, prev, complete)}"


# ---------- local model summary ----------

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
    "required": ["bullets"],
}

SUMMARY_PROMPT = (
    "You write the top of my personal morning brief. You get FACTS computed by code from my "
    "calendar, to-do list and time tracking. Write 3 to 6 short bullets, most important first: "
    "what matters today, what to prepare for in the coming days, and notable changes in how I "
    "spent my time or how many tasks I finished. Mid-week, this week's time is only part of a week: "
    "never call it less than last week. Be direct and specific, like a good assistant. "
    "Use only the FACTS. Every number you mention must appear in FACTS exactly as written. "
    "Never invent events, tasks or reasons. If nothing stands out, say so in one bullet."
)

NUMBER_RE = re.compile(r"\d+(?:[.:]\d+)?")


def model_summary(facts_text: str):
    """Ask the local model for summary bullets. Bullets containing a number that is not in the
    facts are dropped, so the model can phrase and prioritise but cannot make figures up."""
    payload = {
        "model": MODEL, "stream": False, "think": False, "format": SUMMARY_SCHEMA,
        "options": {"temperature": 0.2},
        "messages": [{"role": "system", "content": SUMMARY_PROMPT},
                     {"role": "user", "content": "FACTS:\n" + facts_text}],
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        bullets = json.loads(json.loads(resp.read())["message"]["content"]).get("bullets", [])
    allowed = set(NUMBER_RE.findall(facts_text))
    kept = [b.strip() for b in bullets
            if isinstance(b, str) and b.strip() and set(NUMBER_RE.findall(b)) <= allowed]
    return kept[:6], len(bullets) - len(kept[:6])


# ---------- email ----------

def email_items(consume: bool):
    if not EMAIL_DIGEST.exists():
        return []
    items = [json.loads(l) for l in EMAIL_DIGEST.read_text().splitlines() if l.strip()]
    if consume and EMAIL_CONSUME:
        EMAIL_DIGEST.write_text("")
    order = ["urgent", "work", "personal", "purchase", "jobs", "unsure", "info", "ads"]
    items = [it for it in items if not it.get("quiet")]  # model-filed info/ads stay in the digest only
    return sorted(items, key=lambda it: order.index(it["category"]) if it["category"] in order else len(order))


def pushed_items(now: datetime):
    if not EMAIL_PUSHED.exists():
        return []
    cutoff = now.timestamp() - PUSHED_HOURS * 3600
    items = [json.loads(l) for l in EMAIL_PUSHED.read_text().splitlines() if l.strip()]
    return [it for it in items if it.get("at", 0) >= cutoff]


# ---------- build ----------

def email_summary(pushed, mails):
    """One Summary line built from counts, so no email text ever reaches the model."""
    if not pushed and not mails:
        return "Email: nothing new that needs your attention."
    def by_cat(items):
        n = Counter(m["category"] for m in items)
        return ", ".join(f"{v} {k}" for k, v in n.most_common())
    parts = []
    if pushed:
        parts.append(f"{len(pushed)} pushed to your phone in the last {PUSHED_HOURS} hours ({by_cat(pushed)})")
    if mails:
        parts.append(f"{len(mails)} waiting for the digest ({by_cat(mails)})")
    return "Email: " + "; ".join(parts) + "."


# Email in the brief: grouped under a heading per category, most important first.
EMAIL_GROUPS = [("urgent", "Urgent"), ("work", "Work"), ("personal", "Personal"), ("purchase", "Purchases"),
                ("jobs", "Jobs"), ("unsure", "Unsure"), ("info", "Info"), ("ads", "Ads")]


def email_groups(items):
    """- **Purchases** (3)
      - **Amazon.co.uk**: summary or subject"""
    order = [c for c, _ in EMAIL_GROUPS] + sorted({m["category"] for m in items} - {c for c, _ in EMAIL_GROUPS})
    names = dict(EMAIL_GROUPS)
    out = []
    for cat in order:
        group = [m for m in items if m["category"] == cat]
        if group:
            out.append(f"- **{names.get(cat, cat.capitalize())}** ({len(group)})")
            out += [f"  - **{m['from_name']}**: {m.get('summary') or m['subject']}" for m in group]
    return out


def fmt_day(d: date, today: date):
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %d %b")


def email_health_warnings(path: Path, now: datetime):
    if not path.exists():
        return ["Email triage has not run yet, so urgent emails are not being watched."]
    try:
        h = json.loads(path.read_text())
    except Exception:
        return [f"Email triage health file {path.name} could not be read."]
    if h.get("fails"):
        return [f"Email triage has failed {h['fails']} runs in a row (last error: "
                f"{h.get('last_error', 'unknown')[:120]}). Urgent emails may be missed."]
    last_ok = h.get("last_ok")
    if last_ok and (now.timestamp() - last_ok) / 3600 > EMAIL_STALE_HOURS:
        return [f"Email triage last ran {(now.timestamp() - last_ok) / 3600:.0f} hours ago: "
                "check its timer (systemctl --user list-timers)."]
    return []


def age_hours(p: Path, now: datetime):
    return (now - datetime.fromtimestamp(p.stat().st_mtime, TZ)).total_seconds() / 3600


def has_word(title: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", title.lower()) is not None


def next_month(today: date):
    first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first, last


def build(now: datetime, consume_email: bool):
    today = now.date()
    lines, warnings = [f"# Morning brief, {now:%A %d %B}", ""], []

    # calendar: newest Fossify backup + every file in the extra folder
    ics = newest_ics(CAL_DIR) if CAL_DIR.exists() else None
    extra = sorted(EXTRA_ICS_DIR.glob("*.ics")) if EXTRA_ICS_DIR.exists() else []
    nm_first, nm_last = next_month(today)
    start = datetime.combine(today, datetime.min.time(), TZ)
    horizon = max(today + timedelta(days=LATER_DAYS), nm_last) + timedelta(days=1)
    end = datetime.combine(horizon, datetime.min.time(), TZ)
    events, cal_tasks = [], []
    if ics is None:
        warnings.append(f"No calendar file found in {CAL_DIR}.")
    else:
        h = age_hours(ics, now)
        if h > STALE_HOURS:
            warnings.append(f"Calendar backup is {h / 24:.1f} days old: check Fossify backups and Syncthing.")
    for f in ([ics] if ics else []) + extra:
        try:
            evs, skipped = load_events(f, start, end)
            events += evs
            tks, skipped_tasks = load_calendar_tasks(f, today, end)
            cal_tasks += tks
            skipped += skipped_tasks
            if skipped:
                warnings.append(f"{skipped} event(s) in {f.name} could not be read and were skipped.")
        except Exception as exc:
            warnings.append(f"Calendar file {f.name} could not be read: {exc}")
    rows = event_rows(events)
    week_end = today + timedelta(days=LOOKAHEAD_DAYS)
    today_rows = [r for r in rows if r["day"] == today]
    soon_rows = [r for r in rows if today < r["day"] <= week_end]
    later_rows, seen_titles = [], set()
    for r in rows:  # important events only; repeats shown once
        if (week_end < r["day"] <= today + timedelta(days=LATER_DAYS)
                and any(has_word(r["title"], k) for k in LATER_KEYWORDS)
                and r["title"].lower() not in seen_titles):
            seen_titles.add(r["title"].lower())
            later_rows.append(r)

    lines.append("## Today")
    if today_rows:
        lines += [f"- {r['time']}  {r['title']}" + (f" ({r['where']})" if r["where"] else "") for r in today_rows]
    else:
        lines.append("- Nothing in the calendar.")
    lines.append("")

    lines.append(f"## Next {LOOKAHEAD_DAYS} days")
    if soon_rows:
        lines += [f"- {fmt_day(r['day'], today)} {r['time']}  {r['title']}" for r in soon_rows]
    else:
        lines.append("- Nothing coming up.")
    lines.append("")

    if later_rows:
        lines.append(f"## Later (up to {LATER_DAYS} days)")
        lines += [f"- {r['day']:%a %d %b} {r['time']}  {r['title']}" for r in later_rows[:15]]
        lines.append("")

    # tasks (read here so the month preview can list next month's dated tasks)
    tasks, missing = parse_tasks(TODO_FILES)
    tasks += cal_tasks  # Obsidian and Fossify tasks are listed together
    for m in missing:
        warnings.append(f"To-do file not found: {m}")
    overdue = sorted([t for t in tasks if t["due"] and t["due"] < today], key=lambda t: (t["due"], t["prio"]))
    # by priority; then Obsidian first (file order), then calendar by time
    due_today = sorted([t for t in tasks if t["due"] == today], key=lambda t: (t["prio"], t["text"] if t["line"] == 0 else ""))
    this_week = sorted([t for t in tasks if t["due"] and today < t["due"] <= today + timedelta(days=LOOKAHEAD_DAYS)],
                       key=lambda t: t["due"])
    undated = [t for t in tasks if not t["due"]]
    undated.sort(key=lambda t: t["prio"])  # highest priority first, otherwise file order

    if today.day >= MONTH_PREVIEW_FROM_DAY:
        # events already listed above are skipped; repeats are shown once
        shown = {r["title"].lower() for r in later_rows} | {(r["day"], r["title"].lower()) for r in soon_rows}
        month_rows, times = [], Counter()
        for r in rows:
            t = r["title"].lower()
            if nm_first <= r["day"] <= nm_last and t not in shown and (r["day"], t) not in shown:
                times[t] += 1
                if times[t] == 1:
                    month_rows.append(r)
        month_tasks = sorted([t for t in tasks if t["due"] and nm_first <= t["due"] <= nm_last
                              and t["due"] > today + timedelta(days=LOOKAHEAD_DAYS)], key=lambda t: t["due"])
        lines.append(f"## {nm_first:%B} at a glance")
        lines += [f"- {r['day']:%a %d}  {r['title']}" + (f" (x{n})" if (n := times[r['title'].lower()]) > 1 else "")
                  for r in month_rows[:12]]
        if len(month_rows) > 12:
            lines.append(f"- +{len(month_rows) - 12} more events")
        lines += [f"- Task due {t['due']:%a %d}: {t['text']}" for t in month_tasks[:8]]
        if not month_rows and not month_tasks:
            lines.append("- Nothing in the calendar or to-do list yet.")
        lines.append("")

    lines.append("## Tasks")
    # "⏫ Task · _Computer science_": the Obsidian priority icon first, the list section last
    star = lambda t: f"{PRIO_ICON[t['prio']]} " if t["prio"] != 3 else ""
    where = lambda t: f" · _{t['section']}_" if t["section"] else ""
    if overdue:
        lines += ["", f"### Overdue ({len(overdue)})"]
        lines += [f"- {star(t)}{t['text']} (was due {t['due']:%a %d %b}){where(t)}" for t in overdue]
    if due_today:
        lines += ["", f"### Due today ({len(due_today)})"]
        lines += [f"- {star(t)}{t['text']}{where(t)}" for t in due_today]
    if this_week:
        lines += ["", f"### Due in the next {LOOKAHEAD_DAYS} days ({len(this_week)})"]
        lines += [f"- {fmt_day(t['due'], today)}: {star(t)}{t['text']}{where(t)}" for t in this_week]
    if undated:
        more = f", showing {UNDATED_SHOWN}" if len(undated) > UNDATED_SHOWN else ""
        lines += ["", f"### No date ({len(undated)}{more})"]
        lines += [f"- {star(t)}{t['text']}{where(t)}" for t in undated[:UNDATED_SHOWN]]
    if not tasks:
        lines.append("- No open tasks.")
    lines.append("")

    # time tracking
    time_file, records = load_time_records(TIME_DIR)
    time_label, words, time_rows = time_comparison(records, now) if records else ("", None, [])
    if time_file is None and TIME_DIR.exists():  # no folder = feature not set up, so stay quiet
        warnings.append(f"No time-tracking export (.csv) found in {TIME_DIR}.")
    elif time_file is not None and age_hours(time_file, now) > STALE_HOURS:
        warnings.append(f"Time-tracking export is {age_hours(time_file, now) / 24:.1f} days old.")
    if time_rows:
        now_word, before_word, complete = words
        lines += ["## Time tracked", f"_{time_label[0].upper() + time_label[1:]}_", ""]
        for name, (c, p), tags in time_rows[:8]:
            lines.append(f"- **{name}** {hm(c)} · {before_word} {hm(p)}{trend(c, p, complete)}")
            if tags:
                lines.append(f"  - {tag_text(tags)}")
        lines.append("")

    # email
    if EMAIL_IN_BRIEF:
        pushed, mails = pushed_items(now), email_items(consume_email)
        lines.append("## Email")
        if pushed:
            lines += ["", f"### Pushed in the last {PUSHED_HOURS} hours ({len(pushed)})"]
            lines += email_groups(pushed[-10:][::-1])  # newest first
        if mails:
            lines += ["", f"### Waiting for the digest ({len(mails)})"]
            lines += email_groups(mails[:10])
            if len(mails) > 10:
                lines.append(f"- +{len(mails) - 10} more in the digest")
        if not pushed and not mails:
            lines.append("- Nothing new that needs your attention.")
        lines.append("")
        email_bullet, email_count = email_summary(pushed, mails), len(pushed) + len(mails)
    else:
        email_bullet, email_count = "", None

    # facts for the local model: plain sentences built by code, so every figure is checked
    done_recent, done_before = done_counts(TODO_FILES, today)
    per_day = defaultdict(list)
    for r in today_rows + soon_rows:
        per_day[r["day"]].append(r)
    busiest = max(per_day.items(), key=lambda kv: len(kv[1]), default=(None, []))
    clashes = []
    for day, rs in per_day.items():
        timed = sorted((r for r in rs if r["end"]), key=lambda r: r["sort"])
        clashes += [f"{a['title']} and {b['title']} overlap on {day:%a %d %b}"
                    for a, b in zip(timed, timed[1:]) if b["sort"] < a["end"]]
    facts = [f"Today is {now:%A %d %B}."]
    facts.append("Today's events: " + ("; ".join(f"{r['time']} {r['title']}" for r in today_rows) or "none") + ".")
    facts.append(f"Events in the next {LOOKAHEAD_DAYS} days: {len(soon_rows)}"
                 + (f"; busiest day {busiest[0]:%a %d %b} with {len(busiest[1])} events" if busiest[0] else "") + ".")
    facts += [f"{r['day']:%a %d %b}: {r['title']}." for r in soon_rows]
    facts += [f"Clash: {c}." for c in clashes]
    facts += [f"Coming later: {r['day']:%a %d %b} {r['title']}." for r in later_rows]
    facts.append(f"Tasks: {len(overdue)} overdue, {len(due_today)} due today, "
                 f"{len(this_week)} due in the next {LOOKAHEAD_DAYS} days, {len(undated)} open without a date.")
    facts += [f"Overdue: {t['text']} (due {t['due']:%a %d %b})." for t in overdue]
    facts += [f"Due today: {t['text']}." for t in due_today]
    facts.append(f"Tasks completed: {done_recent} in the last 7 days vs {done_before} in the 7 days before.")
    if time_rows:
        facts.append(f"Time tracked, {time_label}:")
        facts += [f"{time_line(name, c, p, words)}." + (f" Tags this period: {tag_text(tags)}." if tags else "")
                  for name, (c, p), tags in time_rows[:8]]
    facts_text = "\n".join(facts)

    bullets = []
    if OLLAMA_URL:
        try:
            bullets, dropped = model_summary(facts_text)
            if dropped:
                warnings.append(f"{dropped} summary line(s) from the model were dropped because "
                                "they contained numbers not in your data.")
        except Exception as exc:
            warnings.append(f"Summary not written (local model unavailable: {exc}).")
    if email_bullet:
        bullets.append(email_bullet)

    if EMAIL_HEALTH:
        warnings += email_health_warnings(Path(EMAIL_HEALTH), now)

    if warnings:
        lines.append("## Check")
        lines += [f"- {w}" for w in warnings]
        lines.append("")

    lines.append(f"_Built {now:%H:%M}. Calendar file: {ics.name if ics else 'none'}._")
    counts = {"today": len(today_rows), "soon": len(soon_rows), "later": len(later_rows),
              "overdue": len(overdue),
              "due_today": len(due_today), "warnings": len(warnings), "email": email_count}
    # right under the title: the counts line first, then the model's bullets and the email line
    lines[2:2] = ["## Summary", f"- **{headline(counts)}**"] + [f"- {b}" for b in bullets] + [""]
    build.facts_text = facts_text  # kept for --facts and tests
    return "\n".join(lines), counts


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def headline(counts) -> str:
    """e.g. "0 events today, 1 task due, 3 emails, check warnings"."""
    return (f"{plural(counts['today'], 'event')} today, {plural(counts['due_today'] + counts['overdue'], 'task')} due"
            + (f", {plural(counts['email'], 'email')}" if counts.get("email") else "")
            + (", check warnings" if counts["warnings"] else ""))


def notify(text, counts):
    # the push title is the "# Morning brief, <date>" line, so the body starts at the Summary
    first, _, rest = text.partition("\n")
    headers = {"Title": first.lstrip("# ").strip()}
    page = agentdb.link()
    if page and BRIEF_PUSH != "full":
        # Short push: the one-line headline, and a tap opens the brief on the agent page.
        page += "#brief"
        headers["Click"] = page
        headers["Actions"] = f"view, Open brief, {page}"
        text = f"{headline(counts)}\nTap to read the brief."
    else:
        headers["Markdown"] = "yes"
        if page:
            headers["Click"] = page
        text = rest.lstrip("\n") if first.startswith("# ") else text
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    body = text.encode("utf-8")[:4000].decode("utf-8", "ignore").encode("utf-8")  # never cut a character in half
    req = urllib.request.Request(NTFY_URL, data=body, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:
        print(f"ntfy failed: {exc}", file=sys.stderr)
        return
    agentdb.record_push(NTFY_URL, headers["Title"], body.decode("utf-8"), "default", page)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print only; write and send nothing")
    ap.add_argument("--facts", action="store_true", help="also print the facts given to the model")
    args = ap.parse_args()
    now = datetime.now(TZ)
    text, counts = build(now, consume_email=not args.print)
    if args.facts:
        print("FACTS GIVEN TO THE MODEL:\n" + build.facts_text + "\n")
    if args.print:
        print(text)
        sys.exit(0)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT_FILE)
    agentdb.record_brief(now.date().isoformat(), text)
    if NTFY_URL:
        notify(text, counts)
    agentdb.refresh()
    print(text)
