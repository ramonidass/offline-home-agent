// The agent page: reads data.json (rebuilt by the agent after every run) and draws it.
// Every string from email or the model goes in with textContent, never as HTML. The one
// exception is the brief, which the server already turned into escaped HTML.
"use strict";

const REFRESH_MS = 2 * 60 * 1000;
const SEEN_KEY = "agent.lastSeen";
const VIEWS = ["brief", "emails", "pushes"];

const FILTERS = [
  { id: "all", label: "All", test: () => true },
  { id: "important", label: "Important", test: (e) => e.category === "urgent" || e.category === "work" },
  { id: "personal", label: "Personal", test: (e) => e.category === "personal" },
  { id: "pushed", label: "Pushed", test: (e) => e.pushed },
  { id: "purchase", label: "Purchases", test: (e) => e.category === "purchase" },
  { id: "jobs", label: "Jobs", test: (e) => e.category === "jobs" },
  { id: "unsure", label: "Unsure", test: (e) => e.category === "unsure" },
  { id: "quiet", label: "Filed quietly", test: (e) => e.quiet },
  { id: "other", label: "Info & ads", test: (e) => e.category === "info" || e.category === "ads" },
];

// Email groups, most important first. Info and ads start folded.
const CATEGORIES = [
  ["urgent", "Urgent"], ["work", "Work"], ["personal", "Personal"], ["purchase", "Purchases"],
  ["jobs", "Jobs"], ["unsure", "Unsure"], ["info", "Info"], ["ads", "Ads"],
];
const GROUP_KEY = "agent.emailGroup";
const FOLDED_KEY = "agent.foldedGroups";

const state = { data: null, filter: "all", query: "", open: new Set(), seenBefore: null,
  group: "category", folded: new Set(["info", "ads"]) };

// ---------- small helpers ----------

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) {
    return null; // storage can be blocked; the page still works, only "new" marks are lost
  }
  return null;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  svg.setAttribute("aria-hidden", "true");
  return svg;
}

function when(iso) {
  return iso ? new Date(iso) : null;
}

