"""Agent database, page data and Grafana stats, end to end through triage.py (fake IMAP, Ollama, ntfy)."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
home = Path(tempfile.mkdtemp())
data_dir = home / "data"
os.environ.update(
    EMAIL_AGENT_HOME=str(home),
    AGENT_DB=str(data_dir / "agent.db"),
    AGENT_WEB_DIR=str(data_dir / "web"),
    AGENT_STATS_DIR=str(data_dir / "stats"),
    AGENT_PUSH_LINK="http://192.168.8.20:8088/",
)
sys.path.insert(0, str(HERE))

(home / "rules.yaml").write_text("""
actions:
  urgent:   {notify: now,    label: "AI-Urgent"}
  work:     {notify: now,    label: "AI-Work"}
  purchase: {notify: digest, label: "AI-Purchase"}
  personal: {notify: digest, label: "AI-Personal"}
  info:     {notify: none,   label: "AI-Info"}
  ads:      {notify: none,   label: "AI-Ads"}
  unsure:   {notify: digest, label: "AI-Unsure"}
rules:
  - name: Work domain
    from_domain: ["acme.com"]
    category: work
model_hints:
  work: "job"
  purchase: "orders"
  personal: "a person"
  info: "notices"
  ads: "marketing"
""")

pushes = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/api/chat":
            text = json.loads(body)["messages"][1]["content"]
            cat = "ads" if "Sale" in text else "personal"
            reply = {"category": cat, "confident": True, "summary": "<b>model</b> summary"}
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"message": {"content": json.dumps(reply)}}).encode())
        else:
            pushes.append({"title": self.headers.get("Title"), "click": self.headers.get("Click")})
            self.send_response(200); self.end_headers()


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{port}/api/chat"
os.environ["NTFY_URL"] = f"http://127.0.0.1:{port}/email-agent"

import agentdb  # noqa: E402
import triage  # noqa: E402

EVIL = '<img src=x onerror="alert(1)">'


def mk(uid, frm, subject, body, date="Mon, 02 Nov 2026 07:30:00 +0000"):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = frm, subject, date
    m.set_content(body)
    return uid, m.as_bytes()


MESSAGES = [
    mk(11, "Boss <boss@acme.com>", "Q4 plan " + EVIL, "Please review."),
    mk(12, "Ana <ana@example.org>", "Dinner Friday?", "Are you free?"),
    mk(13, "Shop <news@shop.com>", "Big Sale", "50% off"),
]


class FakeIMAP:
    def __init__(self, msgs):
        self.msgs = dict(msgs)
        self.untagged_responses = {"UIDVALIDITY": [b"5"]}

    def select(self, mailbox, readonly=False):
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [" ".join(map(str, self.msgs)).encode()]
        if cmd == "FETCH":
            return "OK", [(b"x", self.msgs[int(args[0])]), b")"]
        return "OK", [b""]

    def create(self, name):
        pass

    def logout(self):
        pass


# 1) a triage run fills the database, links every push to the one fixed address
triage.connect = lambda: FakeIMAP(MESSAGES)
# mail sorted before the database existed (digest queue, pushed log) is imported once
(home / "digest.jsonl").write_text(json.dumps({"uid": 3, "category": "jobs", "from_name": "Adzuna", "from": "a@x",
    "subject": "Old job", "summary": "", "source": "rule: Job alerts"}) + "\nnot json\n")
(home / "pushed.jsonl").write_text(json.dumps({"uid": 4, "category": "purchase", "from_name": "Shop", "from": "s@x",
    "subject": "Order", "summary": "Shipped", "source": "model", "at": 1793577600}) + "\n")
triage.main([])
db = sqlite3.connect(os.environ["AGENT_DB"])
rows = {r[0]: r[1:] for r in db.execute("SELECT uid, category, source, pushed, quiet, received FROM emails")}
assert rows[11][:4] == ("work", "rule: Work domain", 1, 0), rows[11]
assert rows[12][:4] == ("personal", "model", 0, 0), rows[12]
assert rows[13][:4] == ("ads", "model", 0, 1), rows[13]          # model said ads: quiet, still recorded
assert rows[11][4] == "2026-11-02T07:30:00+00:00"
assert rows[3][:4] == ("jobs", "rule: Job alerts", 0, 0) and rows[4][:3] == ("purchase", "model", 1), rows
assert pushes and all(p["click"] == "http://192.168.8.20:8088/" for p in pushes), pushes
assert db.execute("SELECT COUNT(*) FROM pushes").fetchone()[0] == len(pushes)
assert db.execute("SELECT ok FROM runs").fetchall() == [(1,)]
print("triage -> db OK")

# 2) page data: everything present, nothing turned into HTML except the escaped brief
agentdb.record_brief("2026-11-02", f"# Morning brief\n\n## Summary\n- **Busy** day {EVIL}\n- `code`\n\n_Built 07:55._")
agentdb.refresh()
data = json.loads((data_dir / "web" / "data.json").read_text())
assert {e["id"] for e in data["emails"]} >= {"email-5-11", "email-5-12", "email-5-13", "email-5-3"}
subj = next(e for e in data["emails"] if e["id"] == "email-5-11")["subject"]
assert subj == "Q4 plan " + EVIL  # raw text: the page inserts it with textContent
brief_html = data["briefs"][0]["html"]
assert "<img" not in brief_html and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in brief_html, brief_html
assert "<strong>Busy</strong>" in brief_html and "<h2>Morning brief</h2>" in brief_html
assert "<code>code</code>" in brief_html and "<em>Built 07:55.</em>" in brief_html
assert data["health"]["fails"] == 0 and data["health"]["last_ok"]
# full text: one file per email, fetched by the page; none for mail imported from the old queue
by_id = {e["id"]: e for e in data["emails"]}
assert by_id["email-5-11"]["has_body"] and not by_id["email-5-3"]["has_body"]
mail_dir = data_dir / "web" / "mail"
assert (mail_dir / "email-5-11.txt").read_text() == "Please review."
assert not (mail_dir / "email-5-3.txt").exists()
(mail_dir / "email-9-9.txt").write_text("stale")
agentdb.refresh()
assert not (mail_dir / "email-9-9.txt").exists()          # files for emails no longer shown are deleted
import email as _email, email.policy as _policy
m = _email.message.EmailMessage()
m.set_content("plain fallback")
m.add_alternative("<html><head><style>x{}</style></head><body><p>Hi&nbsp;Ana,</p><p>Line&amp;two<br>three</p>"
                  "<script>bad()</script></body></html>", subtype="html")
mm = _email.message_from_bytes(m.as_bytes(), policy=_policy.default)
assert triage.readable_text(mm) == "plain fallback"
h = _email.message.EmailMessage(); h.set_content("<p>Hi&nbsp;Ana,</p><p>Line&amp;two<br>three</p><script>bad()</script>", subtype="html")
assert triage.readable_text(_email.message_from_bytes(h.as_bytes(), policy=_policy.default)) == "Hi Ana,\nLine&two\nthree", \
    repr(triage.readable_text(_email.message_from_bytes(h.as_bytes(), policy=_policy.default)))
print("page data OK")

# 3) Grafana stats: counts only, no subject, sender or summary anywhere in the file
stats_path = data_dir / "stats" / "agent-stats.db"
raw = stats_path.read_bytes()
for secret in (b"Q4 plan", b"Dinner", b"ana@example.org", b"Boss", b"model</b>"):
    assert secret not in raw, secret
st = sqlite3.connect(stats_path)
daily = st.execute("SELECT day, category, sorted_by, emails, pushed, quiet FROM email_daily "
                   "WHERE category IN ('ads', 'personal', 'work') ORDER BY category").fetchall()
assert daily == [("2026-11-02", "ads", "model", 1, 0, 1), ("2026-11-02", "personal", "model", 1, 0, 0),
                 ("2026-11-02", "work", "rule", 1, 1, 0)], daily
assert st.execute("SELECT time FROM email_daily LIMIT 1").fetchone()[0] == 1793577600  # 2026-11-02 00:00 UTC
assert st.execute("SELECT fails FROM health").fetchone()[0] == 0
assert st.execute("SELECT SUM(ok_runs), SUM(failed_runs) FROM triage_daily").fetchone() == (1, 0)
print("stats OK")

# 4) a failing run shows up in the page health and the stats
def broken():
    raise ConnectionRefusedError("bridge down")
triage.connect = broken
try:
    triage.main([])
except ConnectionRefusedError:
    pass
data = json.loads((data_dir / "web" / "data.json").read_text())
assert data["health"]["fails"] == 1 and "bridge down" in data["health"]["last_error"], data["health"]
assert sqlite3.connect(stats_path).execute("SELECT fails FROM health").fetchone()[0] == 1
print("failure health OK")

# 5) re-running on the same mail doesn't duplicate rows; no AGENT_DB = nothing written
triage.connect = lambda: FakeIMAP(MESSAGES)
(home / "state.json").unlink()
triage.main([])
assert sqlite3.connect(os.environ["AGENT_DB"]).execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 5
agentdb.DB_PATH = ""
agentdb.record_push("x", "t", "m", "default", "")
agentdb.refresh()  # silently does nothing
print("idempotent + disabled OK")
# indented bullets (the brief's time-tracking tags) become a sub-list; HTML in text stays escaped
h = agentdb.md_to_html("## Time tracked\n- **Exercise** 2h\n  - Gym 1h <b>x</b>\n- Reading 15m\nend")
assert h == ("<h3>Time tracked</h3>\n<ul>\n<li><strong>Exercise</strong> 2h\n<ul>\n"
             "<li>Gym 1h &lt;b&gt;x&lt;/b&gt;</li></ul></li>\n<li>Reading 15m</li></ul>\n<p>end</p>"), h
print("ALL AGENTDB TESTS PASSED")
