#!/usr/bin/env python3
"""Builds a local, self-contained HTML page for blind human review of the
155 posters (of 658 comparable) where the isolated-prompt gate 13 score
and the combined-mega-prompt score disagree on the flag decision
(>=0.5 threshold) for at least one of blood_gore/violence/sexual_content.

Deliberately blind: does NOT show either system's score, only the poster
image and a direct yes/no/unsure question per disputed axis -- so the
human judgment isn't anchored toward whichever number they see first.
Each poster shows only the axes it actually disagrees on (1-3 of them),
not all three every time.

Same review-tool pattern as build_poster_type_review_page.py (self-
contained HTML, base64-embedded images, localStorage autosave, CSV
export/import to resume across sessions).

  python3 scripts/qa/build_mega_prompt_review_page.py
  open data/qa/mega_prompt_review.html
"""
from __future__ import annotations

import base64
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.tmdb_client import IMAGE_BASE_URL  # noqa: E402

GATE13_CSV = ROOT / "data" / "ground_truth" / "content_moderation_es_poster.csv"
MEGA_CSV = ROOT / "data" / "qa" / "nova_mega_prompt_comparison.csv"
OUT_HTML = ROOT / "data" / "qa" / "mega_prompt_review.html"
POSTER_CACHE = ROOT / "data" / "qa" / ".mega_prompt_review_cache"
TMDB_IMG = f"{IMAGE_BASE_URL}w500"
MAX_WORKERS = 16

