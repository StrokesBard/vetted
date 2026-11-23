/* Vetted UI. No framework and no build step: one file, hash routing, server-sent events for the live run. */
const app = document.getElementById('app');
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let META = null;
let stream = null;

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2400);
}

const STATUS_LABEL = { approved: 'Approved', pending: 'Pending', rejected: 'Rejected', running: 'Running', error: 'Error' };
const pill = (status, big) => `<span class="pill ${esc(status)}${big ? ' big' : ''}">${STATUS_LABEL[status] || esc(status)}</span>`;
const SEV = { block: ['Blocking', '!'], fix: ['Needs fixing', '!'], note: ['Notes for the reviewer', 'i'], pass: ['Passed', '✓'] };
const COUNTRY = { IN: 'India', US: 'United States' };
const CHANNEL = { portal: 'Vendor form', documents_only: 'Documents only' };

function ago(iso) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  const days = Math.round(mins / 1440);
  return `${days} day${days > 1 ? 's' : ''} ago`;
}
const when = (iso) => new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const ms = (n) => (n == null ? '' : n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`);
const fixLink = (id, token) => `${location.origin}/#/fix/${id}?t=${token}`;
const withLink = (text) => String(text || '').replace(/\S*#\/fix\/(\S+)/g, `${location.origin}/#/fix/$1`);

/* ------------------------------------------------------------------ router */

function parseRoute() {
  const [path, query] = (location.hash.replace(/^#\/?/, '') || '').split('?');
  return { parts: path.split('/').filter(Boolean), params: new URLSearchParams(query || '') };
}

async function route() {
  if (stream) { stream.close(); stream = null; }
  closeModal();
  if (!META) {
    META = await api('/api/meta');
    renderAiChip();
  }
  const { parts, params } = parseRoute();
  const name = parts[0] || 'dashboard';
  document.body.classList.toggle('vendor', name === 'fix');
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === name || (a.dataset.route === 'dashboard' && ['case', 'run'].includes(name))));
  window.scrollTo(0, 0);
  try {
    if (name === 'dashboard') await viewDashboard();
    else if (name === 'new') viewNew(params.get('tab') || 'scenarios');
    else if (name === 'run') await viewRun(parts[1], Number(parts[2] || 1));
    else if (name === 'case') await viewCase(parts[1], params);
    else if (name === 'fix') await viewFix(parts[1], params.get('t') || '');
    else if (name === 'suite') viewSuite();
    else if (name === 'how') viewHow();
    else app.innerHTML = `<div class="card empty">That page doesn't exist. <a href="#/">Go to the dashboard</a></div>`;
  } catch (e) {
    app.innerHTML = `<div class="card empty">${esc(e.message)} <a href="#/">Go to the dashboard</a></div>`;
  }
}
window.addEventListener('hashchange', route);

function renderAiChip() {
  const ai = META.ai;
  const el = $('#ai-chip');
  el.classList.toggle('on', ai.enabled);
  el.innerHTML = ai.enabled
    ? `<b><i></i>AI reader on</b>${esc(ai.provider)} · ${esc(ai.model)}${ai.fallback ? ` · fallback ${esc(ai.fallback)}` : ''}`
    : `<b><i></i>Rule-based reader</b>No AI key set. Checks and decisions don't need one.`;
}

$('#reset-btn').addEventListener('click', async () => {
  if (!confirm('Delete every case and restore the sample history?')) return;
  await api('/api/reset', { method: 'POST' });
  toast('Demo data reset');
  location.hash === '#/' || location.hash === '' ? route() : (location.hash = '#/');
});

/* ------------------------------------------------------------------- modal */

function openModal(html, narrow) {
  $('#modal-root').innerHTML = `<div class="overlay"><div class="modal${narrow ? ' narrow' : ''}" role="dialog" aria-modal="true">${html}</div></div>`;
  $('.overlay').addEventListener('mousedown', (e) => { if (e.target.classList.contains('overlay')) closeModal(); });
  $$('[data-close]').forEach((b) => b.addEventListener('click', closeModal));
}
function closeModal() { $('#modal-root').innerHTML = ''; }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

function openDoc(caseId, version, slot, hl, title) {
  const src = `/api/cases/${caseId}/v/${version}/doc/${slot}.png?hl=${encodeURIComponent(hl || '')}`;
  openModal(`
    <div class="modal-head"><div><b>${esc(title)}</b>${hl ? `<div class="small muted">Highlighted: <span class="mono">${esc(hl.split('|')[0])}</span></div>` : ''}</div>
      <div><a class="btn small" target="_blank" rel="noopener" href="/api/cases/${caseId}/v/${version}/doc/${slot}/file">Open original</a> <button class="x" data-close aria-label="Close">×</button></div></div>
    <div class="modal-body flush"><img alt="${esc(title)}" src="${src}"></div>`);
}

/* --------------------------------------------------------------- dashboard */

let dashFilter = 'all';
async function viewDashboard() {
  const { cases, metrics: m } = await api('/api/cases');
  const filters = [['all', 'All', cases.length], ['pending', 'Waiting on vendor', m.pending], ['approved', 'Approved', m.approved], ['rejected', 'Rejected', m.rejected]];
  const shown = cases.filter((c) => dashFilter === 'all' || c.status === dashFilter);
  const maxReason = Math.max(1, ...m.top_reasons.map((r) => r.count));
  app.innerHTML = `
    <div class="page-head">
      <div><h1>Vendor onboarding</h1><p>${esc(META.buyer)} · every submission checked, decided and explained</p></div>
      <a class="btn primary" href="#/new">New case</a>
    </div>
    <div class="metrics">
      <div class="card metric"><div class="k">Cases decided</div><div class="v">${m.total}</div><div class="s">${m.auto_rate}% without a human</div></div>
      <div class="card metric"><div class="k">Approved first time</div><div class="v">${m.first_pass_rate}%</div><div class="s">${m.approved} approved in total</div></div>
      <div class="card metric"><div class="k">Waiting on vendor</div><div class="v">${m.pending}</div><div class="s">Told exactly what to fix</div></div>
      <div class="card metric"><div class="k">Rejected and escalated</div><div class="v">${m.rejected}</div><div class="s">Fraud signals found</div></div>
      <div class="card metric"><div class="k">Reviewer time saved</div><div class="v">${(m.minutes_saved / 60).toFixed(1)} h</div><div class="s">${META.manual_minutes} min per manual review · avg run ${ms(m.avg_ms)}</div></div>
    </div>
    <div class="split">
      <div class="card">
        <div class="tabs" role="tablist">${filters.map(([k, label, n]) => `<button role="tab" data-f="${k}" class="${dashFilter === k ? 'active' : ''}">${label}<span class="count">${n}</span></button>`).join('')}</div>
        ${shown.length ? `<table class="list"><thead><tr><th>Vendor</th><th>Status</th><th>Main reason</th><th>Received</th></tr></thead><tbody>
          ${shown.map((c) => `<tr class="click" data-id="${c.id}" tabindex="0">
            <td><div class="vname">${esc(c.vendor_name || 'Reading documents…')}</div><div class="vsub"><span class="mono">${c.id}</span> · ${esc(COUNTRY[c.country] || c.country || '')}${c.version > 1 ? ` · v${c.version}` : ''}${c.overridden ? ' · overridden' : ''}</div></td>
            <td>${pill(c.status)}</td>
            <td class="muted">${esc(c.top_reason || c.verdict || '')}</td>
            <td class="muted small" title="${esc(when(c.created_at))}">${ago(c.created_at)}</td></tr>`).join('')}
        </tbody></table>` : `<div class="empty">No cases here yet. <a href="#/new">Run a scenario</a> to add one.</div>`}
      </div>
      <div class="grid">
        <div class="card pad"><h2>Why cases get stuck</h2>
          ${m.top_reasons.length ? `<div class="bars">${m.top_reasons.map((r) => `<div class="bar-row"><div class="lbl"><span>${esc(r.reason)}</span><span>${r.count}</span></div><div class="bar"><i style="width:${Math.round(100 * r.count / maxReason)}%"></i></div></div>`).join('')}</div>`
            : '<p class="muted small">Nothing has been held up yet.</p>'}
        </div>
        <div class="card pad"><h2>Try it</h2><p class="muted small">Seven prepared submissions: a clean vendor, four edge cases and two extras.</p><a class="btn small" href="#/new">Open scenarios</a></div>
      </div>
    </div>`;
  $$('.tabs button').forEach((b) => b.addEventListener('click', () => { dashFilter = b.dataset.f; viewDashboard(); }));
  $$('tr.click').forEach((tr) => {
    const go = () => { location.hash = `#/case/${tr.dataset.id}`; };
    tr.addEventListener('click', go);
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  });
}

/* ---------------------------------------------------------------- new case */

function viewNew(tab) {
  const tabs = [['scenarios', 'Demo scenarios'], ['form', 'Vendor form'], ['documents', 'Drop documents']];
  app.innerHTML = `
    <div class="page-head"><div><h1>New case</h1><p>Run a prepared scenario, fill the vendor form, or drop in whatever the vendor emailed.</p></div></div>
    <div class="seg">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? 'active' : ''}">${l}</button>`).join('')}</div>
    <div id="new-body"></div>`;
  $$('.seg button').forEach((b) => b.addEventListener('click', () => { location.hash = `#/new?tab=${b.dataset.tab}`; }));
  ({ scenarios: newScenarios, form: newForm, documents: newDocuments }[tab] || newScenarios)();
}

