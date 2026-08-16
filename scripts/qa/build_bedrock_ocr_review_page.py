#!/usr/bin/env python3
"""Builds a local, self-contained HTML page for blind human review of
data/ground_truth/bedrock_ocr_sample.csv -- the 100-poster stratified
sample used to ground-truth 04_bedrock_ocr.py's match/mismatch/
no_title_on_poster verdict (see docs/MODELS.md, "Building a human
ground-truth set" for how that sample was drawn and why).

Deliberately doesn't show Nova's own verdict/reason during review --
only id, catalog title, and the poster image, so the reviewer's judgment
isn't anchored by what the model already said. Review state autosaves to
the browser's localStorage (safe to close the tab and resume later); an
Export CSV button writes id/title/original_title/original_language/
poster_path/stratum/human_verdict/human_note once you're done, which
04_bedrock_ocr.py's --validate mode (once built) will compare against a
live run. human_verdict is one of match/mismatch/no_title_on_poster/
unjudgeable -- see the "non-English posters" note below for why
unjudgeable exists and how it should be handled (excluded from
accuracy/precision/recall, not treated as a wrong answer).

This has to be a plain local HTML file, not a hosted/shared page:
exporting the finished CSV needs a real browser download, which a
sandboxed viewer blocks but a file opened directly in your own browser
does not.

Poster images are downloaded once and embedded as base64 data URIs,
not loaded live from TMDB's CDN at review time -- a file:// page making
live cross-origin image requests gets silently blocked by several
browsers' tracking-protection features (blank/white boxes instead of
posters, no console error), so this avoids the network round-trip
during review entirely. Downloads are cached in
data/ground_truth/.poster_cache/ so re-running this script after
editing the page's HTML/CSS doesn't re-fetch images already on disk.

48 of the 100 sampled posters are non-English by TMDB's own
original_language field -- a reviewer who doesn't read that poster's
script (Japanese, Cyrillic, etc.) has no way to judge match/mismatch
from the poster alone. Two mitigations, both from TMDB's own catalog
metadata (not derived from Nova's OCR read, so showing them doesn't
anchor the reviewer to the model's own possibly-wrong answer the way
showing `verdict`/`reason` would): the page shows `original_title`
alongside the (often English-localized) catalog `title` whenever they
differ, and there's a 4th "Can't judge" button for cases where even
that isn't enough (a script the reviewer genuinely can't read).
"Can't judge" rows get excluded from accuracy/precision/recall, not
coerced into a guess -- see docs/MODELS.md.

  python3 scripts/qa/build_bedrock_ocr_review_page.py
  open data/ground_truth/bedrock_ocr_review.html
"""
from __future__ import annotations

import base64
import csv
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.tmdb_client import IMAGE_BASE_URL  # noqa: E402

