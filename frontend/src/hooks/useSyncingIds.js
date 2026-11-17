import { useEffect, useState } from "react";
import { getSyncingIds, subscribeSyncState } from "../services/syncEngine";

/**
 * The ids of the records being uploaded right now (a Set that updates live). "Syncing" is never
 * stored on a record — a crash mid-upload would leave it stuck — it is only ever "in flight now".
 */
export default function useSyncingIds() {
  const [ids, setIds] = useState(() => getSyncingIds());
  useEffect(() => {
    setIds(getSyncingIds());
    return subscribeSyncState(() => setIds(getSyncingIds()));
  }, []);
  return ids;
}
