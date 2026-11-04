import json, os, sys, tempfile, time
from datetime import date, datetime, timedelta
from pathlib import Path

d = Path(tempfile.mkdtemp())
(d / "cal").mkdir()
os.environ.update(CAL_DIR=str(d / "cal"), TODO_FILES=str(d / "To-do.md"),
                  EMAIL_DIGEST=str(d / "digest.jsonl"), BRIEF_FILE=str(d / "out/brief.md"),
                  EXTRA_ICS_DIR=str(d / "extra"))
(d / "extra").mkdir()
sys.path.insert(0, str(Path(__file__).parent))
os.environ["EMAIL_IN_BRIEF"] = "yes"
import brief  # noqa

TZ = brief.TZ
now = datetime(2026, 10, 28, 7, 55, tzinfo=TZ)   # a Wednesday
D = lambda n: (now.date() + timedelta(days=n)).strftime("%Y%m%d")

ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Fossify//Calendar//EN
BEGIN:VEVENT
UID:a1
DTSTART;TZID=Europe/London:{D(0)}T093000
DTEND;TZID=Europe/London:{D(0)}T100000
SUMMARY:Barber
LOCATION:High St
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART;VALUE=DATE:{D(1)}
DTEND;VALUE=DATE:{D(2)}
SUMMARY:Day off
END:VEVENT
BEGIN:VEVENT
UID:weekly
DTSTART;TZID=Europe/London:{D(-6)}T190000
DTEND;TZID=Europe/London:{D(-6)}T200000
RRULE:FREQ=WEEKLY
SUMMARY:Gym class
END:VEVENT
BEGIN:VEVENT
UID:weekly
RECURRENCE-ID;TZID=Europe/London:{D(1)}T190000
DTSTART;TZID=Europe/London:{D(1)}T200000
DTEND;TZID=Europe/London:{D(1)}T210000
SUMMARY:Gym class (moved)
END:VEVENT
BEGIN:VEVENT
UID:bday
DTSTART;VALUE=DATE:20000101
RRULE:FREQ=YEARLY;BYMONTH={int(D(3)[4:6])};BYMONTHDAY={int(D(3)[6:])}
SUMMARY:Mum's birthday
END:VEVENT
BEGIN:VEVENT
UID:broken
DTSTART:{D(0)}T120000Z
RRULE:FREQ=DAILY;UNTIL=20200101T000000Z
SUMMARY:Broken repeat
END:VEVENT
BEGIN:VEVENT
UID:dup
DTSTART:{D(2)}T080000
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
UID:dup
DTSTART:{D(5)}T150000
SUMMARY:Flight to Seville
END:VEVENT
BEGIN:VEVENT
UID:far
DTSTART:{D(30)}T090000
SUMMARY:Too far ahead
END:VEVENT
BEGIN:VEVENT
UID:work
DTSTART;TZID=Europe/London:20261102T090000
DTEND;TZID=Europe/London:20261102T170000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T000000Z
SUMMARY:Work
END:VEVENT
BEGIN:VEVENT
UID:workshop
DTSTART:20261110T100000
SUMMARY:Workshop on pottery
END:VEVENT
BEGIN:VEVENT
UID:rep
DTSTART:20261110T080000
RRULE:FREQ=WEEKLY;COUNT=4
SUMMARY:Physio appointment
END:VEVENT
BEGIN:VEVENT
UID:dentist-later
DTSTART:20261120T100000
SUMMARY:Dentist checkup
END:VEVENT
BEGIN:VEVENT
UID:coffee-later
DTSTART:20261120T110000
SUMMARY:Coffee with Ana
END:VEVENT
BEGIN:VTODO
UID:task-today
DTSTART;TZID=Europe/London:{D(0)}T170000
SUMMARY:Pick up parcel
END:VTODO
BEGIN:VTODO
UID:task-done
DTSTART;VALUE=DATE:{D(0)}
STATUS:COMPLETED
SUMMARY:Already done task
END:VTODO
BEGIN:VTODO
UID:task-repeat
DTSTART;VALUE=DATE:{D(-20)}
RRULE:FREQ=WEEKLY
SUMMARY:Water plants
END:VTODO
BEGIN:VTODO
UID:task-late
DTSTART;VALUE=DATE:{D(-3)}
SUMMARY:Return library book
END:VTODO
BEGIN:VTODO
UID:task-ancient
DTSTART;VALUE=DATE:{D(-60)}
SUMMARY:Ancient task
END:VTODO
END:VCALENDAR
"""
old = d / "cal" / "Events_old.ics"
old.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
os.utime(old, (time.time() - 100000, time.time() - 100000))
(d / "cal" / "Events_new.ics").write_text(ics)

(d / "To-do.md").write_text(f"""# To-do
- [ ] Renew passport 📅 {(now.date() - timedelta(days=2)).isoformat()} ⏫
- [ ] Pay council tax [due:: {now.date().isoformat()}]
- [x] Done thing 📅 {now.date().isoformat()}
- [-] Cancelled thing
- [ ] Book Seville flights 📅 {(now.date() + timedelta(days=4)).isoformat()} 🔁 every month
* [ ] Read Kafka chapter 3
- [ ] Call landlord 🔺
    - [ ] Ask about boiler
