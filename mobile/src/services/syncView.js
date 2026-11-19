/**
 * What the delivery list should SHOW: the server's list (fresh, authoritative for deliveries that are
 * still the agent's) overlaid with what is stored on this device.
 *
 *  - A delivery with local sync state that isn't plain "synced" (pending / blocked / conflicted) is shown
 *    from the LOCAL copy, so the agent sees their own unsent change and its state.
 *  - A local record that needs attention but is NOT in the server's list any more (e.g. the delivery was
 *    reassigned, which is exactly why its update was refused) is still listed. Without this, a blocked
 *    record would silently disappear from the screen and nothing could ever explain or resolve it.
 *
 * Pure function: no storage, no network.
 */
const NEEDS_VISIBILITY = new Set(["pending", "blocked", "conflicted"]);

export function mergeLocalSyncState(serverList, localList) {
  const localById = new Map((localList || []).map((record) => [record.id, record]));
  const serverIds = new Set((serverList || []).map((record) => record.id));

  const overlaid = (serverList || []).map((record) => {
    const local = localById.get(record.id);
    return local && NEEDS_VISIBILITY.has(local.sync_status) ? local : record;
  });
  const orphans = (localList || []).filter((record) => !serverIds.has(record.id) && NEEDS_VISIBILITY.has(record.sync_status));
  return [...overlaid, ...orphans];
}
