import os, sys, tempfile, json
from pathlib import Path
d = Path(tempfile.mkdtemp())
os.environ.update(CAL_DIR=str(d), TODO_FILES=str(d/"t.md"), EMAIL_DIGEST=str(d/"dg.jsonl"))
(d/"dg.jsonl").write_text(json.dumps({"category":"work","from_name":"X","subject":"S","summary":"secret"})+"\n")
(d/"t.md").write_text("- [ ] a\n")
sys.path.insert(0, str(Path(__file__).parent))
import brief
from datetime import datetime
t, c = brief.build(datetime.now(brief.TZ), consume_email=True)
assert "secret" not in t and "Email" not in t and (d/"dg.jsonl").read_text() != ""
print("DEFAULT NO-EMAIL OK")

# EMAIL_CONSUME=no: the brief lists waiting mail (no quiet items, urgent first) and leaves the queue
import json
(d/"dg.jsonl").write_text("\n".join(json.dumps(x) for x in [
    {"category": "jobs", "from_name": "Adzuna", "subject": "Jobs", "summary": ""},
    {"category": "ads", "from_name": "Shop", "subject": "Sale", "summary": "", "quiet": True},
    {"category": "urgent", "from_name": "Bank", "subject": "New sign-in", "summary": ""}]) + "\n")
brief.EMAIL_IN_BRIEF, brief.EMAIL_CONSUME = True, False
t2, _ = brief.build(datetime.now(brief.TZ), consume_email=True)
assert "### Waiting for the digest (2)" in t2 and "- Email: 2 waiting for the digest (1 urgent, 1 jobs)." in t2 and t2.index("Bank") < t2.index("Adzuna") and "Shop" not in t2, t2
assert (d/"dg.jsonl").read_text().count("\n") == 3
print("email peek OK")

# pushed alerts from the last 24 h are listed; older ones and an empty queue give "nothing new"
import time
(d/"pushed.jsonl").write_text(json.dumps({"category": "work", "from_name": "Mercor", "subject": "Offer", "summary": "", "at": time.time() - 3600}) + "\n"
                              + json.dumps({"category": "urgent", "from_name": "Old", "subject": "x", "summary": "", "at": time.time() - 3 * 86400}) + "\n")
t3, _ = brief.build(datetime.now(brief.TZ), consume_email=True)
assert "### Pushed in the last 24 hours (1)" in t3 and "Email: 1 pushed to your phone in the last 24 hours (1 work)" in t3 and "Mercor" in t3 and "Old" not in t3, t3
(d/"dg.jsonl").write_text(""); (d/"pushed.jsonl").unlink()
t4, _ = brief.build(datetime.now(brief.TZ), consume_email=True)
assert "## Email\n- Nothing new that needs your attention." in t4, t4
assert t4.splitlines()[2:5] == ["## Summary", "- **0 events today, 0 tasks due, check warnings**", "- Email: nothing new that needs your attention."], t4
print("email section OK")
