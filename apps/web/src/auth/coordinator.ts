/**
 * Browser auth coordinator (ADR-021, ADR-023, ADR-025 slice 1).
 *
 * Concepts for a Python developer new to the browser:
 *
 * - The access token (a 10-minute JWT) lives only in this object's memory. It
 *   is never put in localStorage/sessionStorage, so a page reload forgets it.
 *   The long-lived credential is the HttpOnly refresh *cookie*, which
 *   JavaScript cannot read; the browser attaches it to /api/auth/* requests.
 * - Every refresh *rotates* that cookie, and the server treats reuse of an old
 *   one as theft and revokes the whole session. So two refreshes must never
 *   run at once, and a refresh whose answer we lost must never be repeated.
 * - Several tabs share one cookie jar but have separate JavaScript memory. A
 *   Web Lock (`navigator.locks`) is a mutex shared by all tabs of this origin;
 *   login/refresh/logout run inside it. A BroadcastChannel is a same-origin
 *   message bus between tabs, used to share the fresh token and to announce
 *   logout/recovery so other tabs stop using a dead session.
 * - "Single-flight" means concurrent callers inside this tab share one
 *   promise (like awaiting one asyncio.Task) instead of each starting a refresh.
 */

const LOCK_NAME = "velolab-auth";
const CHANNEL_NAME = "velolab-auth";
const CSRF_HEADERS = { "X-Velolab-CSRF": "1" } as const;
/** Treat a token as expired slightly early so it cannot lapse mid-request. */
const EXPIRY_SKEW_MS = 30_000;
const DEFAULT_TIMEOUT_MS = 15_000;

export type AuthStatus = "initializing" | "authenticated" | "login-required" | "unsupported";

/** Why login is required; the UI turns this into a message. */
export type LoginReason =
  | "no-session" // nothing to restore (first visit, cookie gone, session expired)
  | "expired" // the server rejected our refresh/token
  | "recovery" // a refresh outcome was unknown, so we refuse to replay it
  | "signed-out" // the user logged out in this tab
  | "signed-out-unconfirmed" // logged out here, but the server did not confirm revocation
  | "signed-out-elsewhere"; // another tab logged out

/**
 * Immutable snapshot. React compares snapshots by identity, so a new object is
 * created on every change. `generation` increases whenever the identity or
 * session changes (login, logout, recovery); anything cached for the previous
 * generation must be discarded.
 */
export interface AuthState {
  readonly status: AuthStatus;
  readonly reason?: LoginReason;
  readonly generation: number;
}

export class AuthRequiredError extends Error {
  readonly reason: LoginReason | "unsupported";
  constructor(reason: LoginReason | "unsupported") {
    super(`Authentication required (${reason})`);
    this.name = "AuthRequiredError";
    this.reason = reason;
  }
}

export class LoginFailedError extends Error {
  readonly kind: "invalid-credentials" | "unavailable";
  constructor(kind: "invalid-credentials" | "unavailable") {
    super(`Login failed (${kind})`);
    this.name = "LoginFailedError";
    this.kind = kind;
  }
}

/** The slice of `navigator.locks` we use; tests supply a fake. */
export interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

/** The slice of `BroadcastChannel` we use; tests supply a fake. */
export interface ChannelLike {
  postMessage(message: unknown): void;
  /** Register the handler for messages from *other* tabs (never the sender). */
  onMessage(handler: (data: unknown) => void): void;
}

export interface CoordinatorDeps {
  fetch: typeof fetch;
  /** Undefined when the browser lacks Web Locks => "unsupported". */
  locks?: LockManagerLike | undefined;
  /** Undefined when the browser lacks BroadcastChannel => "unsupported". */
  createChannel?: ((name: string) => ChannelLike) | undefined;
  now?: () => number;
  timeoutMs?: number;
}

export interface RequestOptions {
  /**
   * Allow one coordinated refresh + replay after a 401. Only for idempotent
   * reads. Credential writes and sync must leave this false (no replay).
   */
  replayOnUnauthorized?: boolean;
}

interface Token {
  value: string;
  expiresAt: number; // epoch milliseconds
}

type Broadcast =
  | { type: "token"; kind: "login" | "refresh"; token: string; expiresAt: number }
  | { type: "logout" }
  | { type: "recovery" };

function parseBroadcast(data: unknown): Broadcast | null {
  if (typeof data !== "object" || data === null) return null;
  const message = data as Record<string, unknown>;
  if (message.type === "logout") return { type: "logout" };
  if (message.type === "recovery") return { type: "recovery" };
  if (
    message.type === "token" &&
    (message.kind === "login" || message.kind === "refresh") &&
    typeof message.token === "string" &&
    typeof message.expiresAt === "number"
  ) {
    return { type: "token", kind: message.kind, token: message.token, expiresAt: message.expiresAt };
  }
  return null;
}

