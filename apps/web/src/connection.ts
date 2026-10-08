import { ApiError, getJson } from "./api";
import { coordinator } from "./auth/browser";

const PATH = "/api/integrations/intervals";
export const CONNECTION_QUERY_KEY = ["intervals-connection"] as const;

export interface ConnectionMetadata {
  configured: boolean;
  athlete_id: string | null;
  last_error_code: string | null;
}

export interface TestedConnection {
  athlete_id: string;
  timezone: string;
}

function parseMetadata(data: unknown): ConnectionMetadata {
  if (typeof data === "object" && data !== null) {
    const { configured, athlete_id, last_error_code } = data as Record<string, unknown>;
    if (
      typeof configured === "boolean" &&
      (typeof athlete_id === "string" || athlete_id === null) &&
      (typeof last_error_code === "string" || last_error_code === null)
    ) return { configured, athlete_id, last_error_code };
  }
  throw new Error("Unexpected connection metadata response");
}

function parseTest(data: unknown): TestedConnection {
  if (typeof data === "object" && data !== null) {
    const { athlete_id, timezone } = data as Record<string, unknown>;
    if (typeof athlete_id === "string" && typeof timezone === "string") return { athlete_id, timezone };
  }
  throw new Error("Unexpected connection test response");
}

export const fetchConnection = (): Promise<ConnectionMetadata> => getJson(PATH, parseMetadata);

/** Direct writes, not TanStack mutations: no cached variables, retries or replay. */
async function submit<T>(
  action: "test" | "save",
  api_key: string,
  athlete_id: string,
  signal: AbortSignal,
  parse: (data: unknown) => T,
): Promise<T> {
  const response = await coordinator.fetch(
    action === "test" ? `${PATH}/test` : PATH,
    {
      method: action === "test" ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key, athlete_id }),
      signal,
    },
    { replayOnUnauthorized: false },
  );
  // Do not read error bodies: even a misconfigured backend must not echo a key.
  if (!response.ok) throw new ApiError(response.status);
  return parse(await response.json()); // allowlist fields before returning/caching
}

export const testConnection = (key: string, athlete: string, signal: AbortSignal) =>
  submit("test", key, athlete, signal, parseTest);
export const saveConnection = (key: string, athlete: string, signal: AbortSignal) =>
  submit("save", key, athlete, signal, parseMetadata);
