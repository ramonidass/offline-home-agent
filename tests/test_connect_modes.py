"""connect() against a tiny real IMAP server: implicit TLS (hydroxide) and plain."""
import os, socket, ssl, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
os.environ["BRIDGE_PASSWORD"] = "pw"; os.environ["BRIDGE_USER"] = "me@proton.me"
import triage

logins = []
TMP = tempfile.mkdtemp()
CERT, KEY = os.path.join(TMP, "c.pem"), os.path.join(TMP, "k.pem")
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=protonmail-bridge",
                "-keyout", KEY, "-out", CERT], check=True, capture_output=True)

def serve(sock, use_tls):
    conn, _ = sock.accept()
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(CERT, KEY)
        conn = ctx.wrap_socket(conn, server_side=True)
    f = conn.makefile("rwb", buffering=0)
    f.write(b"* OK IMAP4rev1 ready\r\n")
    while True:
        line = f.readline()
        if not line: break
        tag, cmd, *rest = line.decode().strip().split(" ", 2)
        if cmd.upper() == "CAPABILITY":
            f.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + f"{tag} OK done\r\n".encode())
        elif cmd.upper() == "LOGIN":
            logins.append((use_tls, rest[0])); f.write(f"{tag} OK logged in\r\n".encode())
        elif cmd.upper() == "LOGOUT":
            f.write(b"* BYE\r\n" + f"{tag} OK bye\r\n".encode()); break
    conn.close()

for mode, tls in (("ssl", True), ("none", False)):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    threading.Thread(target=serve, args=(s, tls), daemon=True).start()
    triage.IMAP_HOST, triage.IMAP_PORT, triage.IMAP_SECURITY = "127.0.0.1", s.getsockname()[1], mode
    triage.IMAP_USER = "me@proton.me"
    imap = triage.connect(); imap.logout()
print(logins)
assert logins[0][0] is True and logins[1][0] is False and "me@proton.me" in logins[0][1]
print("CONNECT MODES OK")
