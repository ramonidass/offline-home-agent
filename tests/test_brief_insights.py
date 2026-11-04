"""Time tracking, task completion, clashes and the local-model summary (with a fake Ollama)."""
import json, os, sys, tempfile, threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

d = Path(tempfile.mkdtemp())
for sub in ("cal", "time", "extra"):
    (d / sub).mkdir()

replies = []          # what the fake model will answer, one per call
seen_prompts = []


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen_prompts.append(req)
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": json.dumps({"bullets": replies.pop(0)})}}).encode())


srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ.update(CAL_DIR=str(d / "cal"), EXTRA_ICS_DIR=str(d / "extra"), TIME_DIR=str(d / "time"),
                  TODO_FILES=str(d / "To-do.md"), EMAIL_DIGEST=str(d / "none.jsonl"),
                  OLLAMA_URL=f"http://127.0.0.1:{srv.server_address[1]}/api/chat",
                  TIME_ACTIVITY_MAP="Calisthenics=Exercise:Calisthenics; Sport/Gym=Exercise",
                  TIME_TAG_MAP="Sport/Gym=Gym")
sys.path.insert(0, str(Path(__file__).parent))
import brief  # noqa

TZ = brief.TZ
now = datetime(2026, 10, 28, 7, 55, tzinfo=TZ)  # Wednesday

(d / "cal" / "cal.ics").write_text("""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART;TZID=Europe/London:20261028T100000
DTEND;TZID=Europe/London:20261028T110000
SUMMARY:Team call
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=Europe/London:20261028T103000
DTEND;TZID=Europe/London:20261028T113000
SUMMARY:Dentist
END:VEVENT
END:VCALENDAR
""")

# Exactly what SimpleTimeTracker writes: CRLF rows, quoted categories, mixed time formats.
csv_text = (
    "activity name,time started,time ended,comment,categories,record tags,duration,duration minutes\r\n"
    'Sport/Gym,2026-10-26 18:00:00,2026-10-26 19:00:00,,"Gym, Calisthenics",PullUps&PushUps (105),1:0:0,60\r\n'
    'Exercise,2026-10-27 20:00:00,2026-10-27 20:20:00,,,Sport/Gym,0:20:0,20\r\n'      # old tag name -> Gym
    'Sport/Gym,2026-10-19 18:00:00,2026-10-19 20:00:00,"legs, back",Gym,,2:0:0,120\r\n'
    'Sport/Gym,2026-10-20 18:00:00,2026-10-20 19:10:00,,Gym,,1:10:0,70\r\n'
    "Kafka course,2026-10-27T18:00:00Z,2026-10-27T21:00:00Z,,Study,Linux,3:0:0,180\r\n"
    "Kafka course,2026-10-20T19:00:00+01:00,2026-10-20T20:00:00+01:00,,Study,,1:0:0,60\r\n"
    "Kafka course,2026-10-21 09:00:00,2026-10-21 10:00:00,,Study,,1:0:0,60\r\n"   # later in last week: counted
    "Calisthenics,2026-10-23 18:00:00,2026-10-23 18:45:00,,Calisthenics,,0:45:0,45\r\n"  # last Friday: counted
    "Calisthenics,2026-10-27 07:00:00,2026-10-27 07:30:00,,Calisthenics,PullUps&PushUps (50),0:30:0,30\r\n"
)
(d / "time" / "export.csv").write_text(csv_text, encoding="utf-8")

(d / "To-do.md").write_text(
    "- [x] A ✅ 2026-10-27\n- [x] B ✅ 2026-10-20\n- [x] C ✅ 2026-10-26\n- [ ] Open thing\n", encoding="utf-8")

# 1) facts are right
replies.append([
    "Team call and Dentist overlap today at 10:00, so move one of them.",
    "Exercise so far: 1h 50m this week, 3h 55m last week.",
    "You exercised 5 hours less than usual.",          # invented number -> dropped
    "Kafka study is up strongly this week.",
])
text, c = brief.build(now, consume_email=False)
facts = brief.build.facts_text
print(facts, "\n----\n", text)
assert "Clash: Team call and Dentist overlap on Wed 28 Oct." in facts
# old activities folded into Exercise; the whole of last week counts (Friday's 45m too)
assert "Exercise: 1h 50m this week, 3h 55m last week." in facts, facts
assert "Calisthenics:" not in facts and "Sport/Gym" not in facts and "(category)" not in facts
assert "Kafka course: 3h this week, 2h last week ▲. Tags this period: Linux 3h." in facts  # UTC and +01:00 rows parsed
assert "Tasks completed: 2 in the last 7 days vs 1 in the 7 days before." in facts
assert "this week so far (Mon–Wed) vs all of last week" in facts

# 2) summary sits under the title, invented figure dropped and reported
lines = text.splitlines()
assert lines[2] == "## Summary" and lines[3].startswith("- **2 events today, "), lines[:5]
assert "overlap today at 10:00" in text and "Exercise so far" in text and "Kafka study is up" in text
assert "5 hours" not in text and "1 summary line(s) from the model were dropped" in text
tt = text.split("## Time tracked")[1].split("\n## ")[0]
assert "_This week so far (Mon–Wed) vs all of last week_" in tt, tt
assert "- **Kafka course** 3h · last week 2h ▲\n  - Linux 3h\n" in tt, tt
assert "- **Exercise** 1h 50m · last week 3h 55m\n  - PullUps&PushUps 1h 30m (155), Calisthenics 30m, Gym 20m" in tt, tt
assert tt.index("Kafka") < tt.index("Exercise")  # most time this week first
assert seen_prompts[-1]["think"] is False and "FACTS:" in seen_prompts[-1]["messages"][1]["content"]

# 3) Monday compares full weeks
replies.append(["Quiet week ahead."])
text_mon, _ = brief.build(datetime(2026, 11, 2, 7, 55, tzinfo=TZ), consume_email=False)
assert "last week vs the week before" in brief.build.facts_text
assert "Exercise: 1h 50m last week, 3h 55m the week before ▼ -53%" in brief.build.facts_text

# 4) model down -> brief still complete, with a warning
brief.OLLAMA_URL = "http://127.0.0.1:9/api/chat"
text_down, c_down = brief.build(now, consume_email=False)
assert text_down.splitlines()[4] == "" and "Summary not written" in text_down and "## Today" in text_down
assert brief.hm(0) == "0m" and brief.hm(15) == "15m" and brief.hm(120) == "2h" and brief.hm(150) == "2h 30m"
assert brief.parse_tags("Bible, PullUps (1.5), Push (20)") == [("Bible", None), ("PullUps", 1.5), ("Push", 20.0)]
print("INSIGHTS OK")
