import {h, render, send, fmtDate} from '../../lib/dom.js';
import {trashCleanupPage} from '../../lib/accident-trash.js';
export async function accidentsTab(root, {api, toast}, category = 'accidents') {
  const config = {
    accidents: {title: 'Likely accidents', noun: 'burst', description: 'Review runs of dark, low-detail or possibly blurry photos taken seconds apart. Intentional night shots and soft backgrounds can also appear here.'},
    temporary: {title: 'Temporary photos', noun: 'group', description: 'Find possible delivery updates, parking details, shopping references and screenshots using local text recognition. Documents and receipts are labeled separately. Photos are grouped by category and month for review. A match does not mean the information is no longer useful.'},
    attempts: {title: 'Repeated attempts', noun: 'group', description: 'Compare photos with similar appearance taken within two minutes. Keep your favorite expressions and framing; the suggested keeper uses preview sharpness, not personal significance.'},
  }[category];
  const header = document.querySelector('.topbar');
  const headerObserver = new ResizeObserver(() => root.style.setProperty('--cleanup-header-height', `${header?.offsetHeight || 0}px`));
  if (header) headerObserver.observe(header);
  const jobName = category === 'accidents' ? 'accident-analysis' : `${category}-analysis`;
  let disposed = false, timer, jobId, after = 0, busy = false;
  const status = h('p', {role: 'status', 'aria-live': 'polite'}, 'Uses cached previews of photos you own. No CLIP model needed.');
  const sensitivity = h('select', {id: 'accident-sensitivity'},
    h('option', {value: 'conservative'}, 'Conservative'),
    h('option', {value: 'broad', selected: true}, 'Broad — recommended'),
    h('option', {value: 'very-broad'}, 'Very broad'));
  const filter = h('select', {id: 'temporary-filter', onchange: () => load().catch(showError)},
    h('option', {value: ''}, 'All categories'),
    ...Object.entries({delivery: 'Delivery / orders', parking: 'Parking / travel', shopping: 'Shopping / menus', screenshots: 'Screenshots', documents: 'Documents / receipts', text: 'Other text-heavy photos'}).map(([value, name]) => h('option', {value}, name)));
  const list = h('div.groups');
  const operations = h('div');
  const buttons = new Set();
  const selectionState = new Map();
  const selectionCount = h('span', {role: 'status'});
  const trashPage = h('button.danger', {disabled: true, onclick: trashSelected}, 'Preview selected');
  let opsExpanded = false, analyzing = false;
  const dryRun = h('input', {type: 'checkbox', checked: true, onchange: updateButtons});
  let runningOp = null, opsTimer;
  function updateButtons() {
    const count = [...selectionState.values()].reduce((n, entry) => n + entry.selected.size, 0);
    const locked = busy || !!runningOp || analyzing;
    selectionCount.textContent = `${count} photos selected on this page`;
    trashPage.textContent = `${dryRun.checked ? 'Preview' : 'Trash'} ${count} selected`;
    trashPage.disabled = !count || locked;
    dryRun.disabled = locked;
    filter.disabled = locked;
    run.disabled = locked;
    next.disabled = locked;
    for (const update of buttons) update();
  }
  async function trashSelected() {
    if (busy || runningOp || analyzing) return;
    const selections = [...selectionState.values()].filter(e => e.selected.size).map(e => ({group: e.group, mediaIds: [...e.selected]}));
    busy = true; updateButtons();
    try {
      const isDry = dryRun.checked;
      const op = await trashCleanupPage({api, send, confirm, category, selections, dryRun: isDry});
      if (!op) return;
      if (!isDry) {
        for (const {group} of selections) {
          const entry = selectionState.get(group.id);
          entry.section.remove(); buttons.delete(entry.update); selectionState.delete(group.id);
        }
        dryRun.checked = true;
      }
      execute(op.id);
      if (!selectionState.size) await load();
    } catch (e) {showError(e);}
    finally {busy = false; updateButtons();}
  }
  async function execute(opId) {
    if (runningOp) return;
    runningOp = opId; updateButtons();
    status.textContent = 'Running selected operation…';
    refreshOps().catch(showError);
    try {
      const r = await send({type: 'PC_RUN_OP', opId});
      if (!r?.ok) throw new Error(r?.error || 'Operation stopped; check its status below.');
      status.textContent = 'Operation finished. See the result below.';
    } catch (e) { showError(e); }
    finally {runningOp = null; updateButtons(); await refreshOps().catch(showError);}
  }
  async function refreshOps() {
    if (disposed) return;
    const {items = []} = await api.listOperations();
    if (disposed) return;
    const relevant = items.filter(o => ['Accident', 'Temporary photos', 'Repeated attempts'].some(name => (o.note || '').includes(name)) || (o.note || '').startsWith('Undo of operation ') || o.id === runningOp); 
    // Include recent operations with undo so recovery is available without switching tabs.
    const visible = [...new Map([...relevant, ...items.filter(o => !o.dry_run && o.done_count > 0).slice(0, 3)].map(o => [o.id, o])).values()].sort((a, b) => Number(['pending', 'running', 'paused', 'cancelled'].includes(b.status)) - Number(['pending', 'running', 'paused', 'cancelled'].includes(a.status))).slice(0, 10);
    const active = relevant.filter(o => ['pending', 'running', 'paused'].includes(o.status));
    const last = items.find(o => o.op === 'trash' && !o.dry_run && o.done_count > 0 && !['pending', 'running', 'paused'].includes(o.status));
    const undo = async o => {
      if (!confirm(`Restore ${o.done_count} photos from operation ${o.id}?`)) return;
      try {const inverse = await api.undoOperation(o.id); if (['paused', 'cancelled'].includes(inverse.status)) await api.resumeOperation(inverse.id); execute(inverse.id);} catch(e) {showError(e);}
    };
    render(operations, visible.length ? h('section.card', h('div.row',
      h('span', `Operations · ${active.length ? `${active.length} active or needing attention` : 'nothing running'}`),
      h('button.small', {onclick: () => {opsExpanded = !opsExpanded; refreshOps().catch(showError);}}, opsExpanded ? 'Hide details' : 'Show details'),
      last && h('button.small', {disabled: !!runningOp || busy, onclick: () => undo(last)}, `Undo last (${last.done_count})`)),
      opsExpanded ? h('div.operation-history', visible.map(o =>
      h('div.row', h('span', `${o.note || 'Photo cleanup'} · Run ${o.id} · ${fmtDate(o.created_at)}`), h('span', `${o.op === 'restore' ? 'Restore' : o.dry_run ? 'Dry run' : 'Trash'} · ${o.status} · ${o.done_count}/${o.total} completed · ${o.failed_count} failed`),
        o.error && h('span', o.error),
        ['pending', 'paused', 'cancelled'].includes(o.status) && h('button', {disabled: !!runningOp, onclick: async () => {
          try { if (['paused', 'cancelled'].includes(o.status)) await api.resumeOperation(o.id); execute(o.id); } catch(e) {showError(e);}
        }}, o.status === 'pending' ? 'Start' : 'Resume'),
        ['running', 'pending', 'paused'].includes(o.status) && h('button', {onclick: async () => {
          try {await api.cancelOperation(o.id); await refreshOps();} catch(e) {showError(e);}
        }}, 'Stop'),
        o.op === 'trash' && !o.dry_run && o.done_count > 0 && !['pending', 'running', 'paused'].includes(o.status) &&
        h('button', {disabled: !!runningOp, onclick: async () => {
          if (!confirm(`Restore ${o.done_count} photos from operation ${o.id}?`)) return;
          try {const inverse = await api.undoOperation(o.id); if (['paused', 'cancelled'].includes(inverse.status)) await api.resumeOperation(inverse.id); execute(inverse.id);} catch(e) {showError(e);}
        }}, 'Undo')))) : null) : null);
  }

  const run = h('button.primary', {onclick: start}, `Find ${config.title.toLowerCase()}`);
  const stop = h('button', {hidden: true, onclick: async () => {
    try { await api.post(`/api/jobs/${jobId}/cancel`); status.textContent = 'Stopping; previous suggestions will be kept.'; }
    catch (e) { status.textContent = e.message; }
  }}, 'Stop analysis');
  const next = h('button', {hidden: true, onclick: () => load(true).catch(showError)}, 'Show more bursts');
  function showError(e) { status.textContent = e.message; }
  async function poll() {
    if (disposed) return;
    try {
      const j = await api.job(jobId);
      if (disposed) return;
      status.textContent = j.message || 'Checking cached previews…';
      if (j.status === 'running') { timer = setTimeout(poll, 1500); return; }
      analyzing = false; updateButtons(); sensitivity.disabled = false; stop.hidden = true;
      if (j.status === 'error') status.textContent = `Analysis failed: ${(j.error || '').split('\n')[0]}. Your previous suggestions remain available.`;
      else if (j.status === 'cancelled') status.textContent = 'Analysis stopped. Previous suggestions are unchanged.';
      else {
        const r = j.result;
        status.textContent = `${r.groups} candidate ${config.noun}${r.groups === 1 ? '' : 's'}. ${r.unavailable} previews unavailable; ${r.without_date} photos have no date. Retry missing previews in Library Sync, then analyze again.`;
      }
      await load();
    } catch (e) { showError(e); analyzing = false; updateButtons(); sensitivity.disabled = false; stop.hidden = true; }
  }
  async function start() {
    if (busy || runningOp) return;
    analyzing = true; updateButtons(); sensitivity.disabled = true;
    try { const j = await api.post('/api/accidents/analyze', {sensitivity: sensitivity.value, category}); jobId = j.id; stop.hidden = false; await poll(); }
    catch (e) { showError(e); analyzing = false; updateButtons(); sensitivity.disabled = false; }
  }
  function card(g) {
    const selected = new Set();
    const all = h('input', {type: 'checkbox', onchange: () => {
      if (busy || runningOp || analyzing) return;
      for (const p of g.photos.filter(p => !p.context)) {if (all.checked) selected.add(p.id); else selected.delete(p.id);}
      updateButtons();
    }});
    const boxes = new Map();
    const keep = h('button', {onclick: ignore}, `Keep this ${config.noun}`);
    const feedback = h('p', {role: 'status'});
    const section = h('section.group');
    function update() {
      const locked = busy || !!runningOp || analyzing;
      keep.disabled = locked; all.disabled = locked;
      const total = g.photos.filter(p => !p.context).length;
      all.checked = total > 0 && selected.size === total;
      all.indeterminate = selected.size > 0 && selected.size < total;
      for (const [id, box] of boxes) {box.checked = selected.has(id); box.disabled = locked;}
    }
    buttons.add(update);
    selectionState.set(g.id, {group: g, selected, section, update});
    async function ignore() {
      if (busy || runningOp || analyzing) return;
      busy = true; updateButtons();
      try {
        await api.post(`/api/accidents/${g.id}/ignore`);
        section.remove(); buttons.delete(update); selectionState.delete(g.id);
        toast('Group kept.', 'ok');
        if (!selectionState.size) await load();
      } catch (e) {feedback.textContent = e.message;}
      finally {busy = false; updateButtons();}
    }
    const tiles = g.photos.sort((a,b) => (a.taken_at || '').localeCompare(b.taken_at || '')).map(p => {
      const img = h('img', {alt: p.name || 'Photo preview', loading: 'lazy'});
      const previewStatus = h('span.cap', p.thumb ? 'Loading preview…' : 'Preview unavailable');
      if (p.thumb) send({type: 'GET_IMAGE', url: `${api.API}${p.thumb}`}).then(r => {
        if (disposed) return;
        if (r.ok) {img.src = r.dataUrl; previewStatus.textContent = '';}
        else previewStatus.textContent = 'Preview unavailable; open photo to inspect.';
      });
      const box = h('input', {type: 'checkbox', onchange: () => {
        if (busy || runningOp) {box.checked = selected.has(p.id); return;}
        if (box.checked) selected.add(p.id); else selected.delete(p.id);
        updateButtons();
      }});
      boxes.set(p.id, box);
      return h('div.tile.accident-photo', img, p.id === g.keeper_id ? h('strong', 'Suggested keeper') : null, previewStatus, h('p.cap', fmtDate(p.taken_at)),
        h('p', p.context ? 'Nearby photo · kept for context' : p.reasons.join(' · ') || 'No warning signs'),
        p.context ? null : h('label.check', box, 'Select for trash'),
        h('a', {href: p.product_url, target: '_blank', rel: 'noopener noreferrer'}, 'Inspect in Google Photos'));
    });
    render(section, h('div.row', h('h2', `${g.title || config.title} · ${fmtDate(g.taken_at)}`), h('label.check', all, 'Select entire group'), keep), h('div.tiles', tiles),
      h('p.sub', 'Unselected photos remain protected. Entire-group selection includes the suggested keeper.'), feedback);
    return section;
  }
  async function load(more = false) {
    next.disabled = true;
    try {
      const result = await api.get(`/api/accidents?category=${category}&label=${encodeURIComponent(category === 'temporary' ? filter.value : '')}&after=${more ? after : 0}`);
      if (disposed) return;
      if (!more) {render(list); buttons.clear(); selectionState.clear();}
      for (const g of result.groups) {list.append(card(g)); after = g.id;}
      if (!list.children.length) render(list, h('p.empty', `No ${config.noun}s in this view. Run analysis after downloading previews in Library Sync, or choose another category.`));
      next.hidden = !result.more;
      updateButtons();
    } finally { next.disabled = busy || !!runningOp || analyzing; }
  }
  render(root, h('h1', config.title),
    h('p', config.description),
    h('p', 'These are small previews. Inspect photos in Google Photos before deciding; this does not judge the original’s sharpness.'),
    h('div.row', category !== 'temporary' ? h('label', {htmlFor: 'accident-sensitivity'}, 'Sensitivity') : null, category !== 'temporary' ? sensitivity : null, run, stop),
    h('p.sub', category === 'temporary' ? 'The first run reads cached previews locally and can take several minutes. Later runs reuse saved labels. Nothing is selected automatically.' : 'Broad includes more borderline matches. Very broad casts a wider net; expect more unrelated photos. Nothing is selected automatically.'), status, operations, category === 'temporary' ? h('div.row', h('label', {htmlFor: 'temporary-filter'}, 'Show'), filter) : null, h('div.cleanup-actions.row', selectionCount, h('label.check', dryRun, 'Dry run (no changes)'), trashPage), list, next);
  await load();
  await refreshOps();
  opsTimer = setInterval(() => refreshOps().catch(showError), 3000);
  const active = (await api.get('/api/jobs')).find(j => j.name === jobName && j.status === 'running');
  if (active) {jobId = active.id; analyzing = true; updateButtons(); sensitivity.disabled = true; stop.hidden = false; poll();}
  return () => {disposed = true; headerObserver.disconnect(); clearTimeout(timer); clearInterval(opsTimer);};
}
