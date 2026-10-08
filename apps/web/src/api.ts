import { coordinator } from "./auth/browser";

/** Non-2xx answer from the API (auth failures throw AuthRequiredError instead). */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`API request failed with HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Typed protected GET. TypeScript types vanish at runtime, so JSON from the
 * network is `unknown` until `parse` checks its shape; the generic `T` is
 * whatever `parse` returns. A 401 triggers at most one coordinated refresh.
 */
export async function getJson<T>(path: string, parse: (data: unknown) => T): Promise<T> {
  const response = await coordinator.fetch(path, {}, { replayOnUnauthorized: true });
  if (!response.ok) throw new ApiError(response.status);
  return parse(await response.json());
}

export interface Identity {
  id: string;
  email: string;
}

export function parseIdentity(data: unknown): Identity {
  if (typeof data === "object" && data !== null) {
    const { id, email } = data as Record<string, unknown>;
    if (typeof id === "string" && typeof email === "string") return { id, email };
  }
  throw new Error("Unexpected /auth/me response");
}

export const fetchIdentity = (): Promise<Identity> => getJson("/api/auth/me", parseIdentity);