Some text - [ ] not a task
- [ ] Renew railcard 📅 2026-11-18
- [ ] Far task 📅 {(now.date() + timedelta(days=40)).isoformat()}
""", encoding="utf-8")

(d / "digest.jsonl").write_text(json.dumps({"category": "purchase", "from_name": "Shop",
                                            "subject": "Shipped", "summary": "Your parcel shipped."}) + "\n")

(d / "extra" / "bank-holidays.ics").write_text("""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:bh1
DTSTART;VALUE=DATE:20261225
DTEND;VALUE=DATE:20261226
SUMMARY:Christmas Day
END:VEVENT
BEGIN:VEVENT
UID:bh2
DTSTART;VALUE=DATE:20261102
SUMMARY:Test bank holiday
END:VEVENT
END:VCALENDAR
""")
text, c = brief.build(now, consume_email=False)
print(text)
print(c)
nov = text.split("## November at a glance")[1].split("##")[0]
assert "work days" not in nov and "Mon 09  Work (x7)" in nov, nov  # Mon 02 and Wed 04 are already in the next 7 days
assert "Fri 20  Coffee with Ana" in nov and "Tue 10  Workshop on pottery" in nov  # every event, not only work
assert "Dentist checkup" not in nov and "Physio" not in nov and "Test bank holiday" not in nov  # already above
assert "Task due Wed 18: Renew railcard" in nov, nov
later = text.split("## Later")[1].split("##")[0]
assert "Dentist checkup" in later and "Coffee" not in later and "Workshop" not in later and "Work" not in later, later
assert later.count("Physio appointment") == 1
assert "Mon 02 Nov" in text and "Test bank holiday" in text  # extra calendar read
assert "Christmas" not in text  # beyond 45 days
early = brief.build(datetime(2026, 10, 5, 7, 55, tzinfo=TZ), consume_email=False)[0]
assert "at a glance" not in early

assert "09:30–10:00  Barber (High St)" in text
assert "Tomorrow All day  Day off" in text
assert "Tomorrow 20:00–21:00  Gym class (moved)" in text          # override applied
assert text.split("## November")[0].count("Gym class") == 1                                 # only the moved one falls in 7 days
assert "Mum's birthday" in text
assert "Fri 30 Oct 08:00  Dentist" in text                           # duplicate UID kept, no 08:00-08:00
assert "Flight to Seville" in text
assert "Too far ahead" not in text.split("## November")[0] and "Fri 27  Too far ahead" in nov
assert "Thu 05  Gym class (x4)" in nov  # weekly repeat shown once
assert "12:00  Broken repeat" in text.split("## Next")[0]
assert "Renew passport (was due" in text and "⏫ Renew passport" in text
assert "Pay council tax" in text.split("### Due today")[1]
assert "Done thing" not in text and "Cancelled thing" not in text
assert "Book Seville flights" in text and "🔁" not in text and "📅" not in text
assert "Ask about boiler" in text and "not a task" not in text
assert "Far task" in text.split("### No date")[0] is False or "Far task" not in text.split("### Due in")[1].split("### No date")[0]
assert "🔺 Call landlord" in text.split("### No date")[1].splitlines()[1]  # highest priority first
assert "showing" not in text  # all undated tasks fit
assert "Your parcel shipped." in text
tasks_part = text.split("## Tasks")[1].split("## Email")[0]
assert "17:00 Pick up parcel" in tasks_part.split("### Due today")[1].split("###")[0], tasks_part  # calendar task today
assert "Already done task" not in text and "Ancient task" not in text
assert "Return library book (was due" in tasks_part.split("### Overdue")[1].split("###")[0]
assert tasks_part.count("Water plants") == 1 and "Water plants" in tasks_part.split("### Due in")[1]  # no past repeats
assert "Pick up parcel" not in text.split("## Tasks")[0]  # tasks are not listed as events
assert c["warnings"] == 0, c
assert "Events_new.ics" in text

# stale + missing todo + consume email
os.utime(d / "cal" / "Events_new.ics", (time.time() - 3 * 86400, time.time() - 3 * 86400))
os.utime(old, (time.time() - 4 * 86400, time.time() - 4 * 86400))
brief.TODO_FILES = [d / "missing.md"]
text2, c2 = brief.build(datetime.now(TZ), consume_email=True)
print(text2)
assert "days old" in text2 and "To-do file not found" in text2 and c2["warnings"] == 2
assert (d / "digest.jsonl").read_text() == ""

# unreadable calendar -> warning, brief still produced
(d / "cal" / "Events_new.ics").write_text("garbage")
os.utime(d / "cal" / "Events_new.ics", None)
text3, c3 = brief.build(datetime.now(TZ), consume_email=False)
assert "could not be read" in text3 and "## Tasks" in text3
print("BRIEF OK")
# repeats collapse + work excluded from Later
os.utime(d / "cal" / "Events_new.ics", None)