function ago(iso) {
  const d = when(iso);
  if (!d) return "never";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function dayLabel(d) {
  const today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

function clock(d) {
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function isNew(iso) {
  return Boolean(state.seenBefore && iso && iso > state.seenBefore);
}

// Pushes are stored as sent; ntfy Markdown is shown here as plain text.
function plain(md) {
  return (md || "")
    .split("\n")
    .map((l) => l.replace(/^#{1,6}\s+/, "").replace(/^(\s*)[-*]\s+/, "$1• ").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`(.+?)`/g, "$1"))
    .filter((l) => l.trim())
    .join("\n");
}

// ---------- views ----------

function currentView() {
  const v = location.hash.slice(1);
  return VIEWS.includes(v) ? v : "brief";
}

function showView() {
  const view = currentView();
  for (const s of document.querySelectorAll(".view")) s.hidden = s.dataset.view !== view;
  for (const t of document.querySelectorAll(".tab")) {
    const on = t.dataset.view === view;
    t.classList.toggle("active", on);
    if (on) t.setAttribute("aria-current", "page");
    else t.removeAttribute("aria-current");
  }
  window.scrollTo(0, 0);
}

// ---------- sections ----------

function drawHealth(h) {
  const pill = document.getElementById("health");
  pill.title = "";
  if (!h || !h.last_run) {
    pill.className = "pill pill-muted";
    pill.textContent = "Not run yet";
  } else if (h.fails > 0) {
    pill.className = "pill pill-bad";
    pill.textContent = `Triage failing (${h.fails})`;
    pill.title = h.last_error || "";
  } else {
    const stale = Date.now() - when(h.last_ok).getTime() > 2 * 3600 * 1000;
    pill.className = stale ? "pill pill-bad" : "pill pill-ok";
    pill.textContent = `${stale ? "Last OK" : "OK"} · ${ago(h.last_ok)}`;
    pill.title = "Email triage";
  }
}

// Split one brief (escaped HTML from the server) into a card per "##" section.
function briefCards(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html; // escaped server-side (agentdb.md_to_html)
  decorateTasks(tpl.content);
  const out = { title: "", headline: "", cards: [], note: null };
  let card = null;
  for (const node of [...tpl.content.children]) {
    if (node.tagName === "H2" && !out.title) {
      out.title = node.textContent;
    } else if (node.tagName === "H3") {
      card = el("article", "card brief");
      card.append(node);
      out.cards.push(card);
    } else if (node.tagName === "P" && /^Built /.test(node.textContent)) {
      out.note = el("p", "foot-note", node.textContent);
    } else {
      if (!card) {
        card = el("article", "card brief");
        out.cards.push(card);
      }
      card.append(node);
    }
  }
  // The first Summary bullet is the one-line headline: show it big, above the cards.
  const summary = out.cards.find((c) => /^summary$/i.test((c.querySelector("h3") || {}).textContent || ""));
  if (summary) {
    summary.classList.add("wide");
    const first = summary.querySelector("li");
    if (first) {
      out.headline = first.textContent;
      first.remove();
    }
    if (!summary.querySelector("li, p")) out.cards = out.cards.filter((c) => c !== summary);
  }
  // Long sections (Email, the month preview) always take the full width.
  for (const c of out.cards) {
    if (/^(email|.* at a glance)$/i.test((c.querySelector("h3") || {}).textContent || "")) c.classList.add("wide");
  }
  // Two columns on a wide screen: a section left alone on its row takes the full width.
  const half = out.cards.filter((c) => !c.classList.contains("wide"));
  if (half.length % 2 === 1) half[half.length - 1].classList.add("wide");
  return out;
}

// Tasks arrive as "⏫ Text · <em>Section</em>": turn the Obsidian priority icon and the
// section into small labels.
const PRIORITIES = { "🔺": ["Highest", 0], "⏫": ["High", 1], "🔼": ["Medium", 2], "🔽": ["Low", 4], "⏬": ["Lowest", 5] };

const GROUP_LABELS = Object.fromEntries(CATEGORIES.map(([id, label]) => [label, id]));

function decorateTasks(root) {
  // Email groups in the brief: "<strong>Purchases</strong> (3)" with the emails nested under it.
  for (const li of root.querySelectorAll("li")) {
    const head = li.firstElementChild;
    if (head && head.tagName === "STRONG" && li.firstChild === head && li.querySelector(":scope > ul")
        && GROUP_LABELS[head.textContent]) {
      li.classList.add("mail-group", `cat-${GROUP_LABELS[head.textContent]}`);
    }
  }
  for (const li of root.querySelectorAll("li")) {
    const first = li.firstChild;
    if (first && first.nodeType === Node.TEXT_NODE) {
      const icon = Object.keys(PRIORITIES).find((k) => first.data.startsWith(`${k} `));
      if (icon) {
        const [label, level] = PRIORITIES[icon];
        first.data = first.data.slice(icon.length + 1);
        const chip = el("span", `prio prio-${level}`, label);
        chip.title = `${icon} ${label} priority`;
        li.prepend(chip);
        li.classList.add("task");
      }
    }
    const last = li.lastChild;
    const before = last && last.previousSibling;
    if (last && last.nodeName === "EM" && before && before.nodeType === Node.TEXT_NODE && before.data.endsWith(" · ")) {
      before.data = before.data.slice(0, -3);
      li.replaceChild(el("span", "section-tag", last.textContent), last);
      li.classList.add("task");
    }
  }
}

function splitTitle(title) {
  const [name, ...rest] = title.split(", ");
  return { name: name || "Morning brief", date: rest.join(", ") };
}

function drawBrief(briefs) {
  const body = document.getElementById("brief-body");
  const day = document.getElementById("brief-day");
  const title = document.getElementById("brief-title");
  const headline = document.getElementById("brief-headline");
  const older = document.getElementById("brief-older");
  older.replaceChildren();
  document.getElementById("older-title").hidden = briefs.length < 2;
  if (!briefs.length) {
    body.replaceChildren(el("p", "card muted empty", "No brief yet. It is written at 07:55."));
    day.textContent = "";
    headline.textContent = "";
    return;
  }
  const [latest, ...rest] = briefs;
  const b = briefCards(latest.html);
  const t = splitTitle(b.title);
  const todayIso = new Date().toLocaleDateString("en-CA");
  day.textContent = (latest.day === todayIso ? "Today" : dayLabel(new Date(latest.day + "T12:00:00"))) +
    (t.date ? ` · ${t.date}` : "");
  title.textContent = t.name;
  headline.textContent = b.headline;
  body.replaceChildren(...b.cards);
  if (b.note) body.append(b.note);
  if (isNew(latest.built)) b.cards.forEach((c) => c.classList.add("is-new"));

  for (const ob of rest) {
    const det = el("details", "older");
    const parts = briefCards(ob.html);
    det.append(el("summary", null, dayLabel(new Date(ob.day + "T12:00:00")) +
      (parts.headline ? ` — ${parts.headline}` : "")));
    const grid = el("div", "brief-grid");
    grid.append(...parts.cards);
    det.append(grid);
    older.append(det);
  }
}

function drawFilters(emails) {
  const box = document.getElementById("filters");
  box.replaceChildren();
  for (const f of FILTERS) {
    const n = emails.filter(f.test).length;
    if (f.id !== "all" && n === 0 && state.filter !== f.id) continue;
    const chip = el("button", "chip", f.label);
    chip.append(el("span", null, String(n)));
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.filter === f.id));
    chip.addEventListener("click", () => {
      state.filter = f.id;
      drawFilters(emails);
      drawEmails(emails);
    });
    box.append(chip);
  }
}

function matches(e, q) {
  if (!q) return true;
  return [e.from_name, e.from_addr, e.subject, e.summary, e.category]
    .some((s) => (s || "").toLowerCase().includes(q));
}

function toggleRow(row, id, onOpen) {
  const flip = () => {
    if (state.open.has(id)) state.open.delete(id);
    else state.open.add(id);
    row.classList.toggle("open");
    row.setAttribute("aria-expanded", String(row.classList.contains("open")));
    if (onOpen && row.classList.contains("open")) onOpen();
  };
  row.addEventListener("click", (ev) => {
    // Taps inside the email text select it; they don't close the row.
    if (ev.target.closest(".mail-body") || String(window.getSelection())) return;
    flip();
  });
  row.addEventListener("keydown", (ev) => {
    if (ev.target === row && (ev.key === "Enter" || ev.key === " ")) {
      ev.preventDefault();
      flip();
    }
  });
  row.setAttribute("aria-expanded", String(state.open.has(id)));
  if (state.open.has(id)) {
    row.classList.add("open");
    if (onOpen) onOpen();
  }
}

// The full text is a separate small file, fetched the first time you open the email.
const bodies = new Map();

async function loadBody(e, box) {
  if (box.dataset.loaded) return;
  box.dataset.loaded = "1";
  box.textContent = "Loading the email…";
  try {
    if (!bodies.has(e.id)) {
      const res = await fetch(`mail/${encodeURIComponent(e.id)}.txt`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      bodies.set(e.id, await res.text());
    }
    box.textContent = bodies.get(e.id); // plain text only: links and HTML stay inert
  } catch (_) {
    delete box.dataset.loaded;
    box.textContent = "Couldn't load the email text. Tap the email again to retry.";
  }
}

function mailRow(e, withDay) {
  const name = e.from_name || e.from_addr || "(unknown)";
  const row = el("div", `row cat-${e.category}`);
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.id = e.id;
  if (isNew(e.processed)) row.classList.add("is-new");

  row.append(el("span", "avatar", (name.match(/[\p{L}\p{N}]/u) || ["?"])[0].toUpperCase()));
  const main = el("div", "row-main");
  const top = el("div", "row-top");
  top.append(el("span", "row-from", name));
  const d = when(e.received || e.processed);
  top.append(el("span", "row-time", d ? (withDay ? `${dayLabel(d)} ${clock(d)}` : clock(d)) : ""));
  main.append(top);
  main.append(el("div", "row-subject", e.subject || "(no subject)"));
  if (e.summary) main.append(el("div", "row-preview", e.summary));

  const tags = el("div", "row-tags");
  if (withDay !== true || state.group !== "category") tags.append(el("span", "badge", e.category));
  if (e.pushed) tags.append(el("span", "badge plain", "pushed"));
  if (e.quiet) tags.append(el("span", "badge plain", "filed quietly"));
  main.append(tags);

  const meta = [e.from_addr, e.source === "model" ? "sorted by the model" : `sorted by ${e.source}`];
  const more = el("div", "row-more");
  more.append(el("div", null, meta.filter(Boolean).join(" · ")));
  const body = el("div", "mail-body");
  if (e.has_body) more.append(body);
  else more.append(el("div", "mail-none", "Full text isn't stored for this email (sorted before the page existed)."));
  main.append(more);
  row.append(main);
  toggleRow(row, e.id, e.has_body ? () => loadBody(e, body) : null);
  return row;
}

function drawEmails(emails) {
  const list = document.getElementById("email-list");
  const filter = FILTERS.find((f) => f.id === state.filter) || FILTERS[0];
  const q = state.query.trim().toLowerCase();
  const shown = emails.filter((e) => filter.test(e) && matches(e, q));
  document.getElementById("email-count").textContent =
    shown.length === emails.length ? `${emails.length} in the last 30 days` : `${shown.length} of ${emails.length}`;
  list.replaceChildren();
  if (!shown.length) {
    list.append(el("p", "card muted empty",
      emails.length ? "Nothing matches." : "No emails yet. They appear here as triage sorts new mail."));
    return;
  }
  if (state.group === "category") {
    drawByCategory(list, shown, q !== "" || state.filter !== "all");
    return;
  }
  let lastDay = "";
  for (const e of shown) {
    const d = when(e.received || e.processed);
    const label = d ? dayLabel(d) : "";
    if (label !== lastDay) {
      list.append(el("div", "day", label));
      lastDay = label;
    }
    list.append(mailRow(e, false));
  }
}

// One card per category (Urgent, Work, Personal, ...), newest first inside. Tap a header to fold it.
function drawByCategory(list, shown, searching) {
  const known = new Set(CATEGORIES.map(([id]) => id));
  const groups = [...CATEGORIES];
  for (const e of shown) {
    if (!known.has(e.category)) {
      known.add(e.category);
      groups.push([e.category, e.category]);
    }
  }
  for (const [id, label] of groups) {
    const items = shown.filter((e) => e.category === id);
    if (!items.length) continue;
    const box = el("section", `group-card cat-${id}`);
    const folded = state.folded.has(id) && !searching; // a search always shows what it found
    box.classList.toggle("folded", folded);
    const head = el("button", "group-head");
    head.type = "button";
    head.setAttribute("aria-expanded", String(!folded));
    head.append(el("span", "group-dot"));
    head.append(el("span", "group-name", label));
    const fresh = items.filter((e) => isNew(e.processed)).length;
    if (fresh) head.append(el("span", "group-new", `${fresh} new`));
    head.append(el("span", "group-count", String(items.length)));
    head.append(el("span", "group-chev", "›"));
    head.addEventListener("click", () => {
      if (state.folded.has(id)) state.folded.delete(id);
      else state.folded.add(id);
      store(FOLDED_KEY, JSON.stringify([...state.folded]));
      const now = state.folded.has(id);
      box.classList.toggle("folded", now);
      head.setAttribute("aria-expanded", String(!now));
    });
    box.append(head);
    const body = el("div", "group-body");
    for (const e of items) body.append(mailRow(e, true));
    box.append(body);
    list.append(box);
  }
}

function drawGroupToggle() {
  for (const b of document.querySelectorAll("#group-toggle button")) {
    b.setAttribute("aria-pressed", String(b.dataset.group === state.group));
  }
}

function drawPushes(pushes) {
  const list = document.getElementById("push-list");
  list.replaceChildren();
  if (!pushes.length) {
    list.append(el("p", "card muted empty", "No pushes yet."));
    return;
  }
  let lastDay = "";
  pushes.forEach((p, i) => {
    const d = when(p.sent);
    const label = d ? dayLabel(d) : "";
    if (label !== lastDay) {
      list.append(el("div", "day", label));
      lastDay = label;
    }
    const isBrief = p.topic === "brief";
    const row = el("button", `row ${isBrief ? "cat-brief" : `prio-${p.priority || "default"}`}`);
    row.type = "button";
    const av = el("span", "avatar");
    av.append(icon(isBrief ? "brief" : "mail"));
    row.append(av);
    const main = el("div", "row-main");
    const top = el("div", "row-top");
    top.append(el("span", "row-from", p.title || "(no title)"));
    top.append(el("span", "row-time", d ? clock(d) : ""));
    main.append(top);
    if (p.message) main.append(el("div", "row-preview push-msg", plain(p.message)));
    const tags = el("div", "row-tags");
    tags.append(el("span", "badge plain", p.topic || "push"));
    if (p.priority === "high") tags.append(el("span", "badge", "high priority"));
    main.append(tags);
    row.append(main);
    if (isBrief) {
      row.addEventListener("click", () => { location.hash = "#brief"; });
    } else {
      toggleRow(row, `push-${p.sent}-${i}`);
    }
    list.append(row);
  });
}

function drawNew(data) {
  const banner = document.getElementById("new-banner");
  const count = document.querySelector('.tab[data-view="emails"] .count');
  const newMail = state.seenBefore ? data.emails.filter((e) => isNew(e.processed)) : [];
  count.hidden = newMail.length === 0;
  count.textContent = newMail.length > 99 ? "99+" : String(newMail.length);
  if (!state.seenBefore) {
    banner.hidden = true;
    return;
  }
  const important = newMail.filter((e) => e.category === "urgent" || e.category === "work").length;
  const newBrief = data.briefs.length && isNew(data.briefs[0].built);
  const parts = [];
  if (newBrief) parts.push("a new brief");
  if (newMail.length) parts.push(`${newMail.length} new email${newMail.length === 1 ? "" : "s"}` +
    (important ? ` (${important} important)` : ""));
  banner.hidden = parts.length === 0;
  banner.textContent = parts.length ? `Since your last visit: ${parts.join(", ")}.` : "";
}

function draw() {
  const data = state.data;
  if (!data) return;
  const emails = data.emails || [];
  drawHealth(data.health);
  drawNew(data);
  drawBrief(data.briefs || []);
  drawFilters(emails);
  drawEmails(emails);
  drawPushes(data.pushes || []);
  document.getElementById("generated").textContent = `Updated ${ago(data.generated)}`;
}

// ---------- loading ----------

async function load() {
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    draw();
  } catch (err) {
    const pill = document.getElementById("health");
    pill.className = "pill pill-bad";
    pill.textContent = "Can't load data";
    pill.title = String(err);
  }
}

function markSeen() {
  store(SEEN_KEY, new Date().toISOString());
}

document.addEventListener("DOMContentLoaded", () => {
  state.seenBefore = store(SEEN_KEY);
  state.group = store(GROUP_KEY) === "date" ? "date" : "category";
  try {
    const f = JSON.parse(store(FOLDED_KEY) || "null");
    if (Array.isArray(f)) state.folded = new Set(f);
  } catch (_) { /* keep the default */ }
  for (const b of document.querySelectorAll("#group-toggle button")) {
    b.addEventListener("click", () => {
      state.group = b.dataset.group;
      store(GROUP_KEY, state.group);
      drawGroupToggle();
      drawEmails(state.data ? state.data.emails || [] : []);
    });
  }
  drawGroupToggle();
  document.getElementById("search").addEventListener("input", (ev) => {
    state.query = ev.target.value;
    drawEmails(state.data ? state.data.emails || [] : []);
  });
  window.addEventListener("hashchange", showView);
  // "New" marks compare against your previous visit; this visit counts once you leave.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      markSeen();
    } else {
      state.seenBefore = store(SEEN_KEY);
      load();
    }
  });
  window.addEventListener("pagehide", markSeen);
  setInterval(() => {
    if (document.visibilityState === "visible") load();
  }, REFRESH_MS);
  showView();
  load();
});