export class AuthCoordinator {
  readonly #fetch: typeof fetch;
  readonly #locks: LockManagerLike | undefined;
  readonly #channel: ChannelLike | null;
  readonly #now: () => number;
  readonly #timeoutMs: number;
  readonly #listeners = new Set<() => void>();

  #state: AuthState;
  #token: Token | null = null;
  #refreshFlight: Promise<string> | null = null;
  #started: Promise<void> | null = null;

  constructor(deps: CoordinatorDeps) {
    this.#fetch = deps.fetch;
    this.#locks = deps.locks;
    this.#now = deps.now ?? Date.now;
    this.#timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    // No uncoordinated fallback: without both APIs we could not keep the
    // rotating cookie safe, so we refuse to run rather than risk replay.
    this.#channel = deps.locks && deps.createChannel ? deps.createChannel(CHANNEL_NAME) : null;
    this.#state = {
      status: this.#channel ? "initializing" : "unsupported",
      generation: 0,
    };
    this.#channel?.onMessage((data) => this.#onBroadcast(data));
  }

  // --- state for React (useSyncExternalStore) -----------------------------

  readonly subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  readonly getState = (): AuthState => this.#state;

  #setState(status: AuthStatus, reason?: LoginReason, bumpGeneration = false): void {
    const generation = this.#state.generation + (bumpGeneration ? 1 : 0);
    this.#state = reason === undefined ? { status, generation } : { status, reason, generation };
    for (const listener of [...this.#listeners]) listener();
  }

  // --- lifecycle -----------------------------------------------------------

  /** Reload/tab-startup recovery: try to restore a session from the cookie. */
  start(): Promise<void> {
    this.#started ??= (async () => {
      if (this.#state.status !== "initializing") return;
      try {
        await this.#acquireToken(undefined);
      } catch (error) {
        if (!(error instanceof AuthRequiredError)) throw error;
        // #requireLogin already updated the state.
      }
    })();
    return this.#started;
  }

  async login(email: string, password: string): Promise<void> {
    const locks = this.#requireSupported();
    await locks.request(LOCK_NAME, async () => {
      let response: Response;
      try {
        response = await this.#cookieFetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
      } catch {
        throw new LoginFailedError("unavailable");
      }
      if (response.status === 401) throw new LoginFailedError("invalid-credentials");
      const token = response.ok ? await readToken(response, this.#now()) : null;
      if (token === null) throw new LoginFailedError("unavailable");
      this.#token = token;
      this.#setState("authenticated", undefined, true);
      this.#post({ type: "token", kind: "login", token: token.value, expiresAt: token.expiresAt });
    });
  }

  /**
   * Local state is cleared immediately (stopping protected work and rejecting
   * late responses); the server session is then revoked under the lock so it
   * cannot interleave with a refresh. Returns whether the server confirmed.
   */
  async logout(): Promise<{ serverConfirmed: boolean }> {
    const locks = this.#requireSupported();
    this.#requireLogin("signed-out", { type: "logout" });
    const result = await locks.request(LOCK_NAME, async () => {
      try {
        const response = await this.#cookieFetch("/api/auth/logout", { method: "POST" });
        // 401 means the session was already invalid: nothing left to revoke.
        return { serverConfirmed: response.ok || response.status === 401 };
      } catch {
        return { serverConfirmed: false };
      }
    });
    // Only annotate if nothing else (e.g. a new login) happened meanwhile.
    if (!result.serverConfirmed && this.#state.reason === "signed-out") {
      this.#setState("login-required", "signed-out-unconfirmed");
    }
    return result;
  }

  // --- protected requests --------------------------------------------------

  async fetch(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<Response> {
    this.#requireSupported();
    let token = await this.#currentToken();
    // Captured after any startup refresh (which starts generation 1) and
    // before the request: login/logout/recovery changes it.
    const generation = this.#state.generation;
    // Logout/recovery during the token await: do not send with a dead token.
    if (this.#state.status !== "authenticated") throw new AuthRequiredError(this.#state.reason ?? "expired");
    let response = await this.#fetch(path, withBearer(init, token));
    this.#assertGeneration(generation); // a late response after logout is dropped
    if (response.status !== 401) return response;
    if (!options.replayOnUnauthorized) {
      // A rejected write cannot be replayed, but it must still stop protected
      // work and send the browser back to login rather than keep a dead token.
      return this.#giveUp("expired");
    }

    // At most one coordinated refresh and one replay (the 401 proves the first
    // attempt was not processed, so replaying a read is safe).
    token = await this.#acquireToken(token);
    this.#assertGeneration(generation);
    response = await this.#fetch(path, withBearer(init, token));
    this.#assertGeneration(generation);
    if (response.status === 401) {
      this.#requireLogin("expired", { type: "recovery" });
      throw new AuthRequiredError("expired");
    }
    return response;
  }

  // --- internals -----------------------------------------------------------

  #requireSupported(): LockManagerLike {
    if (!this.#locks || !this.#channel) throw new AuthRequiredError("unsupported");
    return this.#locks;
  }

  #assertGeneration(generation: number): void {
    if (this.#state.generation !== generation || this.#state.status === "login-required") {
      throw new AuthRequiredError(this.#state.reason ?? "expired");
    }
  }

  #isFresh(token: Token | null): boolean {
    return token !== null && token.expiresAt - EXPIRY_SKEW_MS > this.#now();
  }

  /** Re-read each time: the state changes across awaits, so don't let TS narrow it. */
  #loginRequired(): boolean {
    return this.#state.status === "login-required";
  }

  async #currentToken(): Promise<string> {
    if (this.#loginRequired()) throw new AuthRequiredError(this.#state.reason ?? "expired");
    const token = this.#token;
    if (token !== null && this.#isFresh(token)) return token.value;
    return this.#acquireToken(token?.value);
  }

  /** Single-flight refresh. `stale` is the token the caller found unusable. */
  #acquireToken(stale: string | undefined): Promise<string> {
    const locks = this.#requireSupported();
    if (this.#loginRequired()) {
      return Promise.reject(new AuthRequiredError(this.#state.reason ?? "expired"));
    }
    if (this.#refreshFlight) return this.#refreshFlight;
    const flight = locks
      .request(LOCK_NAME, () => this.#refreshUnderLock(stale))
      .finally(() => {
        if (this.#refreshFlight === flight) this.#refreshFlight = null;
      });
    this.#refreshFlight = flight;
    return flight;
  }

  async #refreshUnderLock(stale: string | undefined): Promise<string> {
    // Waiting for the lock may have taken a while: another tab may have logged
    // out, logged in or refreshed. Re-check before spending the cookie.
    if (this.#loginRequired()) throw new AuthRequiredError(this.#state.reason ?? "expired");
    const current = this.#token;
    if (current !== null && this.#isFresh(current) && current.value !== stale) return current.value;

    const hadSession = this.#state.status === "authenticated";
    let response: Response;
    try {
      response = await this.#cookieFetch("/api/auth/refresh", { method: "POST" });
    } catch {
      // Timeout or network error: the server may or may not have rotated the
      // cookie. Replaying could trip replay-revocation, so do not retry.
      return this.#giveUp("recovery");
    }
    if (this.#loginRequired()) {
      // Logout/recovery happened while the request was in flight: discard.
      throw new AuthRequiredError(this.#state.reason ?? "expired");
    }
    if (response.status === 401) {
      // Definitive rejection: nothing was rotated. Quiet on first visit.
      return this.#giveUp(hadSession ? "expired" : "no-session", hadSession);
    }
    const token = response.ok ? await readToken(response, this.#now()) : null;
    if (token === null) return this.#giveUp("recovery"); // 5xx/403/garbled body
    this.#token = token;
    if (this.#state.status !== "authenticated") this.#setState("authenticated", undefined, true);
    this.#post({ type: "token", kind: "refresh", token: token.value, expiresAt: token.expiresAt });
    return token.value;
  }

  #giveUp(reason: LoginReason, broadcast = true): never {
    this.#requireLogin(reason, broadcast ? { type: "recovery" } : null);
    throw new AuthRequiredError(reason);
  }

  #requireLogin(reason: LoginReason, announce: Broadcast | null): void {
    this.#token = null;
    this.#setState("login-required", reason, true);
    if (announce) this.#post(announce);
  }

  /** Same-origin cookie-mutating request: CSRF header, cookie, time limit. */
  #cookieFetch(path: string, init: RequestInit): Promise<Response> {
    return this.#fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: { ...CSRF_HEADERS, ...(init.headers as Record<string, string> | undefined) },
      signal: AbortSignal.timeout(this.#timeoutMs),
    });
  }

  #post(message: Broadcast): void {
    this.#channel?.postMessage(message);
  }

  #onBroadcast(data: unknown): void {
    const message = parseBroadcast(data);
    if (message === null || this.#state.status === "unsupported") return;
    if (message.type === "token") {
      const { status } = this.#state;
      const token = { value: message.token, expiresAt: message.expiresAt };
      if (message.kind === "refresh" && status === "login-required") return; // stale news
      const current = this.#token;
      if (message.kind === "refresh" && current !== null && current.expiresAt >= token.expiresAt) return;
      if (!this.#isFresh(token)) return;
      this.#token = token;
      // A login elsewhere (possibly another account) starts a new generation.
      if (message.kind === "login" || status !== "authenticated") {
        this.#setState("authenticated", undefined, true);
      }
    } else if (this.#state.status !== "login-required") {
      this.#requireLogin(message.type === "logout" ? "signed-out-elsewhere" : "recovery", null);
    }
  }
}

function withBearer(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers, credentials: "same-origin" };
}

async function readToken(response: Response, now: number): Promise<Token | null> {
  try {
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null) return null;
    const { access_token: value, expires_in: seconds } = body as Record<string, unknown>;
    if (typeof value !== "string" || value === "" || typeof seconds !== "number") return null;
    return { value, expiresAt: now + seconds * 1000 };
  } catch {
    return null;
  }
}