async function launch(formData, button) {
  if (button) { button.disabled = true; button.textContent = 'Starting…'; }
  try {
    const r = await api('/api/cases', { method: 'POST', body: formData });
    location.hash = `#/run/${r.case_id}/${r.version}`;
  } catch (e) {
    if (button) { button.disabled = false; button.textContent = 'Run'; }
    toast(e.message);
  }
}

function newScenarios() {
  $('#new-body').innerHTML = `<div class="scen-grid">${META.scenarios.map((s) => `
    <div class="card scen">
      <div class="kind">${esc(s.kind)}</div><h3>${esc(s.title)}</h3><p>${esc(s.blurb)}</p>
      <div class="foot"><span class="small muted">Expect ${pill(s.expect)}${s.expect_after_fix ? ` then ${pill(s.expect_after_fix)}` : ''}</span>
      <button class="btn primary small" data-key="${s.key}">Run</button></div>
    </div>`).join('')}</div>`;
  $$('#new-body [data-key]').forEach((b) => b.addEventListener('click', () => {
    const fd = new FormData();
    fd.append('scenario', b.dataset.key);
    launch(fd, b);
  }));
}

function newForm() {
  const field = (path, label, opts = {}) => `<label class="field${opts.wide ? ' wide' : ''}">${label}
    <input type="${opts.type || 'text'}" data-path="${path}" class="${opts.mono ? 'mono' : ''}" placeholder="${esc(opts.ph || '')}" autocomplete="off"></label>`;
  const render = (country) => {
    const india = country === 'IN';
    const entities = india ? ['private_limited', 'public_limited', 'llp', 'partnership', 'proprietorship'] : ['corporation', 'llc'];
    $('#new-body').innerHTML = `<form id="vform" class="grid" novalidate>
      <div class="card pad"><h2>Company</h2><div class="form-grid">
        <label class="field">Country<select data-path="company.country"><option value="IN"${india ? ' selected' : ''}>India</option><option value="US"${india ? '' : ' selected'}>United States</option></select></label>
        <label class="field">Entity type<select data-path="company.entity_type">${entities.map((e) => `<option value="${e}">${esc(META.entity_labels[e])}</option>`).join('')}</select></label>
        ${field('company.legal_name', 'Legal name', { wide: true, ph: india ? 'Sharma Logistics Private Limited' : 'Brightline Analytics Inc' })}
        ${field('company.address.line1', 'Address', { wide: true, ph: india ? '14, 2nd Floor, MG Road' : '2100 Rio Grande Street, Suite 300' })}
        ${field('company.address.city', 'City', { ph: india ? 'Bengaluru' : 'Austin' })}
        ${field('company.address.state', 'State', { ph: india ? 'Karnataka' : 'Texas' })}
        ${field('company.address.pincode', india ? 'PIN code' : 'ZIP code', { mono: true, ph: india ? '560001' : '78705' })}
        ${field('company.website', 'Website (optional)', { ph: 'company.com' })}
        ${field('company.contact_name', 'Contact name', { ph: 'Anita Sharma' })}
        ${field('company.contact_email', 'Contact email', { type: 'email', ph: 'accounts@company.com' })}
      </div></div>
      <div class="two">
        <div class="card pad"><h2>Tax</h2><div class="form-grid">
          ${india ? field('tax.pan', 'PAN', { mono: true, ph: 'AAKCS4821M' }) + field('tax.gstin', 'GSTIN', { mono: true, ph: '29AAKCS4821M1ZS' })
                  : field('tax.ein', 'EIN', { mono: true, ph: '47-2918365', wide: true })}
        </div></div>
        <div class="card pad"><h2>Bank</h2><div class="form-grid">
          ${field('bank.account_holder', 'Account holder name', { wide: true })}
          ${field('bank.account_number', 'Account number', { mono: true })}
          ${india ? field('bank.ifsc', 'IFSC', { mono: true, ph: 'HDFC0001234' }) : field('bank.routing_number', 'ABA routing number', { mono: true, ph: '021000021' })}
        </div></div>
      </div>
      <div class="card pad"><h2>Documents</h2><div class="form-grid" style="grid-template-columns:repeat(3,minmax(0,1fr))">
        ${Object.keys(META.slots).map((slot) => `<label class="field">${esc(META.slot_ask[country][slot].replace(/^your /, '').replace(/^a /, ''))}<input type="file" name="${slot}" accept=".pdf,.png,.jpg,.jpeg,.webp"></label>`).join('')}
      </div><p class="small faint" style="margin:12px 0 0">Leave anything out and the process will catch it and ask the vendor for exactly that.</p></div>
      <div><button class="btn primary" type="submit">Submit and run checks</button></div>
    </form>`;
    $('[data-path="company.country"]').addEventListener('change', (e) => render(e.target.value));
    $('#vform').addEventListener('submit', (e) => {
      e.preventDefault();
      const payload = {};
      $$('#vform [data-path]').forEach((input) => {
        const value = input.value.trim();
        if (!value) return;
        const keys = input.dataset.path.split('.');
        let node = payload;
        keys.slice(0, -1).forEach((k) => { node = node[k] = node[k] || {}; });
        node[keys.at(-1)] = value;
      });
      const fd = new FormData();
      fd.append('channel', 'portal');
      fd.append('payload', JSON.stringify(payload));
      $$('#vform input[type=file]').forEach((input) => { if (input.files[0]) fd.append(input.name, input.files[0]); });
      launch(fd, $('#vform button[type=submit]'));
    });
  };
  render('IN');
}