SAMPLE_CSV = ROOT / "data" / "ground_truth" / "bedrock_ocr_sample.csv"
OUT_HTML = ROOT / "data" / "ground_truth" / "bedrock_ocr_review.html"
POSTER_CACHE = ROOT / "data" / "ground_truth" / ".poster_cache"
TMDB_IMG = f"{IMAGE_BASE_URL}w780"

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Bedrock OCR ground truth review</title>
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
  img { max-width: 100%; max-height: 42vh; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.15); }
  .catalog-title { font-size: 20px; font-weight: 600; text-align: center; margin: 4px 0 18px; }
  .catalog-title .label { display: block; font-size: 11px; font-weight: 400; color: #888; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 2px; }
  .original-title { text-align: center; font-size: 14px; color: #555; margin: -12px 0 18px; }
  .original-title .lang-badge { display: inline-block; background: #efecf6; color: #55467c; font-size: 10px; font-weight: 600; text-transform: uppercase; padding: 1px 6px; border-radius: 4px; margin-right: 6px; vertical-align: middle; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px; border: 1px solid #ccc; font-family: inherit; font-size: 13px; resize: vertical; min-height: 44px; background: #fff; color: #1a1a1a; }
  /* Action bar (verdict buttons + nav) is fixed to the viewport bottom, not
     part of normal document flow -- this is the fix for buttons becoming
     unreachable below the fold on tall poster images/short windows. It's
     always visible and always clickable regardless of scroll position or
     how tall any given poster renders. */
  .action-bar { position: fixed; left: 0; right: 0; bottom: 0; background: #fff;
                border-top: 1px solid #ddd; box-shadow: 0 -4px 12px rgba(0,0,0,.08);
                padding: 12px 20px calc(12px + env(safe-area-inset-bottom)); z-index: 20; }
  .action-bar-inner { max-width: 740px; margin: 0 auto; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  button.verdict { flex: 1; min-width: 90px; padding: 12px 8px; font-size: 14px; border-radius: 8px; border: 2px solid #ccc; background: #fff; color: #1a1a1a; cursor: pointer; font-weight: 600; }
  button.verdict:hover { border-color: #999; }
  button.verdict.active[data-v="match"] { background: #e7f2ec; border-color: #3f7d5c; color: #316447; }
  button.verdict.active[data-v="mismatch"] { background: #fbeae5; border-color: #bf3f24; color: #9c331d; }
  button.verdict.active[data-v="no_title_on_poster"] { background: #efecf6; border-color: #6b5b95; color: #55467c; }
  button.verdict.active[data-v="unjudgeable"] { background: #f0f0f0; border-color: #888; color: #444; }
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
</style>
</head>
<body>

<h1>Bedrock OCR ground truth review</h1>
<div class="sub">100 real posters, stratified 40 accurate / 40 inaccurate / 20 no_title_on_poster
(labels hidden below on purpose -- judge each poster blind, without seeing what Nova said).
Question: does the <b>visible title text on the poster</b> match the catalog title shown?
When the film's original title differs from the catalog title (non-English releases),
it's shown below as an extra reference -- from TMDB's own catalog data, not from any OCR/vision
model, so it doesn't anchor you to what Nova read. If you still can't judge a poster (a script
you don't read), use "Can't judge" rather than guessing -- those get excluded from accuracy
scoring, not counted as errors either way.
Progress saves automatically in this browser (localStorage) -- closing the tab is safe. Already
have a partial bedrock_ocr_ground_truth_human_labels.csv from an earlier session? Use "Import
progress" below to resume exactly where you left off instead of redoing it.</div>

<div class="progress">
  <span class="counter" id="counter"></span>
  <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
</div>

<div class="card">
  <div class="poster-wrap"><img id="poster-img" src="" alt=""></div>
  <div class="catalog-title"><span class="label">Catalog title</span><span id="catalog-title-text"></span></div>
  <div class="original-title" id="original-title-row" style="display:none;">
    <span class="lang-badge" id="lang-badge"></span><span id="original-title-text"></span>
  </div>
  <textarea id="note" placeholder="optional note (e.g. what the poster actually says)" onchange="setNote(this.value)"></textarea>
</div>

<div class="footer">
  <span class="hint" id="export-hint"></span>
  <span>
    <label class="import-label" for="import-input">Import progress</label>
    <input type="file" id="import-input" accept=".csv" style="display:none" onchange="importCSV(this.files[0])">
    <button class="export" onclick="exportCSV()">Export CSV</button>
  </span>
</div>

<div class="action-bar">
  <div class="action-bar-inner">
    <div class="buttons">
      <button class="verdict" data-v="match" onclick="setVerdict('match')">Match</button>
      <button class="verdict" data-v="mismatch" onclick="setVerdict('mismatch')">Mismatch</button>
      <button class="verdict" data-v="no_title_on_poster" onclick="setVerdict('no_title_on_poster')">No title on poster</button>
      <button class="verdict" data-v="unjudgeable" onclick="setVerdict('unjudgeable')">Can't judge</button>
    </div>
    <div class="nav">
      <button class="plain" id="prev-btn" onclick="go(-1)">&larr; Prev</button>
      <span class="hint">keys: 1/2/3/4 = verdict, &larr; &rarr; = navigate</span>
      <button class="plain" id="next-btn" onclick="go(1)">Next &rarr;</button>
    </div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const STORAGE_KEY = "bedrock_ocr_gt_review_v1";

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
  const otRow = document.getElementById("original-title-row");
  if (row.original_title && row.original_title !== row.title) {
    document.getElementById("lang-badge").textContent = row.original_language || "?";
    document.getElementById("original-title-text").textContent = "original: " + row.original_title;
    otRow.style.display = "block";
  } else {
    otRow.style.display = "none";
  }
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
document.addEventListener("keydown", (e) => {
  if (document.activeElement.tagName === "TEXTAREA") return;
  if (e.key === "1") setVerdict("match");
  else if (e.key === "2") setVerdict("mismatch");
  else if (e.key === "3") setVerdict("no_title_on_poster");
  else if (e.key === "4") setVerdict("unjudgeable");
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
      alert("This doesn't look like an exported ground-truth CSV (missing id/human_verdict columns).");
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
  const header = ["id", "title", "original_title", "original_language", "poster_path", "stratum", "human_verdict", "human_note"];
  const lines = [header.join(",")];
  for (const row of DATA) {
    const s = state[row.id] || {};
    const esc = (v) => '"' + String(v || "").replace(/"/g, '""') + '"';
    lines.push([row.id, esc(row.title), esc(row.original_title), row.original_language, row.poster_path,
                row.stratum, s.verdict || "", esc(s.note)].join(","));
  }
  const blob = new Blob([lines.join("\\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "bedrock_ocr_ground_truth_human_labels.csv";
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
    cache_file = POSTER_CACHE / (poster_path.lstrip("/") .replace("/", "_"))
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


def main():
    with SAMPLE_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    session = requests.Session()
    for i, row in enumerate(rows, 1):
        try:
            row["img"] = fetch_poster_b64(session, row["poster_path"])
        except Exception as e:
            print(f"  {row['id']}: poster fetch failed ({e}) -- leaving img blank", file=sys.stderr)
            row["img"] = ""
        if i % 20 == 0 or i == len(rows):
            print(f"{i}/{len(rows)} posters embedded")

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(rows))
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 * 1024)
    print(f"wrote {OUT_HTML} ({size_mb:.1f} MB, {len(rows)} posters embedded)")


if __name__ == "__main__":
    main()
