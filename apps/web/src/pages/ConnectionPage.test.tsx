// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { ActivityList } from "../activities";
import { AuthCoordinator } from "../auth/coordinator";

// Fake only browser coordination APIs and the external HTTP boundary.
const auth = vi.hoisted(() => ({ current: undefined as AuthCoordinator | undefined }));
vi.mock("../auth/browser", () => ({ get coordinator() { return auth.current; } }));

const PATH = "/api/integrations/intervals";
const KEY = "synthetic-entry-only-key";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let client: QueryClient;
let writeResponse: () => Response | Promise<Response>;
let activityResponse: () => Response | Promise<Response>;
const preview = (overrides: Partial<ActivityList> = {}): ActivityList => ({
  items: [], has_more: false, coverage: "recent_preview", last_preview_at: null,
  possibly_truncated: false, last_error_code: null, ...overrides,
});
let metadata: { configured: boolean; athlete_id: string | null; last_error_code: string | null };
let calls: Array<{ path: string; init: RequestInit }>;

beforeEach(async () => {
  calls = [];
  activityResponse = () => json(preview());
  writeResponse = () => json({ athlete_id: "i123", timezone: "Europe/Brussels" });
  metadata = { configured: false, athlete_id: null, last_error_code: null };
  localStorage.clear();
  sessionStorage.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  auth.current = new AuthCoordinator({
    locks: { request: async (_name, callback) => callback() },
    createChannel: () => ({ postMessage() {}, onMessage() {} }),
    fetch: async (input, init = {}) => {
      const path = String(input);
      calls.push({ path, init });
      if (path === "/api/auth/refresh") return json({ access_token: "synthetic-token", expires_in: 600 });
      if (path === "/api/auth/me") return json({ id: "owner", email: "owner@example.test" });
      if (init.method === "POST" || init.method === "PUT") return writeResponse();
      if (path === PATH) return json(metadata);
      if (path === "/api/activities") return activityResponse();
      throw new Error("Unexpected synthetic request");
    },
  });
  await auth.current.start();
});

afterEach(() => {
  cleanup();
  client.clear();
});

async function openPage() {
  const view = render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await screen.findByText(metadata.configured ? "Saved athlete: i123" : "No saved connection.");
  return view;
}

const cachedRide = {
  id: "activity-1", name: "Evening ride", type: "Ride", start_local: "2026-10-08T23:30:00",
  duration_s: 0, distance_m: 0, training_load: null,
};

