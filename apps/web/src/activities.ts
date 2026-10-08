import { ApiError, getJson } from "./api";
import { coordinator } from "./auth/browser";

export const ACTIVITIES_QUERY_KEY = ["activities"] as const;
export interface Activity {
  id: string;
  name: string | null;
  type: string | null;
  start_local: string | null;
  duration_s: number | null;
  distance_m: number | null;
  training_load: number | null;
}
interface PreviewStatus {
  last_preview_at: string | null;
  possibly_truncated: boolean;
  last_error_code: string | null;
}
export interface ActivityList extends PreviewStatus {
  items: Activity[];
  has_more: boolean;
  coverage: "recent_preview";
}
export interface SyncResult extends PreviewStatus { synced_count: number }

function object(data: unknown): Record<string, unknown> {
  if (typeof data !== "object" || data === null || Array.isArray(data)) throw new Error("Unexpected activity response");
  return data as Record<string, unknown>;
}
const text = (value: unknown): string | null => typeof value === "string" && value.trim() !== "" ? value : null;
const metric = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

function status(data: Record<string, unknown>): PreviewStatus {
  if (
    typeof data.possibly_truncated !== "boolean" ||
    !(data.last_preview_at === null || typeof data.last_preview_at === "string") ||
    !(data.last_error_code === null || typeof data.last_error_code === "string")
  ) throw new Error("Unexpected preview status");
  return {
    last_preview_at: data.last_preview_at,
    possibly_truncated: data.possibly_truncated,
    last_error_code: data.last_error_code,
  };
}

function parseList(data: unknown): ActivityList {
  const body = object(data);
  if (!Array.isArray(body.items) || typeof body.has_more !== "boolean" || body.coverage !== "recent_preview") {
    throw new Error("Unexpected activity list");
  }
  const items = body.items.map((entry: unknown): Activity => {
    const item = object(entry);
    if (typeof item.id !== "string" || !item.id) throw new Error("Unexpected activity identity");
    // Allowlist only; never cache raw payloads. Invalid optional values are gaps.
    return {
      id: item.id, name: text(item.name), type: text(item.type), start_local: text(item.start_local),
      duration_s: metric(item.duration_s), distance_m: metric(item.distance_m), training_load: metric(item.training_load),
    };
  });
  return { ...status(body), items, has_more: body.has_more, coverage: body.coverage };
}

export const fetchActivities = (): Promise<ActivityList> => getJson("/api/activities", parseList);

/** A sync may have completed even if its answer is lost: never retry/replay. */
export async function syncRecentActivities(signal: AbortSignal): Promise<SyncResult> {
  const response = await coordinator.fetch("/api/integrations/intervals/sync-now", {
    method: "POST", signal,
  }, { replayOnUnauthorized: false });
  if (!response.ok) throw new ApiError(response.status);
  const body = object(await response.json());
  if (typeof body.synced_count !== "number" || !Number.isSafeInteger(body.synced_count) || body.synced_count < 0) {
    throw new Error("Unexpected sync result");
  }
  return { ...status(body), synced_count: body.synced_count };
}
