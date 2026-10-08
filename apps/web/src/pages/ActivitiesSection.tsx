import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ACTIVITIES_QUERY_KEY, fetchActivities, syncRecentActivities } from "../activities";
import { formatDistance, formatDuration, formatLoad, formatLocalStart } from "../activityFormat";
import { ApiError } from "../api";
import { coordinator } from "../auth/browser";
import { CONNECTION_QUERY_KEY } from "../connection";

const BUTTON_STYLE = "rounded border border-zinc-700 px-3 py-2 text-sm hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-400 disabled:cursor-not-allowed disabled:opacity-50";

export default function ActivitiesSection({ configured }: { configured: boolean }) {
  const activities = useQuery({ queryKey: ACTIVITIES_QUERY_KEY, queryFn: fetchActivities });
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => { request.current?.abort(); }, []);

  async function sync() {
    if (request.current || !configured) return;
    const controller = new AbortController();
    request.current = controller;
    const generation = coordinator.getState().generation;
    const stillCurrent = () => !controller.signal.aborted && coordinator.getState().generation === generation;
    setPending(true);
    setNotice(null);
    setError(null);
    try {
      const result = await syncRecentActivities(controller.signal);
      if (stillCurrent()) setNotice(`Synced ${result.synced_count} recent activities.`);
    } catch (caught) {
      if (stillCurrent()) setError(caught instanceof ApiError && caught.status === 409
        ? "Sync already running. Check activity status before trying again."
        : "Could not confirm sync. It may have completed; check activity status before retrying manually.");
    } finally {
      if (stillCurrent()) {
        // Invalidation marks cached reads stale and refetches active queries.
        // Also read after failure: cached rows stay visible, but the backend's
        // attempt error (or an ambiguously completed sync) must become visible.
        // Even an initial read with no cached data must be restarted: it may
        // have captured the pre-sync snapshot. Cancellation ignores late data.
        await Promise.all([
          queryClient.cancelQueries({ queryKey: ACTIVITIES_QUERY_KEY }),
          queryClient.cancelQueries({ queryKey: CONNECTION_QUERY_KEY }),
        ]);
        if (stillCurrent()) {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ACTIVITIES_QUERY_KEY }),
            queryClient.invalidateQueries({ queryKey: CONNECTION_QUERY_KEY }),
          ]);
          if (stillCurrent()) setPending(false);
        }
      }
      if (request.current === controller) request.current = null;
    }
  }

  const data = activities.data;
  return (
    <section aria-labelledby="activities-heading" className="mt-10 border-t border-zinc-800 pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="activities-heading" className="text-lg font-medium">Activities</h2>
        <button type="button" className={BUTTON_STYLE} disabled={pending || !configured} onClick={() => void sync()}>
          {pending ? "Syncing recent activities…" : "Sync recent activities"}
        </button>
      </div>
      <p className="mt-2 text-sm text-zinc-400">Recent preview (last 30 days, unverified). No automatic sync.</p>
      {!configured && <p className="mt-2 text-sm text-zinc-400">Save a connection to sync recent activities.</p>}
      <div className="my-4 space-y-2 text-sm text-zinc-300" aria-live="polite">
        {notice && <p role="status">{notice}</p>}
        {error && <p role="alert">{error}</p>}
        {activities.isPending && <p role="status">Loading activities…</p>}
        {activities.isError && <p role="alert">Could not load activity status. {data ? "Cached activities remain below; freshness is unknown." : "Check activity status to try again."}</p>}
        {data && <>
          <p className="break-words">Last preview: {data.last_preview_at ?? "—"}</p>
          {data.possibly_truncated && <p>Partial preview: the provider limit may have been reached.</p>}
          {data.last_error_code && <p>Stale preview: the last sync reported an error. Cached activities are retained.</p>}
        </>}
      </div>
      {data && (data.items.length === 0
        ? <p className="border-y border-zinc-800 py-6 text-sm text-zinc-400">No cached activities. Sync recent activities to fetch a preview.</p>
        : <ul className="divide-y divide-zinc-800 border-y border-zinc-800">
          {data.items.map((item) => <li key={item.id} className="grid gap-3 py-4 sm:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <h3 className="break-words text-sm font-medium">{item.name ?? "—"}</h3>
              <p className="mt-1 break-words text-sm text-zinc-400">{item.type ?? "—"} · {formatLocalStart(item.start_local)} <span className="text-xs">(provider local)</span></p>
            </div>
            <dl className="grid grid-cols-3 gap-4 text-sm tabular-nums sm:text-right">
              <div><dt className="text-xs text-zinc-400">Duration (h:mm)</dt><dd className="mt-1">{formatDuration(item.duration_s)}</dd></div>
              <div><dt className="text-xs text-zinc-400">Distance (km)</dt><dd className="mt-1">{formatDistance(item.distance_m)}</dd></div>
              <div><dt className="text-xs text-zinc-400">Provider load</dt><dd className="mt-1">{formatLoad(item.training_load)}</dd></div>
            </dl>
          </li>)}
        </ul>)}
      {data?.has_more && <p className="mt-3 text-sm text-zinc-400">Showing the latest 50 cached activities. More are stored.</p>}
      <button type="button" className={`${BUTTON_STYLE} mt-4`} disabled={activities.isFetching || pending} onClick={() => void activities.refetch()}>
        {activities.isFetching ? "Checking activity status…" : "Check activity status"}
      </button>
    </section>
  );
}
