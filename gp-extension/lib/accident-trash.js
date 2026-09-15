/** The same explicit approval chain as Duplicates. Preview never consumes a group. */
export async function trashAccident({api, send, confirm, group, mediaIds, dryRun}) {
  const health = await send({type: 'PC_HEALTH'});
  if (!health?.ok || !health.gptk || !health.authed)
    throw new Error(health?.error || 'Open a signed-in Google Photos tab and try again. Nothing was queued.');
  if (!dryRun) {
    if (!(await api.health()).live_trash_enabled) throw new Error('Live trash is disabled in the backend.');
    const names = group.photos.filter(p => mediaIds.includes(p.id)).map(p => p.name || `Photo ${p.id}`);
    if (!confirm(`Move ${mediaIds.length} selected photo(s) to Google Photos trash?\n\n${names.join('\n')}\n\nUnselected photos stay protected. Undo works while Google retains these photos in trash.`)) return null;
  }
  const result = await api.post(`/api/accidents/${group.id}/review`, {media_ids: mediaIds, preview: dryRun});
  if (dryRun) return api.createOperation({op: 'trash', account: result.account, keys: result.keys,
    dry_run: true, note: `${group.category === 'temporary' ? 'Temporary photos' : group.category === 'attempts' ? 'Repeated attempts' : 'Accident'} preview — no photos changed`});
  try {
    await api.approveAction(result.action_id);
    const applied = await api.applyAction(result.action_id, false);
    return {id: applied.result.operation_id};
  } catch (e) {
    throw new Error(`${e.message} Selection saved as review ${result.action_id}; continue from Review queue instead of selecting it again.`);
  }
}

/** One confirmation, one review snapshot and one operation for the visible page selection. */
export async function trashCleanupPage({api, send, confirm, category, selections, dryRun}) {
  const health = await send({type: 'PC_HEALTH'});
  if (!health?.ok || !health.gptk || !health.authed)
    throw new Error(health?.error || 'Open a signed-in Google Photos tab and try again. Nothing was queued.');
  const groups = selections.map(({group, mediaIds}) => ({group_id: group.id, media_ids: [...mediaIds],
    allow_all: group.photos.filter(p => !p.context).every(p => mediaIds.includes(p.id))}));
  const count = new Set(groups.flatMap(g => g.media_ids)).size;
  if (!count) return null;
  if (!dryRun) {
    if (!(await api.health()).live_trash_enabled) throw new Error('Live trash is disabled in the backend.');
    const whole = groups.filter(g => g.allow_all).length;
    const names = selections.flatMap(({group, mediaIds}) => group.photos.filter(p => mediaIds.includes(p.id)).map(p => p.name || `Photo ${p.id}`));
    if (!confirm(`Move ${count} selected photos from ${groups.length} groups to Google Photos trash?\n\n${whole ? `${whole} entire group(s) selected, including any suggested keeper.\n\n` : ''}${names.join('\n')}\n\nUnselected photos remain protected. Undo is available while Google retains the photos in trash.`)) return null;
  }
  const result = await api.post('/api/accidents/review-bulk', {category, preview: dryRun, groups});
  const name = {accidents: 'Accident', temporary: 'Temporary photos', attempts: 'Repeated attempts'}[category];
  if (dryRun) return api.createOperation({op: 'trash', account: result.account, keys: result.keys,
    dry_run: true, note: `${name} page preview — no photos changed`});
  try {
    await api.approveAction(result.action_id);
    const applied = await api.applyAction(result.action_id, false);
    return {id: applied.result.operation_id};
  } catch (e) {
    throw new Error(`${e.message} Selection saved as review ${result.action_id}; continue from Review queue.`);
  }
}