describe("recent activity preview", () => {
  it("shows loading, empty, and read-error states without starting a sync", async () => {
    let complete!: (response: Response) => void;
    activityResponse = () => new Promise<Response>((resolve) => { complete = resolve; });
    await openPage();
    expect(screen.getByText("Loading activities…")).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Sync recent activities" }).disabled).toBe(true);
    await waitFor(() => expect(calls.some((call) => call.path === "/api/activities")).toBe(true));
    await act(async () => complete(json(preview())));
    await screen.findByText(/No cached activities/);
    activityResponse = () => json({}, 503);
    fireEvent.click(screen.getByRole("button", { name: "Check activity status" }));
    await screen.findByText(/Could not load activity status/);
    expect(calls.some((call) => call.path === `${PATH}/sync-now`)).toBe(false);
  });

  it("keeps provider-local starts, zero, gaps, and cached stale/partial coverage visible", async () => {
    activityResponse = () => json(preview({
      items: [cachedRide], has_more: true, possibly_truncated: true,
      last_preview_at: "2026-10-09T01:00:00Z", last_error_code: "provider_unavailable",
    }));
    await openPage();
    await screen.findByText("Evening ride");
    expect(screen.getByText(/Recent preview \(last 30 days, unverified\)/)).toBeTruthy();
    expect(screen.getByText(/2026-10-08 23:30:00/)).toBeTruthy();
    expect(screen.getByText("0:00")).toBeTruthy();
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getAllByRole("definition").map((cell) => cell.textContent)).toEqual(["0:00", "0", "—"]);
    expect(screen.getByText(/Partial preview/)).toBeTruthy();
    expect(screen.getByText(/Stale preview/)).toBeTruthy();
    expect(screen.getByText(/More are stored/)).toBeTruthy();
    activityResponse = () => json({}, 503);
    fireEvent.click(screen.getByRole("button", { name: "Check activity status" }));
    await screen.findByText(/freshness is unknown/);
    expect(screen.getByText("Evening ride")).toBeTruthy();
  });

  it("syncs once, blocks duplicate clicks, and refreshes the activity query", async () => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: null };
    let complete!: (response: Response) => void;
    writeResponse = () => new Promise<Response>((resolve) => { complete = resolve; });
    await openPage();
    await screen.findByText(/No cached activities/);
    fireEvent.click(screen.getByRole("button", { name: "Sync recent activities" }));
    const pending = screen.getByRole<HTMLButtonElement>("button", { name: "Syncing recent activities…" });
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    await waitFor(() => expect(calls.some((call) => call.path === `${PATH}/sync-now`)).toBe(true));
    activityResponse = () => json(preview({ items: [cachedRide], last_preview_at: "2026-10-09T01:00:00Z" }));
    await act(async () => complete(json({ synced_count: 1, last_preview_at: "2026-10-09T01:00:00Z", possibly_truncated: false, last_error_code: null })));
    await screen.findByText("Evening ride");
    expect(screen.getByText("Synced 1 recent activities.")).toBeTruthy();
    expect(calls.filter((call) => call.path === "/api/activities")).toHaveLength(2);
    const syncs = calls.filter((call) => call.path === `${PATH}/sync-now`);
    expect(syncs).toHaveLength(1);
    expect(syncs[0]!.init.method).toBe("POST");
    expect(syncs[0]!.init.credentials).toBe("same-origin");
    expect(new Headers(syncs[0]!.init.headers).get("Authorization")).toBe("Bearer synthetic-token");
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("does not let a pre-sync pending read hide newly synced activities", async () => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: null };
    let initialRead!: (response: Response) => void;
    activityResponse = () => new Promise<Response>((resolve) => { initialRead = resolve; });
    writeResponse = () => json({ synced_count: 1, last_preview_at: null, possibly_truncated: false, last_error_code: null });
    await openPage();
    await waitFor(() => expect(calls.some((call) => call.path === "/api/activities")).toBe(true));
    activityResponse = () => json(preview({ items: [cachedRide] }));
    fireEvent.click(screen.getByRole("button", { name: "Sync recent activities" }));
    await screen.findByText("Evening ride");
    await act(async () => initialRead(json(preview())));
    expect(screen.getByText("Evening ride")).toBeTruthy();
  });

  it.each(["busy", "lost response"])("does not replay a sync after %s and rereads cached status", async (failure) => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: null };
    activityResponse = () => json(preview({ items: [cachedRide] }));
    writeResponse = () => {
      if (failure === "busy") return json({ detail: "untrusted provider body" }, 409);
      throw new TypeError("untrusted provider body");
    };
    await openPage();
    await screen.findByText("Evening ride");
    fireEvent.click(screen.getByRole("button", { name: "Sync recent activities" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain(failure === "busy" ? "Sync already running" : "It may have completed");
    expect(document.body.textContent).not.toContain("untrusted provider body");
    await waitFor(() => expect(calls.filter((call) => call.path === "/api/activities")).toHaveLength(2));
    expect(calls.filter((call) => call.path === `${PATH}/sync-now`)).toHaveLength(1);
    expect(screen.getByText("Evening ride")).toBeTruthy();
  });

  it("renders invalid optional fields as gaps and caches only the response allowlist", async () => {
    activityResponse = () => json({ ...preview(), payload: KEY, items: [{
      id: "missing-fields", name: null, type: null, start_local: "invalid",
      duration_s: -1, distance_m: "unknown", training_load: null, payload: KEY,
    }] });
    await openPage();
    await screen.findByRole("heading", { name: "—" });
    expect(screen.getAllByRole("definition").map((cell) => cell.textContent)).toEqual(["—", "—", "—"]);
    expect(screen.getByText(/— · —/)).toBeTruthy();
    expectNoRetainedKey();
  });

  it("aborts an unmounted sync and ignores its late outcome", async () => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: null };
    let complete!: (response: Response) => void;
    writeResponse = () => new Promise<Response>((resolve) => { complete = resolve; });
    const view = await openPage();
    await screen.findByText(/No cached activities/);
    fireEvent.click(screen.getByRole("button", { name: "Sync recent activities" }));
    await waitFor(() => expect(calls.some((call) => call.path === `${PATH}/sync-now`)).toBe(true));
    const sync = calls.find((call) => call.path === `${PATH}/sync-now`)!;
    view.unmount();
    expect(sync.init.signal?.aborted).toBe(true);
    await act(async () => complete(json({ synced_count: 1, last_preview_at: null, possibly_truncated: false, last_error_code: null })));
    expect(calls.filter((call) => call.path === "/api/activities")).toHaveLength(1);
    expect(client.getQueryData(["activities"])).toEqual(preview());
  });

  it("requires login on a rejected sync without refresh or replay", async () => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: null };
    writeResponse = () => json({}, 401);
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Sync recent activities" }));
    await screen.findByRole("button", { name: "Sign in" });
    expect(calls.filter((call) => call.path === `${PATH}/sync-now`)).toHaveLength(1);
    expect(calls.filter((call) => call.path === "/api/auth/refresh")).toHaveLength(1);
  });
});

function enterCredentials() {
  fireEvent.change(screen.getByLabelText("Athlete ID"), { target: { value: "i123" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: KEY } });
}

function expectNoRetainedKey() {
  const queries = client.getQueryCache().getAll().map((query) => [query.queryKey, query.state]);
  expect(JSON.stringify(queries)).not.toContain(KEY);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
}