AXIS_LABELS = {
    "blood_gore": "sangre / gore real",
    "violence": "violencia real",
    "sexual_content": "contenido sexual real",
}

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Mega-prompt vs isolated: moderation review</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 780px; margin: 0 auto; padding: 24px 20px 220px; background: #fafafa; color: #1a1a1a; }
  h1 { font-size: 18px; margin-bottom: 4px; }
  .sub { color: #666; font-size: 13px; margin-bottom: 20px; }
  .progress { position: sticky; top: 0; background: #fafafa; padding: 10px 0; border-bottom: 1px solid #ddd; margin-bottom: 16px; z-index: 10; }
  .progress-bar { height: 6px; background: #e0e0e0; border-radius: 3px; overflow: hidden; margin-top: 6px; }
  .progress-fill { height: 100%; background: #2f7d5c; transition: width .2s; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 20px; }
  .poster-wrap { text-align: center; margin-bottom: 16px; }
  img { max-width: 100%; max-height: 48vh; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.15); background: #eee; }
  .catalog-title { font-size: 20px; font-weight: 600; text-align: center; margin: 4px 0 4px; }
  .catalog-title .label { display: block; font-size: 11px; font-weight: 400; color: #888; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 2px; }
  .meta { text-align: center; font-size: 13px; color: #888; margin-bottom: 8px; }
  .action-bar { position: fixed; left: 0; right: 0; bottom: 0; background: #fff;
                border-top: 1px solid #ddd; box-shadow: 0 -4px 12px rgba(0,0,0,.08);
                padding: 12px 20px calc(12px + env(safe-area-inset-bottom)); z-index: 20; }
  .action-bar-inner { max-width: 740px; margin: 0 auto; }
  .axis-row { margin-bottom: 10px; }
  .axis-label { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
  button.verdict { flex: 1; min-width: 70px; padding: 10px 6px; font-size: 13px; border-radius: 8px; border: 2px solid #ccc; background: #fff; color: #1a1a1a; cursor: pointer; font-weight: 600; }
  button.verdict:hover { border-color: #999; }
  button.verdict.active[data-v="si"] { background: #fbeae5; border-color: #bf3f24; color: #9c331d; }
  button.verdict.active[data-v="no"] { background: #e7f2ec; border-color: #3f7d5c; color: #316447; }
  button.verdict.active[data-v="no_seguro"] { background: #f0f0f0; border-color: #888; color: #444; }
  .nav { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
  .nav button.plain { padding: 8px 16px; border-radius: 6px; border: 1px solid #ccc; background: #fff; color: #1a1a1a; cursor: pointer; }
  .nav button.plain:disabled { opacity: .4; cursor: default; }
  .counter { font-size: 13px; color: #666; }
  .footer { margin-top: 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  .export, .import-label { padding: 10px 18px; border-radius: 6px; border: none; background: #2f5f8a; color: #fff; cursor: pointer; font-weight: 600; display: inline-block; }
  .export:hover, .import-label:hover { background: #24486b; }
  .import-label { background: #666; }
  .import-label:hover { background: #4d4d4d; }
  .hint { font-size: 12px; color: #999; }
  .jump { font-size: 12px; }
  .jump input { width: 70px; padding: 4px 6px; border-radius: 4px; border: 1px solid #ccc; }
</style>
</head>
<body>

<h1>Revisi&oacute;n ciega: prompt aislado vs. combinado</h1>
<div class="sub">155 posters donde gate 13 (prompt aislado) y el mega-prompt (prompt combinado,
15+ campos) no coinciden en si el poster cruza el umbral de 0.5 en gore, violencia o
contenido sexual. No se muestra que dijo cada sistema -- solo respond&eacute; lo que ves
en el poster. Cada poster muestra solo los ejes donde realmente discrepan (1 a 3).
El progreso se guarda solo en este navegador (localStorage).</div>

<div class="progress">
  <span class="counter" id="counter"></span>
  <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
</div>

<div class="card">
  <div class="poster-wrap"><img id="poster-img" src="" alt=""></div>
  <div class="catalog-title"><span class="label">Catalog title</span><span id="catalog-title-text"></span></div>
  <div class="meta" id="meta-text"></div>
</div>

<div class="footer">
  <span class="hint" id="export-hint"></span>
  <span class="jump">ir a # <input type="number" id="jump-input" min="1" onkeydown="if(event.key==='Enter') jumpTo()"> <button class="plain" onclick="jumpTo()">Ir</button></span>
  <span>
    <label class="import-label" for="import-input">Import progress</label>
    <input type="file" id="import-input" accept=".csv" style="display:none" onchange="importCSV(this.files[0])">
    <button class="export" onclick="exportCSV()">Export CSV</button>
  </span>
</div>

<div class="action-bar">
  <div class="action-bar-inner" id="axes-container"></div>
  <div class="nav">
    <button class="plain" id="prev-btn" onclick="go(-1)">&larr; Prev</button>
    <span class="hint">&larr; &rarr; = navegar (auto-avanza al responder todos los ejes)</span>
    <button class="plain" id="next-btn" onclick="go(1)">Next &rarr;</button>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const AXIS_LABELS = __AXIS_LABELS_JSON__;
const STORAGE_KEY = "mega_prompt_review_v1";

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (e) { return {}; }
}
function saveState(state) { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }

let state = loadState();
let idx = 0;

function render() {
  const row = DATA[idx];
  document.getElementById("poster-img").src = row.img;
  document.getElementById("poster-img").alt = row.title;
  document.getElementById("catalog-title-text").textContent = row.title;
  document.getElementById("meta-text").textContent = `id ${row.id}`;

  const container = document.getElementById("axes-container");
  container.innerHTML = "";
  const axes = row.disagree_fields.split("|");
  const s = state[row.id] || {};
  axes.forEach(axis => {
    const div = document.createElement("div");
    div.className = "axis-row";
    const label = document.createElement("div");
    label.className = "axis-label";
    label.textContent = `¿Muestra ${AXIS_LABELS[axis]}?`;
    div.appendChild(label);
    const btnRow = document.createElement("div");
    btnRow.className = "buttons";
    [["si","Sí"],["no","No"],["no_seguro","No seguro"]].forEach(([v,label2]) => {
      const b = document.createElement("button");
      b.className = "verdict" + ((s[axis] === v) ? " active" : "");
      b.dataset.v = v;
      b.textContent = label2;
      b.onclick = () => setVerdict(axis, v);
      btnRow.appendChild(b);
    });
    div.appendChild(btnRow);
    container.appendChild(div);
  });

  const reviewedCount = DATA.filter(r => {
    const rs = state[r.id] || {};
    return r.disagree_fields.split("|").every(a => rs[a]);
  }).length;
  document.getElementById("counter").textContent =
    `${idx + 1} / ${DATA.length}   (${reviewedCount} fully reviewed so far)`;
  document.getElementById("progress-fill").style.width = (reviewedCount / DATA.length * 100) + "%";
  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").disabled = idx === DATA.length - 1;
  document.getElementById("export-hint").textContent =
    reviewedCount < DATA.length ? `${DATA.length - reviewedCount} left before export is complete` : "all reviewed -- ready to export";
}

function setVerdict(axis, v) {
  const row = DATA[idx];
  state[row.id] = state[row.id] || {};
  state[row.id][axis] = v;
  saveState(state);
  render();
  const axes = row.disagree_fields.split("|");
  const s = state[row.id];
  if (axes.every(a => s[a]) && idx < DATA.length - 1) {
    setTimeout(() => go(1), 150);
  }
}
function go(delta) {
  idx = Math.max(0, Math.min(DATA.length - 1, idx + delta));
  render();
}
function jumpTo() {
  const v = parseInt(document.getElementById("jump-input").value, 10);
  if (!v) return;
  idx = Math.max(0, Math.min(DATA.length - 1, v - 1));
  render();
}
document.addEventListener("keydown", (e) => {
  if (document.activeElement.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") go(-1);
  else if (e.key === "ArrowRight") go(1);
});

function parseCSVLine(line) {
  const out = [];
  let cur = "", inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') { inQuotes = false; }
      else { cur += c; }
    } else {
      if (c === '"') inQuotes = true;
      else if (c === ",") { out.push(cur); cur = ""; }
      else cur += c;
    }
  }
  out.push(cur);
  return out;
}

function importCSV(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length > 0);
    if (!lines.length) return;
    const header = parseCSVLine(lines[0]);
    const idCol = header.indexOf("id");
    const gCol = header.indexOf("human_blood_gore");
    const vCol = header.indexOf("human_violence");
    const sCol = header.indexOf("human_sexual_content");
    if (idCol === -1) { alert("This doesn't look like an exported CSV (missing id column)."); return; }
    let imported = 0;
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCSVLine(lines[i]);
      const id = cols[idCol];
      if (!id) continue;
      state[id] = state[id] || {};
      if (gCol !== -1 && cols[gCol]) state[id].blood_gore = cols[gCol];
      if (vCol !== -1 && cols[vCol]) state[id].violence = cols[vCol];
      if (sCol !== -1 && cols[sCol]) state[id].sexual_content = cols[sCol];
      imported++;
    }
    saveState(state);
    const firstUnreviewed = DATA.findIndex(row => {
      const rs = state[row.id] || {};
      return !row.disagree_fields.split("|").every(a => rs[a]);
    });
    idx = firstUnreviewed === -1 ? 0 : firstUnreviewed;
    render();
    alert(`Imported ${imported} row(s). Jumped to ${firstUnreviewed === -1 ? "the first poster" : "#" + (idx + 1)}.`);
  };
  reader.readAsText(file);
}

function exportCSV() {
  const header = ["id", "title", "poster_path", "disagree_fields", "human_blood_gore", "human_violence", "human_sexual_content"];
  const lines = [header.join(",")];
  for (const row of DATA) {
    const s = state[row.id] || {};
    const esc = (v) => '"' + String(v || "").replace(/"/g, '""') + '"';
    lines.push([row.id, esc(row.title), row.poster_path, row.disagree_fields,
                s.blood_gore || "", s.violence || "", s.sexual_content || ""].join(","));
  }
  const blob = new Blob([lines.join("\\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "mega_prompt_human_review.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

render();
</script>
</body>
</html>
"""


def fetch_poster_b64(session: requests.Session, poster_path: str) -> str:
    cache_file = POSTER_CACHE / (poster_path.lstrip("/").replace("/", "_"))
    if cache_file.exists():
        content = cache_file.read_bytes()
    else:
        resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=20)
        resp.raise_for_status()
        content = resp.content
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def fetch_one(row: dict) -> tuple[str, str]:
    session = requests.Session()
    try:
        return row["id"], fetch_poster_b64(session, row["poster_path"])
    except Exception as e:
        print(f"  {row['id']}: poster fetch failed ({e}) -- leaving img blank", file=sys.stderr)
        return row["id"], ""


def main():
    gate13 = {r["id"]: r for r in csv.DictReader(GATE13_CSV.open(newline="", encoding="utf-8"))}
    mega = {r["id"]: r for r in csv.DictReader(MEGA_CSV.open(newline="", encoding="utf-8"))}

    ids = sorted(set(gate13) & set(mega))
    rows = []
    for i in ids:
        if mega[i].get("error"):
            continue
        disagree = []
        for field, iso_field in [("blood_gore", "nova_blood_gore"), ("violence", "nova_violence"),
                                  ("sexual_content", "nova_sexual_content")]:
            iso_v = float(gate13[i].get(iso_field) or 0)
            mega_v = float(mega[i].get(f"mega_{field}") or 0)
            if (iso_v >= 0.5) != (mega_v >= 0.5):
                disagree.append(field)
        if disagree:
            rows.append({"id": i, "title": gate13[i]["title"], "poster_path": gate13[i]["poster_path"],
                          "disagree_fields": "|".join(disagree)})

    print(f"{len(rows)} disputed posters to review")

    imgs: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_one, row) for row in rows]
        for fut in as_completed(futures):
            id_, img = fut.result()
            imgs[id_] = img
            done += 1
            if done % 25 == 0 or done == len(rows):
                print(f"{done}/{len(rows)} posters embedded")

    n_failed = 0
    for row in rows:
        row["img"] = imgs.get(row["id"], "")
        if not row["img"]:
            n_failed += 1

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(rows)).replace("__AXIS_LABELS_JSON__", json.dumps(AXIS_LABELS))
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 * 1024)
    print(f"wrote {OUT_HTML} ({size_mb:.1f} MB, {len(rows)} posters, {n_failed} failed to fetch)")


if __name__ == "__main__":
    main()
