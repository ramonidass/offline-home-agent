import os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
got = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        got.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()
s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start()
os.environ["NTFY_URL"] = f"http://127.0.0.1:{s.server_address[1]}/brief"; os.environ["NTFY_TOKEN"] = "tk_x"
sys.path.insert(0, str(Path(__file__).parent))
import brief
brief.notify("# Morning brief, Thursday 29 October\n\n## Summary\n" + "é" * 3000, {})
h, body = got[0]
assert h["Title"] == "Morning brief, Thursday 29 October" and h["Authorization"] == "Bearer tk_x"
assert body.decode().startswith("## Summary\n")
assert brief.headline({"today": 0, "due_today": 1, "overdue": 0, "warnings": 1, "email": 3}) == "0 events today, 1 task due, 3 emails, check warnings"
assert brief.headline({"today": 1, "due_today": 1, "overdue": 1, "warnings": 0, "email": 0}) == "1 event today, 2 tasks due"
body.decode("utf-8"); assert len(body) <= 4000
print("NTFY OK")

# With the page link set, the push is one short line and opens the brief on the page
brief.agentdb.PUSH_LINK = "http://10.0.0.5:8088/"
got.clear()
brief.notify("# Morning brief, Tuesday 03 November\n\n## Summary\n- lots", {"today": 1, "due_today": 0, "overdue": 0, "warnings": 0, "email": 2})
h, body = got[0]
assert h["Click"] == "http://10.0.0.5:8088/#brief" and "Markdown" not in h, h
assert h["Actions"] == "view, Open brief, http://10.0.0.5:8088/#brief"
assert body.decode() == "1 event today, 0 tasks due, 2 emails\nTap to read the brief.", body
brief.BRIEF_PUSH = "full"
got.clear()
brief.notify("# Morning brief, Tuesday 03 November\n\n## Summary\n- lots", {})
assert got[0][1].decode().startswith("## Summary") and got[0][0]["Markdown"] == "yes"
print("NTFY LINK OK")
