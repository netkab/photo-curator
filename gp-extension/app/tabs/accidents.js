import {h, render, send, fmtDate} from '../../lib/dom.js';
import {trashAccident} from '../../lib/accident-trash.js';
export async function accidentsTab(root, {api, toast}, category = 'accidents') {
  const config = {
    accidents: {title: 'Likely accidents', noun: 'burst', description: 'Review runs of dark, low-detail or possibly blurry photos taken seconds apart. Intentional night shots and soft backgrounds can also appear here.'},
    temporary: {title: 'Temporary photos', noun: 'group', description: 'Find possible delivery updates, parking details, shopping references and screenshots using local text recognition. Documents and receipts are labeled separately. Photos are grouped by category and month for review. A match does not mean the information is no longer useful.'},
    attempts: {title: 'Repeated attempts', noun: 'group', description: 'Compare photos with similar appearance taken within two minutes. Keep your favorite expressions and framing; the suggested keeper uses preview sharpness, not personal significance.'},
  }[category];
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
  const dryRun = h('input', {type: 'checkbox', checked: true, onchange: updateButtons});
  let runningOp = null, opsTimer;
  function updateButtons() { for (const update of buttons) update(); }
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
    render(operations, visible.length ? h('section.card', h('h2', 'Operations'), visible.map(o =>
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
        }}, 'Undo')))) : null);
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
      run.disabled = false; sensitivity.disabled = false; stop.hidden = true;
      if (j.status === 'error') status.textContent = `Analysis failed: ${(j.error || '').split('\n')[0]}. Your previous suggestions remain available.`;
      else if (j.status === 'cancelled') status.textContent = 'Analysis stopped. Previous suggestions are unchanged.';
      else {
        const r = j.result;
        status.textContent = `${r.groups} candidate ${config.noun}${r.groups === 1 ? '' : 's'}. ${r.unavailable} previews unavailable; ${r.without_date} photos have no date. Retry missing previews in Library Sync, then analyze again.`;
      }
      await load();
    } catch (e) { showError(e); run.disabled = false; sensitivity.disabled = false; stop.hidden = true; }
  }
  async function start() {
    if (busy || runningOp) return;
    run.disabled = true; sensitivity.disabled = true;
    try { const j = await api.post('/api/accidents/analyze', {sensitivity: sensitivity.value, category}); jobId = j.id; stop.hidden = false; await poll(); }
    catch (e) { showError(e); run.disabled = false; sensitivity.disabled = false; }
  }
  function card(g) {
    const selected = new Set();
    const review = h('button.danger', {disabled: true, onclick: () => action('review')}, 'Preview selected');
    const keep = h('button', {onclick: () => action('ignore')}, `Keep this ${config.noun}`);
    const feedback = h('p', {role: 'status'});
    const section = h('section.group');
    function update() {
      review.disabled = !selected.size || busy || !!runningOp;
      keep.disabled = busy || !!runningOp;
      review.textContent = `${dryRun.checked ? 'Preview' : 'Trash'} ${selected.size || ''} selected`;
    }
    buttons.add(update);
    async function action(command) {
      if (busy || runningOp) return;
      busy = true; updateButtons();
      try {
        if (command === 'ignore') {
          await api.post(`/api/accidents/${g.id}/ignore`);
          section.remove(); buttons.delete(update);
          toast(`${config.noun === 'photo' ? 'Photo' : 'Group'} kept.`, 'ok');
        } else {
          const isDry = dryRun.checked;
          const op = await trashAccident({api, send, confirm, group: g, mediaIds: [...selected], dryRun: isDry});
          if (!op) return;
          if (!isDry) {section.remove(); buttons.delete(update); dryRun.checked = true;}
          execute(op.id);
        }
        if (!list.children.length) await load();
      } catch (e) { feedback.textContent = e.message; }
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
        update();
      }});
      return h('div.tile.accident-photo', img, p.id === g.keeper_id ? h('strong', 'Suggested keeper') : null, previewStatus, h('p.cap', fmtDate(p.taken_at)),
        h('p', p.context ? 'Nearby photo · kept for context' : p.reasons.join(' · ') || 'No warning signs'),
        p.context ? null : h('label.check', box, 'Select for trash'),
        h('a', {href: p.product_url, target: '_blank', rel: 'noopener noreferrer'}, 'Inspect in Google Photos'));
    });
    render(section, h('h2', `${g.title || config.title} · ${fmtDate(g.taken_at)}`), h('div.tiles', tiles),
      h('p.sub', 'Nothing is selected automatically. Unselected photos remain protected.'), h('div.row', review, keep), feedback);
    return section;
  }
  async function load(more = false) {
    next.disabled = true;
    try {
      const result = await api.get(`/api/accidents?category=${category}&label=${encodeURIComponent(category === 'temporary' ? filter.value : '')}&after=${more ? after : 0}`);
      if (disposed) return;
      if (!more) {render(list); buttons.clear();}
      for (const g of result.groups) {list.append(card(g)); after = g.id;}
      if (!list.children.length) render(list, h('p.empty', `No ${config.noun}s in this view. Run analysis after downloading previews in Library Sync, or choose another category.`));
      next.hidden = !result.more;
    } finally { next.disabled = false; }
  }
  render(root, h('h1', config.title),
    h('p', config.description),
    h('p', 'These are small previews. Inspect photos in Google Photos before deciding; this does not judge the original’s sharpness.'),
    h('div.row', category !== 'temporary' ? h('label', {htmlFor: 'accident-sensitivity'}, 'Sensitivity') : null, category !== 'temporary' ? sensitivity : null, run, stop),
    h('p.sub', category === 'temporary' ? 'The first run reads cached previews locally and can take several minutes. Later runs reuse saved labels. Nothing is selected automatically.' : 'Broad includes more borderline matches. Very broad casts a wider net; expect more unrelated photos. Nothing is selected automatically.'), status, h('label.check', dryRun, 'Dry run (no changes)'), operations, category === 'temporary' ? h('div.row', h('label', {htmlFor: 'temporary-filter'}, 'Show'), filter) : null, list, next);
  await load();
  await refreshOps();
  opsTimer = setInterval(() => refreshOps().catch(showError), 3000);
  const active = (await api.get('/api/jobs')).find(j => j.name === jobName && j.status === 'running');
  if (active) {jobId = active.id; run.disabled = true; sensitivity.disabled = true; stop.hidden = false; poll();}
  return () => {disposed = true; clearTimeout(timer); clearInterval(opsTimer);};
}
