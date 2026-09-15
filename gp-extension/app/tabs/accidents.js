import {h, render, send, fmtDate} from '../../lib/dom.js';
export async function accidentsTab(root, {api, toast}) {
  let disposed = false, timer, jobId, after = 0, busy = false;
  const status = h('p', {role: 'status', 'aria-live': 'polite'}, 'Uses cached previews of photos you own. No CLIP model needed.');
  const list = h('div.groups');
  const run = h('button.primary', {onclick: start}, 'Find likely accidents');
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
      run.disabled = false; stop.hidden = true;
      if (j.status === 'error') status.textContent = `Analysis failed: ${(j.error || '').split('\n')[0]}. Your previous suggestions remain available.`;
      else if (j.status === 'cancelled') status.textContent = 'Analysis stopped. Previous suggestions are unchanged.';
      else {
        const r = j.result;
        status.textContent = `${r.groups} possible accidental bursts. ${r.unavailable} previews unavailable; ${r.without_date} photos have no date. Retry missing previews in Library Sync, then analyze again.`;
      }
      await load();
    } catch (e) { showError(e); run.disabled = false; stop.hidden = true; }
  }
  async function start() {
    run.disabled = true;
    try { const j = await api.post('/api/accidents/analyze'); jobId = j.id; stop.hidden = false; await poll(); }
    catch (e) { showError(e); run.disabled = false; }
  }
  function card(g) {
    const selected = new Set();
    const review = h('button.primary', {disabled: true, onclick: () => action('review')}, 'Send selection to review');
    const keep = h('button', {onclick: () => action('ignore')}, 'Keep this burst');
    const feedback = h('p', {role: 'status'});
    const section = h('section.group');
    async function action(command) {
      if (busy) return;
      busy = true; review.disabled = true; keep.disabled = true;
      try {
        const r = await api.post(`/api/accidents/${g.id}/${command}`, command === 'review' ? {media_ids: [...selected]} : {});
        section.remove();
        toast(command === 'review' ? `Review ${r.action_id} created. Inspect and approve it in Review queue.` : 'Burst kept. It will stay hidden after analysis.', 'ok');
        if (!list.children.length) await load();
      } catch (e) { feedback.textContent = e.message; }
      finally { busy = false; keep.disabled = false; review.disabled = !selected.size; }
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
        if (box.checked) selected.add(p.id); else selected.delete(p.id);
        review.disabled = !selected.size;
        review.textContent = selected.size ? `Review ${selected.size} selected photos` : 'Send selection to review';
      }});
      return h('div.tile.accident-photo', img, previewStatus, h('p.cap', fmtDate(p.taken_at)),
        h('p', p.context ? 'Nearby photo · kept for context' : p.reasons.join(' · ') || 'No warning signs'),
        p.context ? null : h('label.check', box, 'Select for review'),
        h('a', {href: p.product_url, target: '_blank', rel: 'noopener noreferrer'}, 'Inspect in Google Photos'));
    });
    render(section, h('h2', `Burst · ${fmtDate(g.taken_at)}`), h('div.tiles', tiles),
      h('p.sub', 'Nothing is selected automatically. Unselected photos remain protected.'), h('div.row', review, keep), feedback);
    return section;
  }
  async function load(more = false) {
    next.disabled = true;
    try {
      const result = await api.get(`/api/accidents?after=${more ? after : 0}`);
      if (disposed) return;
      if (!more) render(list);
      for (const g of result.groups) {list.append(card(g)); after = g.id;}
      if (!list.children.length) render(list, h('p.empty', 'No bursts to review. Run analysis after downloading previews in Library Sync.'));
      next.hidden = !result.more;
    } finally { next.disabled = false; }
  }
  render(root, h('h1', 'Likely accidents'),
    h('p', 'Review runs of dark, low-detail or possibly blurry photos taken seconds apart. Intentional night shots and soft backgrounds can also appear here.'),
    h('p', 'These are small previews. Inspect photos in Google Photos before deciding; this does not judge the original’s sharpness.'),
    h('div.row', run, stop), status, list, next);
  await load();
  const active = (await api.get('/api/jobs')).find(j => j.name === 'accident-analysis' && j.status === 'running');
  if (active) {jobId = active.id; run.disabled = true; stop.hidden = false; poll();}
  return () => {disposed = true; clearTimeout(timer);};
}
