# offline-home-agent

A small assistant that runs on a mini PC at home and has no internet access. It sorts my Proton
Mail, pushes the emails that matter to my phone, and writes a morning brief from my calendar,
Obsidian to-do list and time tracking. The only thing allowed out of the house is the mail bridge,
and only to Proton's servers.

I built it because I wanted the useful parts of an "AI inbox" without handing my email to a
cloud service. Everything here, the language model included, runs locally.

## What it does

- **Email triage, every 10 minutes.** New mail is sorted into urgent, work, personal, purchase,
  jobs, info, ads or unsure. My own rules (`rules.yaml`) decide first; a local model (qwen3.5 9B
  via Ollama) handles the rest. Each email gets a Proton label, and the important ones become a
  push notification on my phone.
- **Digest at 08:00 and 18:00** with everything that didn't need a push, including a list of what
  the model filed away quietly, so a wrong guess gets caught the same day.
- **Morning brief at 07:55.** Today's events and the next week, tasks by priority (Obsidian Tasks
  format plus calendar tasks), a month preview, this week's tracked time against last week, and the
  email waiting for me. The model writes a short summary at the top from facts the code has already
  checked, so it can't invent numbers.
- **A small web page** for reading all of this on the phone, because a long notification is hard
  to read. Tapping a push opens it. It's static files plus one JSON file, served on the LAN.
- **Health alerts.** If triage fails three times in a row (bridge down, password changed), I get a
  push saying so, and another when it recovers. Silence never means "no mail" by accident.
- **Grafana stats** (optional): counts only, never subjects or addresses.
- **Night sleep** (optional): the box suspends at 00:30 and wakes itself at 07:30.

## How it's put together

Everything is a rootless Podman container managed by systemd (Quadlet files), on a Fedora mini PC
(Ryzen 7 8845HS, 32 GB). The model runs on the CPU.

```
                    internet
                       │
                 (Proton only)
                       │
              ┌────────┴────────┐
              │  egress-proxy   │  Squid, allowlist: Proton domains
              │  egress network │
              └────────┬────────┘
   ────────────────────┼──────────── agent network (internal, no route out)
        │              │              │             │            │
   hydroxide      email-triage    morning-brief   ollama       ntfy
  (IMAP bridge)   email-digest    (one-shot)      (model)    (pushes)
                  (one-shot jobs)                               │
                                                              caddy ── ports 8090 / 8088
                                                          (socket-activated, no internet)
                                                                │
                                                     phone, over WireGuard only
```

A few decisions that shaped it:

- **Two networks.** `agent` is internal: containers on it can talk to each other and nothing else.
  Only the proxy also sits on `egress`, and Squid refuses anything that isn't Proton. The mail
  bridge reaches Proton through it; nothing else has a way out.
- **No published ports.** Caddy is started by a systemd socket (`caddy.socket`), so the ports are
  opened on the host by systemd and handed in. Caddy itself has no internet either.
- **The jobs are one-shot containers** with a read-only filesystem, no capabilities, and the
  user's own UID. They start on a timer, do their work and exit.
- **Email text is untrusted.** The model only classifies and summarises it; it never gets tools.
  The page shows email text with `textContent`, never as HTML, and its Content-Security-Policy only
  allows its own files.
- **Everything is pinned.** Image tags and Python packages are fixed versions, so nothing changes
  until I update it on purpose.
- **Remote access is WireGuard only**, on my router. SSH accepts keys only, and only from WireGuard
  addresses; `security-check.sh` verifies the firewall and SSH settings.

## Repository layout

| Path | What it is |
|---|---|
| `agent-jobs/` | The Python jobs: `triage.py` (sorting, pushes, digest), `brief.py` (morning brief), `agentdb.py` (SQLite record, page data, stats) and their container |
| `quadlets/` | Podman Quadlet units: networks, containers, the job image build |
| `timers/` | systemd user timers for triage, digest and brief |
| `caddy/` | `Caddyfile` and `caddy.socket` |
| `egress-proxy/` | Squid config with the Proton allowlist |
| `hydroxide/`, `bridge/` | Two ways to get IMAP from Proton: [hydroxide](https://codeberg.org/emersion/hydroxide) (free) or the official Bridge (needs a paid plan) |
| `ntfy/` | ntfy server config |
| `site/` | The web page (plain HTML, CSS and JavaScript, no build step, no dependencies) |
| `state-email/rules.yaml` | Example sorting rules and category hints for the model |
| `grafana/` | Dashboard for the counts-only stats |
| `power/` | Night suspend and morning wake timers |
| `tests/` | Plain Python test scripts |
| `docs/` | Setup guide and remote-access notes |

## Running it

The full walkthrough is in [docs/setup.md](docs/setup.md). It takes an afternoon, plus a week or two
of adding rules until the sorting matches how you think about your mail. You need:

- Linux with Podman 5.2+ and systemd (I use Fedora); 16 GB RAM is the practical minimum for the 9B model
- A Proton Mail account
- ntfy on your phone, and WireGuard (or similar) if you want pushes away from home
- Optional: Fossify Calendar and SimpleTimeTracker backups synced with Syncthing, and an Obsidian vault

## Tests

No framework; each file is a script that exits non-zero on failure. From the repo root:

```bash
pip install -r agent-jobs/requirements.txt
for f in tests/test_*.py; do PYTHONPATH=agent-jobs python3 "$f" > /dev/null && echo "ok  $f" || echo "FAIL $f"; done
```

They use fake IMAP, ntfy and Ollama servers, so they need no accounts or network.

## Limits and trade-offs

- A 9B model on a CPU is slow (a classification takes a few seconds) and sometimes wrong. That's
  why rules come first and the digest lists what the model filed quietly.
- hydroxide is unofficial. It has been reliable for me, but the official Bridge is the fallback.
- The page uses plain HTTP. It's only reachable over WireGuard, which already encrypts the traffic.
- It's built for one person and one mailbox. There's no multi-user support and no plan for it.

## How it was built

I built this with Claude Code over a couple of weeks, one piece at a time: first triage, then the brief,
then the isolation and the page, then hardening remote access. I made the design calls and tested
every step on the real machine; most of the code was written in conversation with the assistant.

## License

MIT. See [LICENSE](LICENSE).
