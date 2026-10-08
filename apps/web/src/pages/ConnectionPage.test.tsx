// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { AuthCoordinator } from "../auth/coordinator";

// Fake only browser coordination APIs and the external HTTP boundary.
const auth = vi.hoisted(() => ({ current: undefined as AuthCoordinator | undefined }));
vi.mock("../auth/browser", () => ({ get coordinator() { return auth.current; } }));

const PATH = "/api/integrations/intervals";
const KEY = "synthetic-entry-only-key";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let client: QueryClient;
let writeResponse: () => Response | Promise<Response>;
let metadata: { configured: boolean; athlete_id: string | null; last_error_code: string | null };
let calls: Array<{ path: string; init: RequestInit }>;

beforeEach(async () => {
  calls = [];
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
  await screen.findByText("No saved connection.");
  return view;
}

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