function newDocuments() {
  let files = [];
  $('#new-body').innerHTML = `<div class="card pad" style="max-width:640px">
    <h2>The vendor emailed a pile of attachments</h2>
    <p class="muted">Drop them here in any order. The process works out which document is which, fills in the form from them, then runs every check.</p>
    <div class="drop" id="drop" tabindex="0" role="button">Drop PDFs or images here, or click to choose<input type="file" id="pick" multiple accept=".pdf,.png,.jpg,.jpeg,.webp" hidden></div>
    <div class="file-list" id="files"></div>
    <label class="field" style="margin-top:14px">Vendor's email address (to send the result to)<input type="email" id="demail" placeholder="accounts@vendor.com"></label>
    <div style="margin-top:16px"><button class="btn primary" id="dgo">Run checks</button></div>
  </div>`;
  const draw = () => { $('#files').innerHTML = files.map((f) => `<div><span>${esc(f.name)}</span><span class="faint">${Math.round(f.size / 1024)} KB</span></div>`).join(''); };
  const add = (list) => { files = files.concat([...list]).slice(0, 6); draw(); };
  const drop = $('#drop');
  drop.addEventListener('click', () => $('#pick').click());
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#pick').click(); });
  $('#pick').addEventListener('change', (e) => add(e.target.files));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); add(e.dataTransfer.files); });
  $('#dgo').addEventListener('click', () => {
    if (!files.length) return toast('Add at least one document first');
    const fd = new FormData();
    fd.append('channel', 'documents_only');
    fd.append('payload', JSON.stringify({ company: { contact_email: $('#demail').value.trim() } }));
    files.forEach((f) => fd.append('files', f));
    launch(fd, $('#dgo'));
  });
}

/* ----------------------------------------------------- shared case widgets */

