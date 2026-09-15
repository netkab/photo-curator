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
    dry_run: true, note: 'Accident preview — no photos changed'});
  try {
    await api.approveAction(result.action_id);
    const applied = await api.applyAction(result.action_id, false);
    return {id: applied.result.operation_id};
  } catch (e) {
    throw new Error(`${e.message} Selection saved as review ${result.action_id}; continue from Review queue instead of selecting it again.`);
  }
}
