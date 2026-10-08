import { describe, expect, it } from "vitest";
import {
  AuthCoordinator,
  AuthRequiredError,
  type ChannelLike,
  type LockManagerLike,
} from "./coordinator";

// --- test doubles for the browser APIs -------------------------------------

type Handler = (call: Call) => Response | Promise<Response>;
interface Call {
  path: string;
  init: RequestInit;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const tokenBody = (value: string) => json({ access_token: value, token_type: "bearer", expires_in: 600 });

/** One Web Lock manager shared by all "tabs": requests run strictly one at a time. */
function fakeLocks(): LockManagerLike {
  let tail: Promise<unknown> = Promise.resolve();
  return {
    request<T>(_name: string, callback: () => Promise<T>): Promise<T> {
      const run = tail.then(callback, callback);
      tail = run.catch(() => undefined);
      return run;
    },
  };
}

/** A BroadcastChannel hub: a message reaches every other tab, not the sender. */
function fakeChannels() {
  const members = new Set<ChannelLike & { deliver(message: unknown): void }>();
  return (): ChannelLike => {
    const handlers: Array<(data: unknown) => void> = [];
    const channel = {
      postMessage(message: unknown) {
        for (const other of members) if (other !== channel) other.deliver(message);
      },
      onMessage: (handler: (data: unknown) => void) => void handlers.push(handler),
      deliver: (message: unknown) => handlers.forEach((handler) => handler(message)),
    };
    members.add(channel);
    return channel;
  };
}

function makeTab(
  handler: Handler,
  shared = { locks: fakeLocks(), createChannel: fakeChannels() },
  now?: () => number,
) {
  const calls: Call[] = [];
  const coordinator = new AuthCoordinator({
    fetch: (async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const call = { path: String(input), init };
      calls.push(call);
      return handler(call);
    }) as typeof fetch,
    ...shared,
    ...(now ? { now } : {}),
  });
  const count = (path: string) => calls.filter((call) => call.path === path).length;
  return { coordinator, calls, count, shared };
}

const bearer = (call: Call) => new Headers(call.init.headers).get("Authorization");

// --- tests ------------------------------------------------------------------

describe("refresh coordination", () => {
  it("shares one refresh between concurrent protected requests", async () => {
    let refreshes = 0;
    const { coordinator, calls } = makeTab(({ path }) => {
      if (path === "/api/auth/refresh") return tokenBody(`token-${++refreshes}`);
      return json({ ok: true });
    });

    const responses = await Promise.all([
      coordinator.fetch("/api/a"),
      coordinator.fetch("/api/b"),
      coordinator.fetch("/api/c"),
    ]);

    expect(responses.every((response) => response.ok)).toBe(true);
    expect(refreshes).toBe(1);
    const refresh = calls.find((call) => call.path === "/api/auth/refresh")!;
    expect(refresh.init.method).toBe("POST");
    expect(refresh.init.credentials).toBe("same-origin");
    expect((refresh.init.headers as Record<string, string>)["X-Velolab-CSRF"]).toBe("1");
    expect(calls.filter((call) => call.path !== "/api/auth/refresh").map(bearer)).toEqual([
      "Bearer token-1",
      "Bearer token-1",
      "Bearer token-1",
    ]);
  });

  it("refreshes once across two tabs starting together", async () => {
    let refreshes = 0;
    const handler: Handler = () => tokenBody(`token-${++refreshes}`);
    const a = makeTab(handler);
    const b = makeTab(handler, a.shared);

    await Promise.all([a.coordinator.start(), b.coordinator.start()]);

    // The second tab re-checks after getting the lock and adopts the token the
    // first tab broadcast instead of spending the rotated cookie again.
    expect(refreshes).toBe(1);
    expect(a.coordinator.getState().status).toBe("authenticated");
    expect(b.coordinator.getState().status).toBe("authenticated");
  });

  it("replays a rejected read once after a coordinated refresh", async () => {
    let refreshes = 0;
    const { coordinator, calls } = makeTab(({ path, init }) => {
      if (path === "/api/auth/refresh") return tokenBody(`token-${++refreshes}`);
      return new Headers(init.headers).get("Authorization") === "Bearer token-2"
        ? json({ ok: true })
        : json({}, 401);
    });
    await coordinator.start();

    const response = await coordinator.fetch("/api/data", {}, { replayOnUnauthorized: true });

    expect(response.status).toBe(200);
    expect(refreshes).toBe(2); // startup + one refresh for the 401
    expect(calls.filter((call) => call.path === "/api/data")).toHaveLength(2);
  });

  it("requires login if the replay is rejected too, and does not replay writes", async () => {
    const { coordinator, count } = makeTab(({ path }) =>
      path === "/api/auth/refresh" ? tokenBody("t") : json({}, 401),
    );
    await coordinator.start();

    const write = await coordinator.fetch("/api/write", { method: "PUT" });
    expect(write.status).toBe(401); // returned as-is, no automatic refresh/replay
    expect(count("/api/auth/refresh")).toBe(1);

    await expect(
      coordinator.fetch("/api/read", {}, { replayOnUnauthorized: true }),
    ).rejects.toBeInstanceOf(AuthRequiredError);
    expect(coordinator.getState()).toMatchObject({ status: "login-required", reason: "expired" });
  });
});

