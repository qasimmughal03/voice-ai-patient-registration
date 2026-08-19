"""Minimal read-only dashboard listing registered patients.

Self-contained: no build step, no CDN, no external assets. It reads the same
public REST API the reviewers use, so what it shows is exactly what the API
returns — no second data path to drift out of sync.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harborview — Registered Patients</title>
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --ink: #16191d; --muted: #6b7280;
    --line: #e5e7eb; --accent: #1f6feb; --chip: #eef2f7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1216; --panel: #161a20; --ink: #e8eaed; --muted: #9aa4b2;
      --line: #262c34; --accent: #5b9dff; --chip: #1d232b;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem 1.25rem; background: var(--bg); color: var(--ink);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .wrap { max-width: 1100px; margin: 0 auto; }
  header { display: flex; flex-wrap: wrap; gap: .75rem 1rem; align-items: baseline;
           justify-content: space-between; margin-bottom: 1.25rem; }
  h1 { font-size: 1.35rem; margin: 0; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: .875rem; }
  .panel { background: var(--panel); border: 1px solid var(--line);
           border-radius: 12px; overflow: hidden; }
  .toolbar { display: flex; gap: .5rem; padding: .85rem; border-bottom: 1px solid var(--line); }
  input {
    flex: 1; padding: .55rem .7rem; font: inherit; color: var(--ink);
    background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  button {
    padding: .55rem .9rem; font: inherit; cursor: pointer; color: var(--ink);
    background: var(--chip); border: 1px solid var(--line); border-radius: 8px;
  }
  .scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; white-space: nowrap; }
  th, td { padding: .7rem .85rem; text-align: left; border-bottom: 1px solid var(--line); }
  th { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); font-weight: 600; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover { background: var(--chip); }
  .name { font-weight: 600; }
  .mono { font-variant-numeric: tabular-nums; }
  .id { color: var(--muted); font-size: .78rem; }
  .msg { padding: 2.5rem 1rem; text-align: center; color: var(--muted); }
  footer { margin-top: 1rem; color: var(--muted); font-size: .8rem; }
  a { color: var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Harborview Family Clinic — Registered Patients</h1>
      <div class="sub">Live from the same REST API at <code>/patients</code>.
        Soft-deleted records are excluded.</div>
    </div>
    <div class="sub" id="count"></div>
  </header>

  <div class="panel">
    <div class="toolbar">
      <input id="q" type="search" placeholder="Filter by name, phone, city or state…"
             aria-label="Filter patients">
      <button id="refresh">Refresh</button>
    </div>
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Date of birth</th><th>Sex</th><th>Phone</th>
            <th>Address</th><th>Insurance</th><th>Registered</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="msg" id="msg">Loading…</div>
  </div>

  <footer>
    Read-only view. Records are created by the voice agent or
    <code>POST /patients</code>. API docs at <a href="/docs">/docs</a>.
  </footer>
</div>

<script>
const rowsEl = document.getElementById('rows');
const msgEl = document.getElementById('msg');
const countEl = document.getElementById('count');
const qEl = document.getElementById('q');
let patients = [];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function phone(p) {
  return p && p.length === 10 ? `(${p.slice(0,3)}) ${p.slice(3,6)}-${p.slice(6)}` : (p || '—');
}
function dob(d) {
  if (!d) return '—';
  const [y, m, day] = d.split('-');
  return `${m}/${day}/${y}`;
}
function address(p) {
  const line = [p.address_line_1, p.address_line_2].filter(Boolean).join(', ');
  return [line, p.city, [p.state, p.zip_code].filter(Boolean).join(' ')]
    .filter(Boolean).join(', ') || '—';
}

function render() {
  const q = qEl.value.trim().toLowerCase();
  const shown = !q ? patients : patients.filter(p =>
    [p.first_name, p.last_name, p.phone_number, p.city, p.state]
      .filter(Boolean).join(' ').toLowerCase().includes(q));

  rowsEl.innerHTML = shown.map(p => `
    <tr>
      <td><div class="name">${esc(p.first_name)} ${esc(p.last_name)}</div>
          <div class="id">${esc(p.patient_id)}</div></td>
      <td class="mono">${esc(dob(p.date_of_birth))}</td>
      <td>${esc(p.sex)}</td>
      <td class="mono">${esc(phone(p.phone_number))}</td>
      <td>${esc(address(p))}</td>
      <td>${esc(p.insurance_provider || '—')}</td>
      <td class="mono">${esc((p.created_at || '').slice(0, 10))}</td>
    </tr>`).join('');

  countEl.textContent = q
    ? `${shown.length} of ${patients.length} patients`
    : `${patients.length} patient${patients.length === 1 ? '' : 's'}`;
  msgEl.style.display = shown.length ? 'none' : 'block';
  if (!shown.length) {
    msgEl.textContent = patients.length
      ? 'No patients match that filter.'
      : 'No patients registered yet. Call the number to add one.';
  }
}

async function load() {
  msgEl.style.display = 'block';
  msgEl.textContent = 'Loading…';
  try {
    const res = await fetch('/patients');
    const body = await res.json();
    if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
    patients = body.data || [];
    render();
  } catch (err) {
    rowsEl.innerHTML = '';
    countEl.textContent = '';
    msgEl.style.display = 'block';
    msgEl.textContent = `Could not load patients: ${err.message}`;
  }
}

qEl.addEventListener('input', render);
document.getElementById('refresh').addEventListener('click', load);
load();
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(PAGE)
