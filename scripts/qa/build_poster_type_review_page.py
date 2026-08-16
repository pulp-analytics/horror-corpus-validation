#!/usr/bin/env python3
"""Builds a local, self-contained HTML page for blind human review of
data/ground_truth/poster_type_sample.csv -- real catalog ids whose poster
had zero OCR'd characters in the real project's own poster_title_match.csv
(2,630 of 65,107 titles, ~4%; 2,539 of those still have a resolvable
poster_path in the real catalog data). Zero visible text is a *candidate*
signal that an image isn't actually a movie poster (a still, a photo, a
placeholder) -- but plenty of real posters are legitimately textless
(minimalist international releases), so this needs a human look, not an
automated rule. See docs/RESULTS.md for however this sample's review
turns out.

Same review-tool pattern as build_bedrock_ocr_review_page.py (same
reasons: base64-embedded images so a file:// page doesn't get silently
blocked loading cross-origin TMDB images, browser localStorage autosave,
CSV export via a real download since a sandboxed viewer blocks that).
Differences for this sample's ~25x larger size (2,539 vs 100 posters):

  - Poster images are fetched at w185 (vs w780) -- plenty of detail to
    tell "designed poster" from "photo/still/placeholder" at a glance,
    and keeps the embedded page a fraction of the size.
  - Downloads run concurrently (a thread pool, not one-at-a-time) --
    this is a CDN built for exactly this kind of bulk image serving, not
    a rate-limited business API, so there's no reason to throttle it to
    single-file-at-a-time speed like the TMDB *API* calls elsewhere in
    this repo.
  - The question is simpler (is this a poster at all, not does the title
    match), so there are 3 verdict buttons instead of 4: "Es poster",
    "No es poster", "No estoy seguro".

  python3 scripts/qa/build_poster_type_review_page.py
  open data/ground_truth/poster_type_review.html
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

SAMPLE_CSV = ROOT / "data" / "ground_truth" / "poster_type_sample.csv"
OUT_HTML = ROOT / "data" / "ground_truth" / "poster_type_review.html"
POSTER_CACHE = ROOT / "data" / "ground_truth" / ".poster_type_cache"
TMDB_IMG = f"{IMAGE_BASE_URL}w185"
MAX_WORKERS = 16

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Poster type review</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 780px; margin: 0 auto; padding: 24px 20px 160px; background: #fafafa; color: #1a1a1a; }
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
  .meta { text-align: center; font-size: 13px; color: #888; margin-bottom: 18px; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px; border: 1px solid #ccc; font-family: inherit; font-size: 13px; resize: vertical; min-height: 44px; background: #fff; color: #1a1a1a; }
  .action-bar { position: fixed; left: 0; right: 0; bottom: 0; background: #fff;
                border-top: 1px solid #ddd; box-shadow: 0 -4px 12px rgba(0,0,0,.08);
                padding: 12px 20px calc(12px + env(safe-area-inset-bottom)); z-index: 20; }
  .action-bar-inner { max-width: 740px; margin: 0 auto; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  button.verdict { flex: 1; min-width: 90px; padding: 12px 8px; font-size: 14px; border-radius: 8px; border: 2px solid #ccc; background: #fff; color: #1a1a1a; cursor: pointer; font-weight: 600; }
  button.verdict:hover { border-color: #999; }
  button.verdict.active[data-v="es_poster"] { background: #e7f2ec; border-color: #3f7d5c; color: #316447; }
  button.verdict.active[data-v="no_es_poster"] { background: #fbeae5; border-color: #bf3f24; color: #9c331d; }
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

<h1>Poster type review</h1>
<div class="sub">2,539 ids reales cuyo poster tuvo 0 caracteres de OCR en poster_title_match.csv
(4% del corpus real). Pregunta: <b>la imagen es un poster de pelicula real</b> (arte diseniado,
aunque no tenga texto), o <b>no es un poster</b> (still de la pelicula, foto generica, placeholder,
imagen equivocada)? Si no estas seguro, usa "No estoy seguro" en vez de adivinar.
El progreso se guarda solo en este navegador (localStorage) -- cerrar la pestana es seguro.
Ya tenes un CSV parcial de una sesion anterior? Usa "Import progress" para retomar donde quedaste.</div>

<div class="progress">
  <span class="counter" id="counter"></span>
  <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
</div>

<div class="card">
  <div class="poster-wrap"><img id="poster-img" src="" alt=""></div>
  <div class="catalog-title"><span class="label">Catalog title</span><span id="catalog-title-text"></span></div>
  <div class="meta" id="meta-text"></div>
  <textarea id="note" placeholder="nota opcional (ej: es un still, es una foto de elenco, etc.)" onchange="setNote(this.value)"></textarea>
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
  <div class="action-bar-inner">
    <div class="buttons">
      <button class="verdict" data-v="es_poster" onclick="setVerdict('es_poster')">Es poster</button>
      <button class="verdict" data-v="no_es_poster" onclick="setVerdict('no_es_poster')">No es poster</button>
      <button class="verdict" data-v="no_seguro" onclick="setVerdict('no_seguro')">No estoy seguro</button>
    </div>
    <div class="nav">
      <button class="plain" id="prev-btn" onclick="go(-1)">&larr; Prev</button>
      <span class="hint">teclas: 1/2/3 = veredicto, &larr; &rarr; = navegar</span>
      <button class="plain" id="next-btn" onclick="go(1)">Next &rarr;</button>
    </div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const STORAGE_KEY = "poster_type_review_v1";

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (e) { return {}; }
}
function saveState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

let state = loadState();
let idx = 0;

function render() {
  const row = DATA[idx];
  document.getElementById("poster-img").src = row.img;
  document.getElementById("poster-img").alt = row.title;
  document.getElementById("catalog-title-text").textContent = row.title;
  document.getElementById("meta-text").textContent = `id ${row.id} · ${row.year || "?"} · ${row.original_language || "?"}`;
  document.getElementById("note").value = (state[row.id] && state[row.id].note) || "";
  document.querySelectorAll("button.verdict").forEach(b => {
    b.classList.toggle("active", state[row.id] && state[row.id].verdict === b.dataset.v);
  });
  const reviewed = Object.keys(state).filter(id => state[id].verdict).length;
  document.getElementById("counter").textContent =
    `${idx + 1} / ${DATA.length}   (${reviewed} reviewed so far)`;
  document.getElementById("progress-fill").style.width = (reviewed / DATA.length * 100) + "%";
  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").disabled = idx === DATA.length - 1;
  document.getElementById("export-hint").textContent =
    reviewed < DATA.length ? `${DATA.length - reviewed} left before export is complete` : "all reviewed -- ready to export";
}

function setVerdict(v) {
  const id = DATA[idx].id;
  state[id] = state[id] || {};
  state[id].verdict = v;
  saveState(state);
  render();
  if (idx < DATA.length - 1) setTimeout(() => go(1), 150);
}
function setNote(v) {
  const id = DATA[idx].id;
  state[id] = state[id] || {};
  state[id].note = v;
  saveState(state);
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
  if (document.activeElement.tagName === "TEXTAREA" || document.activeElement.tagName === "INPUT") return;
  if (e.key === "1") setVerdict("es_poster");
  else if (e.key === "2") setVerdict("no_es_poster");
  else if (e.key === "3") setVerdict("no_seguro");
  else if (e.key === "ArrowLeft") go(-1);
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
    const verdictCol = header.indexOf("human_verdict");
    const noteCol = header.indexOf("human_note");
    if (idCol === -1 || verdictCol === -1) {
      alert("This doesn't look like an exported CSV (missing id/human_verdict columns).");
      return;
    }
    let imported = 0;
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCSVLine(lines[i]);
      const id = cols[idCol];
      const verdict = cols[verdictCol] || "";
      const note = noteCol !== -1 ? (cols[noteCol] || "") : "";
      if (!id || (!verdict && !note)) continue;
      state[id] = { verdict: verdict || (state[id] && state[id].verdict) || "",
                    note: note || (state[id] && state[id].note) || "" };
      imported++;
    }
    saveState(state);
    const firstUnreviewed = DATA.findIndex(row => !(state[row.id] && state[row.id].verdict));
    idx = firstUnreviewed === -1 ? 0 : firstUnreviewed;
    render();
    alert(`Imported ${imported} row(s). Jumped to ${firstUnreviewed === -1 ? "the first poster (everything's already reviewed)" : "your first unreviewed poster (#" + (idx + 1) + ")"}.`);
  };
  reader.readAsText(file);
}

function exportCSV() {
  const header = ["id", "title", "year", "original_language", "poster_path", "human_verdict", "human_note"];
  const lines = [header.join(",")];
  for (const row of DATA) {
    const s = state[row.id] || {};
    const esc = (v) => '"' + String(v || "").replace(/"/g, '""') + '"';
    lines.push([row.id, esc(row.title), row.year, row.original_language, row.poster_path,
                s.verdict || "", esc(s.note)].join(","));
  }
  const blob = new Blob([lines.join("\\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "poster_type_human_labels.csv";
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
    """Data URI for poster_path, cached on disk so re-running this script
    doesn't re-download images already fetched by an earlier run."""
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
    with SAMPLE_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    imgs: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_one, row) for row in rows]
        for fut in as_completed(futures):
            id_, img = fut.result()
            imgs[id_] = img
            done += 1
            if done % 100 == 0 or done == len(rows):
                print(f"{done}/{len(rows)} posters embedded")

    n_failed = 0
    for row in rows:
        row["img"] = imgs.get(row["id"], "")
        if not row["img"]:
            n_failed += 1

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(rows))
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 * 1024)
    print(f"wrote {OUT_HTML} ({size_mb:.1f} MB, {len(rows)} posters, {n_failed} failed to fetch)")


if __name__ == "__main__":
    main()