const docTitle = (slot) => `${META.slots[slot][0].toUpperCase()}${META.slots[slot].slice(1)}`;
const modelName = (m) => m.split('-').filter((w) => !['latest', 'versatile', 'preview'].includes(w))
  .map((w) => (/^\d+b$/.test(w) ? w.toUpperCase() : /^[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w)).join(' ');
function readerLabel(reader) {
  if (!reader || !reader.startsWith('ai:')) return 'the rule-based reader (no AI)';
  const m = reader.match(/^ai:([^+ ]+)/);
  return `AI (${modelName(m ? m[1] : 'model')})${reader.includes('(cached)') ? ' · reused from an earlier read' : ''}`;
}
function writerLabel(by) {
  const m = (by || '').match(/^ai:(\S+)/);
  return m ? `AI (${modelName(m[1])}), checked against the rule-built draft` : (by || '').replace(/^template/, 'a fixed template');
}
const bindDocs = (root, id, version) => $$('[data-doc]', root).forEach((b) => b.addEventListener('click', () => openDoc(id, version, b.dataset.doc, b.dataset.hl, docTitle(b.dataset.doc))));

/* The consistency matrix: every fact (rows) as each source states it (columns).
   animate: cells appear source by source, as they are checked in the live run. */
function matrixBlock(matrix, animate) {
  const nRows = matrix.rows.length;
  const pop = (ci, ri) => (animate ? ` pop" style="animation-delay:${(ci * nRows + ri) * 45}ms` : '');
  return `
    <div class="feed-head"><div><h2 style="margin:0">Consistency matrix</h2><div class="small muted">Every fact, as each source states it. Click a cell to see it in the document.</div></div></div>
    <div class="matrix-wrap"><table class="matrix"><thead><tr><th></th>${matrix.columns.map((col) => `<th>${esc(col.label)}${col.available ? '' : ' <span class="faint">(not usable)</span>'}</th>`).join('')}</tr></thead><tbody>
      ${matrix.rows.map((row, ri) => `<tr><td class="rowh">${esc(row.label)}</td>${matrix.columns.map((col, ci) => {
        const cell = row.cells[col.key];
        if (cell.status === 'na') return `<td><span class="cell na${pop(ci, ri)}">—</span></td>`;
        if (col.key === 'form') return `<td><span class="cell ref${pop(ci, ri)}">${esc(cell.value)}</span></td>`;
        return `<td><button class="cell ${cell.status}${pop(ci, ri)}" data-doc="${col.key}" data-hl="${esc(cell.hl)}">${esc(cell.value)}</button></td>`;
      }).join('')}</tr>`).join('')}
    </tbody></table></div>
    <div class="legend"><span><i style="background:var(--ok-bg);border:1px solid var(--ok-line)"></i>Agrees with the form</span><span><i style="background:var(--warn-bg);border:1px solid var(--warn-line)"></i>Disagrees, vendor can fix</span><span><i style="background:var(--bad-bg);border:1px solid var(--bad-line)"></i>Fraud signal</span><span><i style="background:var(--note-bg);border:1px solid var(--note-line)"></i>Differs, allowed</span><span>— Not on this document</span></div>`;
}

/* Findings that need attention first, passed checks folded away. */
function findingsBlock(findings) {
  const group = (sev) => findings.filter((f) => f.severity === sev);
  const issues = ['block', 'fix', 'note'].filter((s) => group(s).length);
  return `${issues.length ? issues.map((s) => `<div class="group-title ${s}">${SEV[s][0]} <span class="faint">${group(s).length}</span></div>${group(s).map((f) => findingRow(f, true)).join('')}`).join('')
      : '<div class="group-title pass">Nothing to fix, nothing to flag</div>'}
    <details class="passed"><summary>${group('pass').length} checks passed.</summary>${group('pass').map((f) => findingRow(f, true)).join('')}</details>`;
}

/* ---------------------------------------------------------------- live run */

function findingRow(f, ctx) {
  const view = ctx && f.source && f.source.slot ? `<button class="btn small" data-doc="${esc(f.source.slot)}" data-hl="${esc(f.source.value || '')}">View in document</button>` : '';
  return `<div class="finding ${f.severity}">
    <div class="ico" aria-hidden="true">${SEV[f.severity][1]}</div>
    <div><div class="t">${esc(f.title)}</div>${f.detail ? `<div class="d">${esc(f.detail)}</div>` : ''}</div>
    <div class="meta">${view}${f.internal_only ? '<span class="tag" title="Never shown to the vendor">Internal only</span>' : ''}<span class="tag mono">${esc(f.check)}</span></div>
  </div>`;
}

async function viewRun(id, version) {
  app.innerHTML = `
    <div class="crumbs"><a href="#/">Dashboard</a><span>/</span><span class="mono">${esc(id)}</span><span>/</span><span>Live run${version > 1 ? ` · version ${version}` : ''}</span></div>
    <div class="card banner running" id="banner"><div><span class="pill running">Running</span><div class="verdict" id="run-title">Checking this submission…</div></div><div id="banner-actions"></div></div>
    <div class="run-grid">
      <div class="card" style="padding:8px 0">
        ${META.stages.map((s, i) => `<div class="stage" id="st-${s.key}"><div class="dot">${i + 1}</div><div><div class="name">${esc(s.label)}</div><div class="sum"></div></div><div class="ms"></div></div>`).join('')}
        <div class="small faint" style="padding:10px 16px 8px;border-top:1px solid var(--line);margin-top:6px">Each stage is held on screen briefly so you can follow it. The times shown are real processing time.</div>
      </div>
      <div class="run-right">
        <div class="card" id="mx-card" hidden></div>
        <div class="card feed">
          <div class="feed-head"><h2 style="margin:0">Findings as they happen</h2><span class="small muted" id="counts">Waiting…</span></div>
          <div id="feed"><div class="empty">Starting…</div></div>
        </div>
      </div>
    </div>`;
  const counts = { pass: 0, note: 0, fix: 0, block: 0 };
  const seen = [];
  let first = true;
  let decided = null;
  api(`/api/cases/${id}`).then((c) => { if (c.vendor_name && !decided) $('#run-title').textContent = `Checking ${c.vendor_name}…`; }).catch(() => {});

  stream = new EventSource(`/api/cases/${id}/v/${version}/events`);
  stream.onmessage = (msg) => {
    const e = JSON.parse(msg.data);
    if (e.type === 'stage_start') {
      $(`#st-${e.stage}`).classList.add('running');
    } else if (e.type === 'finding') {
      if (first) { $('#feed').innerHTML = ''; first = false; }
      counts[e.finding.severity] += 1;
      seen.push(e.finding);
      $('#feed').insertAdjacentHTML('afterbegin', findingRow(e.finding));
      const issues = counts.fix + counts.block;
      $('#counts').textContent = `${counts.pass} passed · ${issues} issue${issues === 1 ? '' : 's'} · ${counts.note} note${counts.note === 1 ? '' : 's'}`;
    } else if (e.type === 'stage_done') {
      const el = $(`#st-${e.key}`);
      el.classList.remove('running');
      el.classList.add('done', e.worst);
      $('.dot', el).textContent = e.worst === 'pass' ? '✓' : e.worst === 'note' ? 'i' : '!';
      $('.sum', el).textContent = e.summary;
      $('.ms', el).textContent = ms(e.ms);
    } else if (e.type === 'matrix') {
      const card = $('#mx-card');
      card.hidden = false;
      card.innerHTML = matrixBlock(e.matrix, true);
      bindDocs(card, id, version);
    } else if (e.type === 'decision') {
      decided = e;
    } else if (e.type === 'saved') {
      stream.close();
      const b = $('#banner');
      const openCase = `#/case/${id}?v=${version}`;
      const banner = (actions) => {
        b.className = `card banner ${decided.status}`;
        b.innerHTML = `<div>${pill(decided.status, true)}<div class="verdict">${esc(decided.verdict)}</div>
          <div class="small muted" style="margin-top:4px">Decided in ${ms(e.work_ms)} of processing${e.replay ? ' · replay of a finished run' : ''}</div></div>
          <div class="banner-actions">${actions}</div>`;
      };
      banner(`<a class="btn primary" href="${openCase}">Open case</a>`);
      // Once it's over, show what needs attention and fold the passed checks away.
      const issues = seen.some((f) => f.severity !== 'pass');
      $('#feed').innerHTML = findingsBlock(seen);
      bindDocs($('#feed'), id, version);
      $('.feed .feed-head h2').textContent = issues ? 'What needs attention' : 'Findings';
      // Offer the obvious next step, but only if this run is still the case's current decision.
      api(`/api/cases/${id}`).then((c) => {
        if (c.version !== version || c.status !== decided.status) return;
        if (c.status === 'pending') {
          banner(`<a class="btn primary" href="#/fix/${id}?t=${c.token}">Open the vendor's fix page</a><a class="btn" href="${openCase}">Open case</a>`);
        } else if (c.status === 'rejected') {
          banner(`<a class="btn primary" href="#/case/${id}?v=${version}&tab=messages">Read the internal note</a><a class="btn" href="${openCase}">Open case</a>`);
        }
      }).catch(() => {});
    } else if (e.type === 'error') {
      stream.close();
      const b = $('#banner');
      b.className = 'card banner rejected';
      b.innerHTML = `<div>${pill('error', true)}<div class="verdict">The run stopped: ${esc(e.message)}</div></div><div><a class="btn" href="#/new">Back</a></div>`;
    }
  };
  stream.onerror = () => { if (stream && stream.readyState === 2) toast('Lost the live connection'); };
}

/* -------------------------------------------------------------------- case */

async function viewCase(id, params) {
  const c = await api(`/api/cases/${id}`);
  if (!c.versions.length) {
    location.hash = `#/run/${id}/${Math.max(1, c.version)}`;
    return;
  }
  const version = Number(params.get('v')) || c.versions.at(-1).version;
  const ver = c.versions.find((v) => v.version === version) || c.versions.at(-1);
  const tab = params.get('tab') || 'overview';
  const r = ver.result;
  const latest = ver.version === c.versions.at(-1).version;
  const status = latest ? c.status : r.status;
  const tabs = [['overview', 'Overview'], ['documents', 'Documents'], ['messages', 'Messages'], ['history', 'History and audit']];
  const link = (t, v = ver.version) => `#/case/${id}?v=${v}&tab=${t}`;

  app.innerHTML = `
    <div class="crumbs"><a href="#/">Dashboard</a><span>/</span><span class="mono">${esc(id)}</span></div>
    <div class="card case-head">
      <div class="top">
        <div>
          <h1>${esc(c.vendor_name)}</h1>
          <div class="kv"><span class="mono">${esc(id)}</span><span class="sep">·</span><span>${esc(COUNTRY[r.country] || r.country)}</span><span class="sep">·</span><span>${esc(CHANNEL[r.channel] || r.channel)}</span>
            <span class="sep">·</span><span>Received ${esc(when(c.created_at))}</span>${c.scenario_title ? `<span class="sep">·</span><span class="tag">Demo: ${esc(c.scenario_title)}</span>` : ''}
            ${c.versions.length > 1 ? `<span class="sep">·</span><span class="vers">${c.versions.map((v) => `<button class="${v.version === ver.version ? 'active' : ''}" data-v="${v.version}">v${v.version}</button>`).join('')}</span>` : ''}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">${pill(status, true)}</div>
      </div>
      <p class="verdict">${esc(r.verdict)}</p>
      ${latest && c.override ? `<div class="override-note"><b>Overridden by ${esc(c.override.reviewer)}</b> (automatic decision was ${esc(c.auto_status)}): ${esc(c.override.reason)}</div>` : ''}
      ${!latest ? `<div class="override-note">You're looking at version ${ver.version}. <a href="${link(tab, c.versions.at(-1).version)}">Go to the latest version</a></div>` : ''}
      <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
        ${latest && c.status === 'pending' ? `<a class="btn primary" href="#/fix/${id}?t=${c.token}">Open the vendor's fix page</a><button class="btn" id="copy-link">Copy fix link</button>` : ''}
        ${latest ? '<button class="btn" id="override-btn">Override decision</button>' : ''}
        <a class="btn" href="#/run/${id}/${ver.version}">Replay run</a>
      </div>
    </div>
    <div class="card"><div class="tabs">${tabs.map(([k, l]) => `<button data-t="${k}" class="${tab === k ? 'active' : ''}">${l}</button>`).join('')}</div><div id="tab-body"></div></div>`;
  $$('.tabs button').forEach((b) => b.addEventListener('click', () => { location.hash = link(b.dataset.t); }));
  $$('.vers button').forEach((b) => b.addEventListener('click', () => { location.hash = link(tab, b.dataset.v); }));
  const copy = $('#copy-link');
  if (copy) copy.addEventListener('click', () => navigator.clipboard.writeText(fixLink(id, c.token)).then(() => toast('Fix link copied')));
  const ob = $('#override-btn');
  if (ob) ob.addEventListener('click', () => overrideDialog(c));

  const body = $('#tab-body');

  if (tab === 'overview') {
    body.innerHTML = `${matrixBlock(r.matrix, false)}
      <div class="findings-head"><h2 style="margin:0">Findings</h2><span class="small muted">${r.findings.length} checks</span></div>
      ${findingsBlock(r.findings)}`;
    bindDocs(body, id, ver.version);
  } else if (tab === 'documents') {
    const slots = Object.keys(META.slots);
    body.innerHTML = `<div class="doc-grid" style="padding:16px">${slots.map((slot) => {
      const d = r.documents[slot];
      if (!d) return `<div class="card doc"><div class="body"><h3>${esc(docTitle(slot))}</h3><p class="muted small">Nothing was uploaded.</p></div></div>`;
      const usable = r.matrix.columns.find((col) => col.key === slot).available;
      return `<div class="card doc">
        <img class="thumb" loading="lazy" alt="${esc(docTitle(slot))}" src="/api/cases/${id}/v/${ver.version}/doc/${slot}.png" data-doc="${slot}" data-hl="">
        <div class="body"><h3>${esc(docTitle(slot))}</h3>
          <div class="small ${usable ? 'muted' : ''}" ${usable ? '' : 'style="color:var(--warn)"'}>Detected: ${esc(META.doc_labels[d.doc_type] || d.doc_type)} · ${Math.round(d.confidence * 100)}% confident</div>
          <div class="small faint">Read by ${esc(readerLabel(d.reader))} · file ${esc(d.original_name)}</div>
          ${d.notes.length ? `<div class="small muted" style="margin-top:6px">${d.notes.map(esc).join(' ')}</div>` : ''}
          <dl class="fields">${Object.entries(d.fields).map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
        </div></div>`;
    }).join('')}</div>`;
    bindDocs(body, id, ver.version);
  } else if (tab === 'messages') {
    const text = withLink(r.vendor_message.text);
    body.innerHTML = `<div class="two" style="padding:16px">
      <div class="card"><div class="msg-head"><div><b>To the vendor</b><div class="small faint">Written by ${esc(writerLabel(r.vendor_message.written_by))} · not sent automatically</div></div><button class="btn small" id="copy-msg">Copy</button></div><pre class="msg">${esc(text)}</pre></div>
      <div class="card"><div class="msg-head"><div><b>Internal note</b><div class="small faint">For the reviewer and the audit file · never sent to the vendor</div></div></div><pre class="msg">${esc(r.internal_note)}</pre></div>
    </div>`;
    $('#copy-msg').addEventListener('click', () => navigator.clipboard.writeText(text).then(() => toast('Message copied')));
  } else {
    body.innerHTML = `
      <div class="group-title">Versions</div>
      <div class="timeline">${c.versions.map((v) => `<div class="tl"><span class="when">${esc(when(v.created_at))}</span><span>${pill(v.result.status)}</span><span><a href="${link('overview', v.version)}">Version ${v.version}</a> · ${esc(v.result.top_reason)}</span></div>`).join('')}</div>
      <div class="group-title" style="border-top:1px solid var(--line)">Audit log</div>
      <div class="timeline">${c.audit.map((a) => `<div class="tl"><span class="when">${esc(when(a.at))}</span><span class="muted">${esc(a.actor)}</span><span><b style="font-weight:500">${esc(a.action)}</b>${a.detail ? `<div class="small muted">${esc(a.detail)}</div>` : ''}</span></div>`).join('')}</div>
      <div class="group-title" style="border-top:1px solid var(--line)">Stage record for version ${ver.version}</div>
      <div class="timeline">${r.stages.map((s) => `<div class="tl"><span>${esc(s.label)}</span><span class="mono faint">${ms(s.ms)}</span><span class="muted">${esc(s.summary)}</span></div>`).join('')}</div>`;
  }
}

function overrideDialog(c) {
  openModal(`
    <div class="modal-head"><b>Override the decision</b><button class="x" data-close aria-label="Close">×</button></div>
    <div class="modal-body">
      <p class="muted small">The automatic decision is ${esc(c.auto_status)}. An override is recorded in the audit log with your name and reason.</p>
      <label class="field">New status<select id="o-status">${['approved', 'pending', 'rejected'].filter((s) => s !== c.status).map((s) => `<option value="${s}">${STATUS_LABEL[s]}</option>`).join('')}</select></label>
      <label class="field" style="margin-top:12px">Your name<input type="text" id="o-name" placeholder="Priya (Procurement)"></label>
      <label class="field" style="margin-top:12px">Reason<textarea id="o-reason" rows="3" placeholder="Confirmed the Karnataka registration with the vendor's CFO by phone."></textarea></label>
      <div class="error-text" id="o-error"></div>
    </div>
    <div class="modal-foot"><button class="btn" data-close>Cancel</button><button class="btn primary" id="o-save">Save override</button></div>`, true);
  $('#o-save').addEventListener('click', async () => {
    try {
      await api(`/api/cases/${c.id}/override`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: $('#o-status').value, reviewer: $('#o-name').value, reason: $('#o-reason').value }) });
      closeModal();
      toast('Decision overridden');
      route();
    } catch (e) { $('#o-error').textContent = e.message; }
  });
}

/* --------------------------------------------------------- vendor fix page */

async function viewFix(id, token) {
  const f = await api(`/api/fix/${id}?t=${encodeURIComponent(token)}`);
  const top = `<div class="vendor-top"><span class="brand-mark" style="color:var(--accent);display:flex"><svg viewBox="0 0 32 32" width="22" height="22"><rect width="32" height="32" rx="8" fill="currentColor"/><path d="M9 16.5l4.5 4.5L23 11.5" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></span>${esc(f.buyer)} · Vendor onboarding</div>`;
  if (f.status !== 'pending' || !f.asks.length) {
    const text = { approved: ["You're all set", 'Your vendor registration is approved. There is nothing more to do.'],
      running: ['We are checking your details', 'This takes a few seconds. Refresh the page to see the result.'] }[f.status]
      || ['Nothing is needed from you right now', 'Our team will contact you if we need anything else.'];
    app.innerHTML = `${top}<div class="card pad"><h1>${text[0]}</h1><p class="muted" style="margin-top:8px">${text[1]}</p></div>
      <div class="demo-box"><span>Demo only: this is the page the vendor sees.</span><a class="btn small" href="#/case/${id}">Back to the reviewer's view</a></div>`;
    return;
  }
  const n = f.asks.length;
  app.innerHTML = `${top}
    <div class="card pad">
      <h1>${f.contact_name ? `Hi ${esc(f.contact_name.split(' ')[0])}, ` : ''}${n === 1 ? 'one thing is' : `${n} things are`} needed to finish your registration</h1>
      <p class="muted" style="margin-top:6px">Everything else you sent for ${esc(f.vendor_name)} is fine. You only need to fix what's below.</p>
      <form id="fix-form">
        ${f.asks.map((a, i) => `<div class="ask"><div class="n">${i + 1}</div><div>
          <b style="font-weight:500">${esc(a.label)}</b><div class="why">${esc(a.why)}</div>
          ${a.kind === 'document' ? `<input type="file" name="${esc(a.slot)}" accept=".pdf,.png,.jpg,.jpeg,.webp" aria-label="${esc(a.label)}">`
            : `<input type="text" class="mono" data-path="${esc(a.path)}" value="${esc(a.current)}" aria-label="${esc(a.label)}">`}
        </div></div>`).join('')}
        <div class="error-text" id="fix-error"></div>
        <div style="margin-top:16px"><button class="btn primary" type="submit">Send and re-check</button></div>
      </form>
    </div>
    <div class="demo-box"><span>Demo only: ${f.demo_fix_available ? 'send the corrected details prepared for this scenario.' : 'this is the page the vendor sees.'}</span>
      <span style="display:flex;gap:8px">${f.demo_fix_available ? '<button class="btn small" id="demo-fix">Use the prepared fix</button>' : ''}<a class="btn small" href="#/case/${id}">Reviewer's view</a></span></div>`;
  const send = async (fd) => {
    try {
      fd.append('t', token);
      const r = await api(`/api/cases/${id}/resubmit`, { method: 'POST', body: fd });
      app.innerHTML = `${top}<div class="card pad"><h1>Thanks, we've got it</h1><p class="muted" style="margin-top:8px">We're re-checking your details now and will email you the result.</p></div>
        <div class="demo-box"><span>Demo only: switch to the reviewer's side to watch version ${r.version} being checked.</span><a class="btn primary small" href="#/run/${id}/${r.version}">Watch the re-run</a></div>`;
    } catch (e) { $('#fix-error').textContent = e.message; }
  };
  $('#fix-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const fields = {};
    $$('#fix-form [data-path]').forEach((input) => { if (input.value.trim() && input.value.trim() !== input.defaultValue) fields[input.dataset.path] = input.value.trim(); });
    const fd = new FormData();
    fd.append('fields', JSON.stringify(fields));
    $$('#fix-form input[type=file]').forEach((input) => { if (input.files[0]) fd.append(input.name, input.files[0]); });
    send(fd);
  });
  const demo = $('#demo-fix');
  if (demo) demo.addEventListener('click', () => { const fd = new FormData(); fd.append('demo_fix', '1'); send(fd); });
}