describe("connection enrollment", () => {
  it("clears the key immediately, keeps Test distinct from Save, and caches only safe metadata", async () => {
    let complete!: (response: Response) => void;
    writeResponse = () => new Promise<Response>((resolve) => { complete = resolve; });
    await openPage();
    enterCredentials();
    const keyInput = screen.getByLabelText<HTMLInputElement>("API key");
    expect(keyInput.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(keyInput.value).toBe("");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Testing…" }).disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save connection" }).disabled).toBe(true);
    expectNoRetainedKey();
    await waitFor(() => expect(calls.some((call) => call.path === `${PATH}/test`)).toBe(true));
    await act(async () => complete(json({ athlete_id: "i123", timezone: "Europe/Brussels", api_key: KEY })));
    await screen.findByText(/Test passed.*Not saved/);
    expect(screen.getByText("No saved connection.")).toBeTruthy();
    expectNoRetainedKey();

    writeResponse = () => json({ configured: true, athlete_id: "i123", last_error_code: null, api_key: KEY });
    fireEvent.change(keyInput, { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    expect(keyInput.value).toBe("");
    await screen.findByText("Connection saved.");
    expect(screen.getByText(/Saved athlete: i123/)).toBeTruthy();
    expect(screen.queryByText(/Not saved/)).toBeNull();
    expectNoRetainedKey();
    const writes = calls.filter((call) => call.init.method === "POST" || call.init.method === "PUT");
    expect(writes.map((call) => [call.path, call.init.method])).toEqual([
      ["/api/auth/refresh", "POST"], [`${PATH}/test`, "POST"], [PATH, "PUT"],
    ]);
    for (const call of writes.slice(1)) {
      expect(JSON.parse(call.init.body as string)).toEqual({ api_key: KEY, athlete_id: "i123" });
      expect(call.path).not.toContain(KEY);
      expect(call.init.credentials).toBe("same-origin");
      expect(new Headers(call.init.headers).get("Authorization")).toBe("Bearer synthetic-token");
    }
  });

  it("requires key re-entry after a conflict and never displays provider/error bodies", async () => {
    writeResponse = () => json({ detail: KEY }, 409);
    await openPage();
    enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("different athlete");
    expect(screen.getByRole("alert").textContent).toContain("Re-enter");
    expect(screen.getByLabelText<HTMLInputElement>("API key").value).toBe("");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save connection" }).disabled).toBe(true);
    expect(screen.queryByText("Connection saved.")).toBeNull();
    expect(document.body.textContent).not.toContain(KEY);
    expectNoRetainedKey();
    expect(calls.filter((call) => call.init.method === "PUT")).toHaveLength(1);
  });

  it("returns to login on a rejected write without refresh or replay", async () => {
    writeResponse = () => json({ detail: "Unauthorized" }, 401);
    await openPage();
    enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await screen.findByRole("button", { name: "Sign in" });
    expect(auth.current!.getState()).toMatchObject({ status: "login-required", reason: "expired" });
    expect(calls.filter((call) => call.path === "/api/auth/refresh")).toHaveLength(1);
    expect(calls.filter((call) => call.init.method === "PUT")).toHaveLength(1);
    expectNoRetainedKey();
  });

  it("shows existing metadata without prefilling the key", async () => {
    metadata = { configured: true, athlete_id: "i123", last_error_code: "provider_unavailable" };
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    await screen.findByText("Saved athlete: i123");
    expect(screen.getByLabelText<HTMLInputElement>("Athlete ID").value).toBe("i123");
    expect(screen.getByLabelText<HTMLInputElement>("API key").value).toBe("");
    expect(screen.getByText("The last connection attempt reported an error.")).toBeTruthy();
  });

  it("does not replay ambiguous network failures or retain the submitted key", async () => {
    writeResponse = () => { throw new TypeError(KEY); };
    await openPage();
    enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("check it before trying again");
    expect(screen.getByLabelText<HTMLInputElement>("API key").value).toBe("");
    expect(document.body.textContent).not.toContain(KEY);
    expect(calls.filter((call) => call.init.method === "PUT")).toHaveLength(1);
    expectNoRetainedKey();
  });

  it("aborts pending work on unmount and ignores a late save response", async () => {
    let complete!: (response: Response) => void;
    writeResponse = () => new Promise<Response>((resolve) => { complete = resolve; });
    const view = await openPage();
    enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((call) => call.init.method === "PUT")).toBe(true));
    const write = calls.find((call) => call.init.method === "PUT")!;
    view.unmount();
    expect(write.init.signal?.aborted).toBe(true);
    await act(async () => complete(json({ configured: true, athlete_id: "i123", last_error_code: null })));
    expect(client.getQueryData(["intervals-connection"])).toEqual({ configured: false, athlete_id: null, last_error_code: null });
    expectNoRetainedKey();
  });

  it("clears even an unsubmitted key when the page unmounts", async () => {
    const view = await openPage();
    enterCredentials();
    const keyInput = screen.getByLabelText<HTMLInputElement>("API key");
    view.unmount();
    expect(keyInput.value).toBe("");
    expectNoRetainedKey();
  });
});