describe("ambiguous or rejected refresh", () => {
  it("requires login after a lost refresh response and never replays the cookie", async () => {
    let refreshAttempts = 0;
    const { coordinator, count } = makeTab(({ path }) => {
      if (path === "/api/auth/refresh") {
        refreshAttempts++;
        throw new TypeError("network error"); // request may have reached the server
      }
      return json({});
    });

    await expect(coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(coordinator.getState()).toMatchObject({ status: "login-required", reason: "recovery" });

    // Further protected work stops immediately; the spent cookie is not retried.
    await expect(coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);
    await expect(coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(refreshAttempts).toBe(1);
    expect(count("/api/data")).toBe(0);
  });

  it("treats a server error or garbled success body as unrecoverable too", async () => {
    for (const response of [() => json({}, 503), () => new Response("not json")]) {
      const { coordinator, count } = makeTab(response);
      await coordinator.start();
      expect(coordinator.getState()).toMatchObject({ status: "login-required", reason: "recovery" });
      await expect(coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);
      expect(count("/api/auth/refresh")).toBe(1);
    }
  });

  it("announces recovery so other tabs stop, and quietly handles a first visit", async () => {
    let clock = 0;
    let networkDown = false;
    const handler: Handler = () => {
      if (networkDown) throw new TypeError("network error");
      return tokenBody("t");
    };
    const a = makeTab(handler, undefined, () => clock);
    const b = makeTab(handler, a.shared, () => clock);
    await a.coordinator.start(); // b adopts the broadcast token
    expect(b.coordinator.getState().status).toBe("authenticated");

    clock += 11 * 60 * 1000; // access tokens (10 min) have expired
    networkDown = true;
    await expect(a.coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);

    expect(b.coordinator.getState()).toMatchObject({ status: "login-required", reason: "recovery" });

    const firstVisit = makeTab(() => json({}, 401));
    await firstVisit.coordinator.start();
    expect(firstVisit.coordinator.getState()).toMatchObject({
      status: "login-required",
      reason: "no-session",
    });
  });
});

describe("login and logout", () => {
  it("logs in with CSRF header and JSON body, and reports bad credentials", async () => {
    const { coordinator, calls } = makeTab(({ path, init }) =>
      path === "/api/auth/login" && JSON.parse(String(init.body)).password === "correct horse"
        ? tokenBody("login-token")
        : json({}, 401),
    );
    await coordinator.start(); // first visit: 401 -> login required

    await expect(coordinator.login("a@b.c", "wrong")).rejects.toMatchObject({
      kind: "invalid-credentials",
    });
    expect(coordinator.getState().status).toBe("login-required");

    await coordinator.login("a@b.c", "correct horse");
    expect(coordinator.getState().status).toBe("authenticated");
    const login = calls.filter((call) => call.path === "/api/auth/login").at(-1)!;
    expect((login.init.headers as Record<string, string>)["X-Velolab-CSRF"]).toBe("1");

    await coordinator.fetch("/api/data");
    expect(bearer(calls.at(-1)!)).toBe("Bearer login-token");
  });

  it("clears the token, stops protected work and signs out other tabs", async () => {
    const a = makeTab(({ path }) => (path === "/api/auth/logout" ? new Response(null, { status: 204 }) : tokenBody("t")));
    const b = makeTab(() => tokenBody("t"), a.shared);
    await a.coordinator.start();
    expect(b.coordinator.getState().status).toBe("authenticated"); // adopted via broadcast

    const result = await a.coordinator.logout();

    expect(result.serverConfirmed).toBe(true);
    expect(a.coordinator.getState()).toMatchObject({ status: "login-required", reason: "signed-out" });
    expect(b.coordinator.getState()).toMatchObject({
      status: "login-required",
      reason: "signed-out-elsewhere",
    });
    const logout = a.calls.find((call) => call.path === "/api/auth/logout")!;
    expect((logout.init.headers as Record<string, string>)["X-Velolab-CSRF"]).toBe("1");
    const refreshesBefore = a.count("/api/auth/refresh");
    await expect(a.coordinator.fetch("/api/data")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(a.count("/api/auth/refresh")).toBe(refreshesBefore);
  });

  it("drops a response that arrives after logout", async () => {
    let release!: (response: Response) => void;
    const slow = new Promise<Response>((resolve) => (release = resolve));
    const { coordinator } = makeTab(({ path }) => {
      if (path === "/api/auth/refresh") return tokenBody("t");
      if (path === "/api/auth/logout") return new Response(null, { status: 204 });
      return slow;
    });
    await coordinator.start();

    const inFlight = coordinator.fetch("/api/data");
    const settled = inFlight.then(
      () => "resolved",
      (error: unknown) => (error instanceof AuthRequiredError ? "rejected" : "other"),
    );
    await Promise.resolve();
    await coordinator.logout();
    release(json({ secret: "previous user's data" }));

    expect(await settled).toBe("rejected");
  });

  it("clears local state even when the server cannot confirm logout", async () => {
    let failLogout = false;
    const { coordinator } = makeTab(({ path }) => {
      if (path === "/api/auth/logout") throw new TypeError("offline");
      failLogout = true;
      return tokenBody("t");
    });
    await coordinator.start();
    expect(failLogout).toBe(true);

    const result = await coordinator.logout();

    expect(result.serverConfirmed).toBe(false);
    expect(coordinator.getState()).toMatchObject({
      status: "login-required",
      reason: "signed-out-unconfirmed",
    });
  });
});

describe("unsupported browsers", () => {
  it("refuses to run without Web Locks instead of refreshing uncoordinated", async () => {
    let calls = 0;
    const coordinator = new AuthCoordinator({
      fetch: (async () => {
        calls++;
        return json({});
      }) as typeof fetch,
      createChannel: fakeChannels(),
    });

    await coordinator.start();
    expect(coordinator.getState().status).toBe("unsupported");
    await expect(coordinator.fetch("/api/data")).rejects.toMatchObject({ reason: "unsupported" });
    expect(calls).toBe(0);
  });
});
