# Obsidian tasks: priority icons, sort order, the "## Section" each task is under, stray date emoji.
import os, sys, tempfile
from pathlib import Path
d = Path(tempfile.mkdtemp())
(d / "To-do.md").write_text("""# To-do
## Inbox
- [ ] Sell the old keyboard  🔼  📆
## Computer science
- [ ] Kubernetes course
- [ ] Low thing 🔽
- [ ] Compare two backup tools ⏫

## Done
- [x] Fg    📆 ✅ 2026-11-02
""", encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import brief
tasks, missing = brief.parse_tasks([d / "To-do.md"])
by = {t["text"]: t for t in tasks}
assert set(by) == {"Sell the old keyboard", "Kubernetes course", "Low thing", "Compare two backup tools"}, by
assert by["Sell the old keyboard"]["section"] == "Inbox" and by["Sell the old keyboard"]["prio"] == 2
assert by["Compare two backup tools"]["prio"] == 1 and by["Kubernetes course"]["prio"] == 3
assert by["Low thing"]["prio"] == 4 and by["Kubernetes course"]["section"] == "Computer science"
print("TASKS OK")