/* -------------------------------------------------------------- test suite */

function viewSuite() {
  app.innerHTML = `
    <div class="page-head"><div><h1>Test suite</h1><p>Runs every scenario through the real pipeline and compares the decision with what's expected. Nothing is saved.</p></div>
      <button class="btn primary" id="suite-go">Run all scenarios</button></div>
    <div class="card" id="suite-out"><div class="empty">Not run yet.</div></div>`;
  $('#suite-go').addEventListener('click', async (e) => {
    e.target.disabled = true;
    $('#suite-out').innerHTML = '<div class="empty">Running…</div>';
    try {
      const s = await api('/api/suite/run', { method: 'POST' });
      $('#suite-out').innerHTML = `<div class="feed-head"><h2 style="margin:0">${s.passed} of ${s.total} scenarios behave as designed</h2><span class="small muted">Whole suite in ${ms(s.ms)} · rule-based reader, so results are identical every time</span></div>
        <table class="list"><thead><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>After the vendor's fix</th><th>Main reason</th><th>Time</th><th></th></tr></thead><tbody>
        ${s.rows.map((r) => `<tr><td><div class="vname">${esc(r.title)}</div><div class="vsub">${esc(r.kind)} · ${r.checks} checks</div></td>
          <td>${pill(r.expected)}</td><td>${pill(r.actual)}</td>
          <td>${r.expected_after_fix ? `${pill(r.actual_after_fix)}` : '<span class="faint">—</span>'}</td>
          <td class="muted small">${esc(r.reason)}</td><td class="mono faint">${ms(r.ms)}</td>
          <td><span class="pill ${r.ok ? 'approved' : 'rejected'}">${r.ok ? 'Pass' : 'Fail'}</span></td></tr>`).join('')}
        </tbody></table>`;
    } catch (err) { $('#suite-out').innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
    e.target.disabled = false;
  });
}

/* ------------------------------------------------------------ how it works */

function viewHow() {
  const steps = [
    ['Intake', 'Form and documents, or documents alone, get a case ID.', 'code'],
    ['Read documents', 'What is each file, and what does it say?', 'ai'],
    ['Completeness', 'Every required field and document is present.', 'code'],
    ['Validate formats', 'Check digits and patterns for each country.', 'code'],
    ['Cross-check', 'Every fact compared across form and documents.', 'code'],
    ['Risk screen', 'Vendor master, blocked list, fraud patterns.', 'code'],
    ['Decide', 'Fixed rules turn findings into a status.', 'code'],
    ['Draft messages', 'One for the vendor, one for the reviewer.', 'ai'],
  ];
  const ai = META.ai;
  app.innerHTML = `
    <div class="page-head"><div><h1>How it works</h1><p>The process, the rules behind every decision, and the assumptions made.</p></div></div>
    <div class="card pad section"><h2>The process</h2>
      <div class="flow">${steps.map(([s, w, who], i) => `<div class="step"><div class="n">${String(i + 1).padStart(2, '0')}</div><div class="s">${s}</div><div class="w">${w}</div><span class="who ${who}">${who === 'ai' ? 'AI, with rule fallback' : 'Plain code'}</span></div>`).join('')}</div>
    </div>
    <div class="two section">
      <div class="card pad"><h2>AI reads. Rules decide.</h2><ul class="clean">
        <li><b>AI is used for</b> working out what a document is, reading scans and odd layouts, and wording the email to the vendor.</li>
        <li><b>Plain code is used for</b> every check that has one right answer, and for the decision itself. The same input always gives the same result, and every result can be explained.</li>
        <li><b>Guardrail:</b> an ID the AI reads must literally appear in the document's text, or it is discarded.</li>
        <li><b>If the AI is unavailable</b> the rule-based reader takes over, so a rate limit can't break a run.</li>
        <li><b>Right now:</b> ${ai.enabled ? `AI reader on (${esc(ai.provider)}, ${esc(ai.model)}).` : 'no AI key is set, so the rule-based reader is doing the reading.'} <button class="link-btn" id="probe" style="color:var(--accent)">Test the AI connection</button> <span id="probe-out" class="small"></span></li>
      </ul></div>
      <div class="card pad"><h2>How a status is chosen</h2><ul class="clean">
        <li><b>Rejected</b> only on evidence of fraud or a false identity: a bank account in a person's name, an account another vendor already uses, a tax ID that belongs to someone else, a blocked vendor.</li>
        <li><b>Pending</b> for anything the vendor could plausibly fix. They are told exactly what, and get a link that asks for only that.</li>
        <li><b>Approved</b> when nothing needs fixing. Notes are still shown to the reviewer.</li>
        <li><b>Two messages:</b> the vendor gets plain instructions; the reviewer gets the evidence. A rejected vendor is never told why, so a fraudster isn't coached.</li>
        <li><b>People stay in charge:</b> a reviewer can override any decision, with a reason, and it lands in the audit log.</li>
      </ul></div>
    </div>
    <div class="card section"><div class="feed-head"><h2 style="margin:0">Rule catalogue</h2><span class="small muted">${META.checks.length} checks</span></div>
      <table class="list"><thead><tr><th>ID</th><th>Check</th><th>Stage</th><th>If it fails</th></tr></thead><tbody>
      ${META.checks.map((c) => `<tr><td class="mono">${c.id}</td><td>${esc(c.title)}</td><td class="muted">${esc((META.stages.find((s) => s.key === c.stage) || {}).label || c.stage)}</td><td class="muted">${esc({ fix: 'Pending', block: 'Rejected', note: 'Note only', 'fix / block': 'Pending or rejected', 'note / fix': 'Note or pending' }[c.severity] || c.severity)}</td></tr>`).join('')}
      </tbody></table></div>
    <div class="two section">
      <div class="card pad"><h2>Assumptions</h2><ul class="clean">
        <li>The buyer is an Indian mid-size company onboarding mostly Indian vendors, with some from the US. Each country has its own rulebook.</li>
        <li>A submission is a form plus three documents: tax registration, bank proof and incorporation certificate. Some vendors skip the form and just email documents.</li>
        <li>There is no live call to GSTN, MCA, the IRS or the bank. Check digits and cross-document consistency stand in for registry lookups.</li>
        <li>An existing vendor master and a blocked-vendor list are available to screen against.</li>
        <li>Emails are drafted, not sent. Sending is one integration away.</li>
        <li>All companies, IDs and accounts here are fictional. IDs are synthetic but pass their real checksums.</li>
      </ul></div>
      <div class="card pad"><h2>What I'd build next</h2><ul class="clean">
        <li><b>Registry verification:</b> GSTN and MCA lookups, and a one-rupee test deposit to confirm the bank account holder.</li>
        <li><b>Policy screen:</b> let each customer tune severities and thresholds without code.</li>
        <li><b>Bank-change requests</b> for existing vendors, the most common real-world fraud route.</li>
        <li><b>Reminders</b> when a vendor hasn't responded in a few days.</li>
        <li><b>Real sanctions screening</b> and email sending.</li>
      </ul></div>
    </div>
    <div class="card section"><div class="feed-head"><h2 style="margin:0">Vendor master used for screening</h2><span class="small muted">${META.vendors.length} existing vendors · ${META.blocklist.length} blocked names</span></div>
      <table class="list"><thead><tr><th>Code</th><th>Vendor</th><th>Tax ID</th><th>Bank account</th></tr></thead><tbody>
      ${META.vendors.map((v) => `<tr><td class="mono">${esc(v.code)}</td><td>${esc(v.legal_name)}</td><td class="mono">${esc(v.tax_id)}</td><td class="mono">${esc(v.bank_account)} · ${esc(v.bank_code)}</td></tr>`).join('')}
      </tbody></table></div>`;
  $('#probe').addEventListener('click', async () => {
    $('#probe-out').textContent = 'Testing…';
    const r = await api('/api/ai/probe');
    $('#probe-out').textContent = r.ok ? `Working (${r.model}).` : `Not available: ${r.error}`;
  });
}

route();
