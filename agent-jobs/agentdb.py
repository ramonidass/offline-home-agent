"""The agent's own database, and the two files built from it after every run.

agent.db (SQLite, stays on the server):
  emails   every email triage sorted: when, category, rule or model, sender, subject, summary
  pushes   every ntfy notification the agent sent, with the link it carried
  briefs   one morning brief per day
  runs     every triage run: ok or the error

Built from it (both replaced atomically, so readers never see half a file):
  web/data.json        what the agent page shows (emails, briefs, pushes, health)
  stats/agent-stats.db counts only, no subjects or names: synced to the NAS for Grafana

Standard library only. Everything is off unless AGENT_DB is set, and a problem here never
stops triage or the brief: it prints a warning and they carry on.
"""
import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

DB_PATH = os.environ.get("AGENT_DB", "")
WEB_DIR = Path(os.environ.get("AGENT_WEB_DIR", "/data/web"))
STATS_DIR = Path(os.environ.get("AGENT_STATS_DIR", "/data/stats"))
# Link put on every ntfy push (tapping it opens this). Always the same address, so a browser
# that reuses tabs can match it; the page itself shows what is new. Empty = pushes carry no link.
PUSH_LINK = os.environ.get("AGENT_PUSH_LINK", "")

KEEP_DAYS = 120          # rows older than this are deleted
PAGE_EMAIL_DAYS = 30     # the page shows this many days of email...
PAGE_EMAIL_MAX = 500     # ...up to this many
PAGE_BRIEFS = 14
PAGE_PUSH_DAYS = 14
BODY_KEEP_DAYS = 30      # full email text: kept only as long as the page shows the email

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    uidvalidity TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    received    TEXT,
    processed   TEXT NOT NULL,
    category    TEXT NOT NULL,
    source      TEXT NOT NULL,
    from_name   TEXT,
    from_addr   TEXT,
    subject     TEXT,
    summary     TEXT,
    pushed      INTEGER NOT NULL DEFAULT 0,
    quiet       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS bodies (
    uidvalidity TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    body        TEXT NOT NULL,
    PRIMARY KEY (uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS pushes (
    id       INTEGER PRIMARY KEY,
    sent     TEXT NOT NULL,
    topic    TEXT,
    title    TEXT,
    message  TEXT,
    priority TEXT,
    link     TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
    day      TEXT PRIMARY KEY,
    built    TEXT NOT NULL,
    markdown TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id    INTEGER PRIMARY KEY,
    ts    TEXT NOT NULL,
    job   TEXT NOT NULL,
    ok    INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS emails_processed ON emails (processed);
CREATE INDEX IF NOT EXISTS pushes_sent ON pushes (sent);
CREATE INDEX IF NOT EXISTS runs_job_ts ON runs (job, ts);
"""


def enabled():
    return bool(DB_PATH)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _warn(what, exc):
    print(f"agentdb: {what} failed: {exc}", file=sys.stderr)


def connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript(SCHEMA)
    return conn


def _write(fn, *args):
    """Run one write in its own short transaction; never raise."""
    if not enabled():
        return
    try:
        conn = connect()
        try:
            with conn:
                fn(conn, *args)
        finally:
            conn.close()
    except Exception as exc:
        _warn(fn.__name__, exc)


def link(_section=""):
    """The link for a push. One fixed address for every push (see PUSH_LINK)."""
    return PUSH_LINK


def email_anchor(uidvalidity, uid):
    return f"email-{uidvalidity}-{uid}"


def received_at(date_header):
    try:
        return _iso(parsedate_to_datetime(date_header))
    except Exception:
        return None


# ---------- writes ----------

def _email(conn, item, uidvalidity, received, pushed, quiet, body):
    if body:
        conn.execute("INSERT OR REPLACE INTO bodies VALUES (?,?,?)", (str(uidvalidity), int(item["uid"]), body))
    conn.execute(
        "INSERT OR REPLACE INTO emails VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uidvalidity), int(item["uid"]), received, _iso(_now()), item["category"],
         item.get("source", ""), item.get("from_name", ""), item.get("from", ""),
         item.get("subject", ""), item.get("summary", ""), int(bool(pushed)), int(bool(quiet))))


def record_email(item, uidvalidity, received=None, pushed=False, quiet=False, body=""):
    _write(_email, item, uidvalidity, received, pushed, quiet, body)


def _import_queued(conn, uidvalidity, digest_file, pushed_file):
    for path, pushed in ((digest_file, False), (pushed_file, True)):
        if not path or not Path(path).exists():
            continue
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                it = json.loads(line)
            except ValueError:
                continue
            if "uid" not in it or "category" not in it:
                continue
            at = _iso(datetime.fromtimestamp(it["at"], timezone.utc)) if it.get("at") else _iso(_now())
            conn.execute(
                "INSERT OR IGNORE INTO emails VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uidvalidity), int(it["uid"]), None, at, it["category"], it.get("source", ""),
                 it.get("from_name", ""), it.get("from", ""), it.get("subject", ""), it.get("summary", ""),
                 int(pushed), int(bool(it.get("quiet")))))


def import_queued(uidvalidity, digest_file, pushed_file):
    """Mail sorted before the database existed still sits in the digest queue and the pushed log:
    copy it in so the page isn't empty at the start. Rows already present are left alone."""
    _write(_import_queued, uidvalidity, digest_file, pushed_file)


def _mark_pushed(conn, uidvalidity, uids):
    conn.executemany("UPDATE emails SET pushed = 1 WHERE uidvalidity = ? AND uid = ?",
                     [(str(uidvalidity), int(u)) for u in uids])


def mark_pushed(uidvalidity, uids):
    _write(_mark_pushed, uidvalidity, uids)


def _push(conn, topic, title, message, priority, link_):
    conn.execute("INSERT INTO pushes (sent, topic, title, message, priority, link) VALUES (?,?,?,?,?,?)",
                 (_iso(_now()), topic, title, message[:2000], priority, link_))


def record_push(url, title, message, priority, link_):
    topic = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    _write(_push, topic, title, message, priority, link_)


def _brief(conn, day, markdown):
    conn.execute("INSERT OR REPLACE INTO briefs VALUES (?,?,?)", (day, _iso(_now()), markdown))


def record_brief(day, markdown):
    _write(_brief, day, markdown)


def _run(conn, job, ok, error):
    conn.execute("INSERT INTO runs (ts, job, ok, error) VALUES (?,?,?,?)",
                 (_iso(_now()), job, int(bool(ok)), (error or "")[:300]))


def record_run(job, ok, error=""):
    _write(_run, job, ok, error)


def _prune(conn):
    cutoff = _iso(_now() - timedelta(days=KEEP_DAYS))
    conn.execute("DELETE FROM emails WHERE processed < ?", (cutoff,))
    conn.execute("DELETE FROM bodies WHERE NOT EXISTS (SELECT 1 FROM emails e WHERE e.uidvalidity = bodies.uidvalidity "
                 "AND e.uid = bodies.uid AND e.processed >= ?)", (_iso(_now() - timedelta(days=BODY_KEEP_DAYS)),))
    conn.execute("DELETE FROM pushes WHERE sent < ?", (cutoff,))
    conn.execute("DELETE FROM runs WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM briefs WHERE day < ?", (cutoff[:10],))


# ---------- the brief's Markdown, as safe HTML ----------

def _inline(text):
    text = html.escape(text, quote=True)  # escape first: only the tags added below can exist
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"(?<![\w])_(.+?)_(?![\w])", r"<em>\1</em>", text)
    return text


def md_to_html(md):
    out, depth = [], 0      # depth: open <ul> levels; a sub-list (2) opens inside its parent's <li>

    def close_to(level):
        nonlocal depth
        while depth > level:
            out[-1] += "</li></ul>"
            depth -= 1

    for raw in md.splitlines():
        line = raw.rstrip()
        bullet = re.match(r"^(\s*)[-*] (.*)", line)
        if bullet:
            want = 2 if len(bullet.group(1)) >= 2 and depth >= 1 else 1
            if want > depth:
                out.append("<ul>")
                depth += 1
            else:
                close_to(want)
                out[-1] += "</li>"
            out.append(f"<li>{_inline(bullet.group(2))}")
            continue
        close_to(0)
        heading = re.match(r"^(#{1,4}) (.*)", line)
        if heading:
            level = min(len(heading.group(1)) + 1, 5)   # the page has its own h1
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    close_to(0)
    return "\n".join(out)


# ---------- reads ----------

def _health(conn):
    rows = conn.execute("SELECT ts, ok, error FROM runs WHERE job = 'triage' ORDER BY id DESC LIMIT 200").fetchall()
    last_ok = next((ts for ts, ok, _ in rows if ok), None)
    fails = 0
    for _, ok, _ in rows:
        if ok:
            break
        fails += 1
    last_error = rows[0][2] if rows and not rows[0][1] else ""
    return {"last_run": rows[0][0] if rows else None, "last_ok": last_ok,
            "fails": fails, "last_error": last_error}


def _atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_bodies(wanted):
    """One text file per email in web/mail/, fetched only when you open that email. A file is
    written once (the text never changes) and deleted when the email leaves the page."""
    mail_dir = WEB_DIR / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    for f in mail_dir.glob("*.txt"):
        if f.stem not in wanted:
            f.unlink()
    for name, body in wanted.items():
        path = mail_dir / f"{name}.txt"
        if not path.exists():
            _atomic_write_text(path, body)


def build_web(conn):
    since = _iso(_now() - timedelta(days=PAGE_EMAIL_DAYS))
    emails = [dict(zip(
        ("uidvalidity", "uid", "received", "processed", "category", "source", "from_name",
         "from_addr", "subject", "summary", "pushed", "quiet"), r))
        for r in conn.execute(
            "SELECT * FROM emails WHERE processed >= ? "
            "ORDER BY COALESCE(received, processed) DESC LIMIT ?", (since, PAGE_EMAIL_MAX))]
    bodies = {email_anchor(uv, uid): body for uv, uid, body in conn.execute("SELECT uidvalidity, uid, body FROM bodies")}
    for e in emails:
        e["id"] = email_anchor(e.pop("uidvalidity"), e.pop("uid"))
        e["pushed"], e["quiet"] = bool(e["pushed"]), bool(e["quiet"])
        e["has_body"] = e["id"] in bodies
    _write_bodies({e["id"]: bodies[e["id"]] for e in emails if e["has_body"]})
    briefs = [{"day": d, "built": b, "html": md_to_html(m)}
              for d, b, m in conn.execute("SELECT day, built, markdown FROM briefs ORDER BY day DESC LIMIT ?",
                                          (PAGE_BRIEFS,))]
    psince = _iso(_now() - timedelta(days=PAGE_PUSH_DAYS))
    pushes = [dict(zip(("sent", "topic", "title", "message", "priority", "link"), r))
              for r in conn.execute("SELECT sent, topic, title, message, priority, link FROM pushes "
                                    "WHERE sent >= ? ORDER BY sent DESC, id DESC LIMIT 300", (psince,))]
    for p in pushes:
        p["message"] = (p["message"] or "")[:400]
    data = {"generated": _iso(_now()), "health": _health(conn),
            "briefs": briefs, "emails": emails, "pushes": pushes}
    _atomic_write_text(WEB_DIR / "data.json", json.dumps(data, ensure_ascii=False))


def export_stats(conn):
    """Counts only (no subjects, names or addresses), for Grafana on the NAS."""
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    final = STATS_DIR / "agent-stats.db"
    tmp = STATS_DIR / "agent-stats.db.tmp"
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    try:
        out.executescript("""
            CREATE TABLE email_daily (time INTEGER, day TEXT, category TEXT, sorted_by TEXT,
                                      emails INTEGER, pushed INTEGER, quiet INTEGER);
            CREATE TABLE push_daily (time INTEGER, day TEXT, topic TEXT, pushes INTEGER);
            CREATE TABLE triage_daily (time INTEGER, day TEXT, ok_runs INTEGER, failed_runs INTEGER);
            CREATE TABLE briefs (time INTEGER, day TEXT, built TEXT);
            CREATE TABLE health (updated TEXT, last_ok TEXT, minutes_since_ok INTEGER, fails INTEGER);
        """)
        day_epoch = "CAST(strftime('%s', {0}) AS INTEGER)"
        rows = conn.execute(f"""
            SELECT {day_epoch.format("substr(COALESCE(received, processed), 1, 10)")},
                   substr(COALESCE(received, processed), 1, 10),
                   category,
                   CASE WHEN source LIKE 'rule%' THEN 'rule' ELSE 'model' END,
                   COUNT(*), SUM(pushed), SUM(quiet)
            FROM emails GROUP BY 2, 3, 4 ORDER BY 1""").fetchall()
        out.executemany("INSERT INTO email_daily VALUES (?,?,?,?,?,?,?)", rows)
        rows = conn.execute(f"""
            SELECT {day_epoch.format("substr(sent, 1, 10)")}, substr(sent, 1, 10), topic, COUNT(*)
            FROM pushes GROUP BY 2, 3 ORDER BY 1""").fetchall()
        out.executemany("INSERT INTO push_daily VALUES (?,?,?,?)", rows)
        rows = conn.execute(f"""
            SELECT {day_epoch.format("substr(ts, 1, 10)")}, substr(ts, 1, 10), SUM(ok), SUM(1 - ok)
            FROM runs WHERE job = 'triage' GROUP BY 2 ORDER BY 1""").fetchall()
        out.executemany("INSERT INTO triage_daily VALUES (?,?,?,?)", rows)
        rows = conn.execute(f"SELECT {day_epoch.format('day')}, day, built FROM briefs ORDER BY day").fetchall()
        out.executemany("INSERT INTO briefs VALUES (?,?,?)", rows)
        h = _health(conn)
        minutes = None
        if h["last_ok"]:
            minutes = int((_now() - datetime.fromisoformat(h["last_ok"])).total_seconds() // 60)
        out.execute("INSERT INTO health VALUES (?,?,?,?)", (_iso(_now()), h["last_ok"], minutes, h["fails"]))
        out.commit()
    finally:
        out.close()
    os.replace(tmp, final)


def refresh():
    """After a run: drop old rows, rebuild the page data and the Grafana stats."""
    if not enabled():
        return
    try:
        conn = connect()
        try:
            with conn:
                _prune(conn)
            build_web(conn)
            export_stats(conn)
        finally:
            conn.close()
    except Exception as exc:
        _warn("refresh", exc)
