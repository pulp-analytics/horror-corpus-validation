#!/usr/bin/env python3
"""Builds a local, self-contained HTML page for blind human review of gate
16's (`16_verify_celebrities.py`) Nova plausibility verdicts -- the layer
that decides whether a celebrity Rekognition found but TMDB's cast doesn't
list is "clearly_wrong" (flags the poster) or "plausible"/"uncertain"
(doesn't). That verdict is currently trusted from Nova alone; this page is
what turns it into a real, checked number the way gates 4/6/15's
accuracy tables already are (see "Validation methodology" in the README).

Deliberately blind: shows only the poster and the disputed name, never
Nova's own verdict, so the human judgment isn't anchored by it.

Same review-tool pattern as build_poster_type_review_page.py and
build_mega_prompt_review_page.py (self-contained HTML, base64-embedded
images, localStorage autosave, CSV export/import to resume across
sessions).

  python3 scripts/qa/build_celebrity_review_page.py
  open data/qa/celebrity_review.html
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

GATE16_CSV = ROOT / "data" / "sample_output" / "celebrity_verification.csv"
OUT_HTML = ROOT / "data" / "qa" / "celebrity_review.html"
POSTER_CACHE = ROOT / "data" / "qa" / ".celebrity_review_cache"
TMDB_IMG = f"{IMAGE_BASE_URL}w500"
MAX_WORKERS = 16

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Celebrity verification: blind review</title>
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
  .disputed-name { text-align: center; font-size: 16px; font-weight: 600; margin: 12px 0 2px; color: #9c331d; }
  .action-bar { position: fixed; left: 0; right: 0; bottom: 0; background: #fff;
                border-top: 1px solid #ddd; box-shadow: 0 -4px 12px rgba(0,0,0,.08);
                padding: 12px 20px calc(12px + env(safe-area-inset-bottom)); z-index: 20; }
  .action-bar-inner { max-width: 740px; margin: 0 auto; }
  .axis-label { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
  button.verdict { flex: 1; min-width: 70px; padding: 10px 6px; font-size: 13px; border-radius: 8px; border: 2px solid #ccc; background: #fff; color: #1a1a1a; cursor: pointer; font-weight: 600; }
  button.verdict:hover { border-color: #999; }
  button.verdict.active[data-v="plausible"] { background: #e7f2ec; border-color: #3f7d5c; color: #316447; }
  button.verdict.active[data-v="clearly_wrong"] { background: #fbeae5; border-color: #bf3f24; color: #9c331d; }
  button.verdict.active[data-v="uncertain"] { background: #f0f0f0; border-color: #888; color: #444; }
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

<h1>Blind review: celebrity cast mismatches</h1>
<div class="sub">Each row is one poster + one name Rekognition detected that TMDB's real
cast/crew list doesn't have. Nova's own "clearly_wrong / plausible / uncertain" verdict is
NOT shown -- judge only from the poster: could this real person plausibly appear on it?
Progress saves only in this browser (localStorage).</div>

<div class="progress">
  <span class="counter" id="counter"></span>
  <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
</div>

<div class="card">
  <div class="poster-wrap"><img id="poster-img" src="" alt=""></div>
  <div class="catalog-title"><span class="label">Catalog title</span><span id="catalog-title-text"></span></div>
  <div class="meta" id="meta-text"></div>
  <div class="disputed-name" id="disputed-name"></div>
</div>

<div class="footer">
  <span class="hint" id="export-hint"></span>
  <span class="jump">go to # <input type="number" id="jump-input" min="1" onkeydown="if(event.key==='Enter') jumpTo()"> <button class="plain" onclick="jumpTo()">Go</button></span>
  <span>
    <label class="import-label" for="import-input">Import progress</label>
    <input type="file" id="import-input" accept=".csv" style="display:none" onchange="importCSV(this.files[0])">
    <button class="export" onclick="exportCSV()">Export CSV</button>
  </span>
</div>

<div class="action-bar">
  <div class="action-bar-inner">
    <div class="axis-label" id="question-label"></div>
    <div class="buttons">
      <button class="verdict" data-v="plausible" onclick="setVerdict('plausible')">Plausible</button>
      <button class="verdict" data-v="clearly_wrong" onclick="setVerdict('clearly_wrong')">Clearly wrong</button>
      <button class="verdict" data-v="uncertain" onclick="setVerdict('uncertain')">Uncertain</button>
    </div>
  </div>
  <div class="nav">
    <button class="plain" id="prev-btn" onclick="go(-1)">&larr; Prev</button>
    <span class="hint">&larr; &rarr; = navigate (auto-advances on answer)</span>
    <button class="plain" id="next-btn" onclick="go(1)">Next &rarr;</button>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const STORAGE_KEY = "celebrity_review_v1";

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (e) { return {}; }
}
function saveState(state) { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }

let state = loadState();
let idx = 0;

function rowKey(row) { return row.id + "::" + row.name; }

function render() {
  const row = DATA[idx];
  document.getElementById("poster-img").src = row.img;
  document.getElementById("poster-img").alt = row.title;
  document.getElementById("catalog-title-text").textContent = row.title;
  document.getElementById("meta-text").textContent = `id ${row.id}`;
  document.getElementById("disputed-name").textContent = `Rekognition says: "${row.name}"`;
  document.getElementById("question-label").textContent =
    `Could "${row.name}" plausibly appear on this poster?`;

  const key = rowKey(row);
  const v = state[key];
  document.querySelectorAll("button.verdict").forEach(b => {
    b.classList.toggle("active", b.dataset.v === v);
  });

  const reviewedCount = DATA.filter(r => state[rowKey(r)]).length;
  document.getElementById("counter").textContent =
    `${idx + 1} / ${DATA.length}   (${reviewedCount} reviewed so far)`;
  document.getElementById("progress-fill").style.width = (reviewedCount / DATA.length * 100) + "%";
  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").disabled = idx === DATA.length - 1;
  document.getElementById("export-hint").textContent =
    reviewedCount < DATA.length ? `${DATA.length - reviewedCount} left before export is complete` : "all reviewed -- ready to export";
}

function setVerdict(v) {
  const row = DATA[idx];
  state[rowKey(row)] = v;
  saveState(state);
  render();
  if (idx < DATA.length - 1) setTimeout(() => go(1), 150);
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
    const nameCol = header.indexOf("name");
    const vCol = header.indexOf("human_verdict");
    if (idCol === -1 || nameCol === -1) { alert("This doesn't look like an exported CSV (missing id/name column)."); return; }
    let imported = 0;
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCSVLine(lines[i]);
      const key = cols[idCol] + "::" + cols[nameCol];
      if (vCol !== -1 && cols[vCol]) { state[key] = cols[vCol]; imported++; }
    }
    saveState(state);
    const firstUnreviewed = DATA.findIndex(row => !state[rowKey(row)]);
    idx = firstUnreviewed === -1 ? 0 : firstUnreviewed;
    render();
    alert(`Imported ${imported} row(s). Jumped to ${firstUnreviewed === -1 ? "the first row" : "#" + (idx + 1)}.`);
  };
  reader.readAsText(file);
}

function exportCSV() {
  const header = ["id", "title", "poster_path", "name", "human_verdict"];
  const lines = [header.join(",")];
  for (const row of DATA) {
    const v = state[rowKey(row)] || "";
    const esc = (val) => '"' + String(val || "").replace(/"/g, '""') + '"';
    lines.push([row.id, esc(row.title), row.poster_path, esc(row.name), v].join(","));
  }
  const blob = new Blob([lines.join("\\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "celebrity_cast_human_review.csv";
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
    with GATE16_CSV.open(newline="", encoding="utf-8") as f:
        gate16 = list(csv.DictReader(f))

    rows = []
    for r in gate16:
        if r.get("error"):
            continue
        unmatched = json.loads(r["unmatched_celebs"]) if r.get("unmatched_celebs") else []
        for name in unmatched:
            rows.append({"id": r["id"], "title": r.get("title", ""), "poster_path": r.get("poster_path", ""), "name": name})

    print(f"{len(rows)} disputed (poster, name) pairs to review")
    if not rows:
        print(f"no unmatched celebrities found in {GATE16_CSV} -- run 16_verify_celebrities.py first")
        return

    unique_posters = {r["id"]: r["poster_path"] for r in rows}
    imgs: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, {"id": pid, "poster_path": pp}): pid for pid, pp in unique_posters.items()}
        for fut in as_completed(futures):
            id_, img = fut.result()
            imgs[id_] = img
            done += 1
            if done % 25 == 0 or done == len(unique_posters):
                print(f"{done}/{len(unique_posters)} posters embedded")

    n_failed = 0
    for row in rows:
        row["img"] = imgs.get(row["id"], "")
        if not row["img"]:
            n_failed += 1

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(rows))
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 * 1024)
    print(f"wrote {OUT_HTML} ({size_mb:.1f} MB, {len(rows)} rows, {n_failed} posters failed to fetch)")


if __name__ == "__main__":
    main()
