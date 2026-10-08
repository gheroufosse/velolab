import { AuthCoordinator } from "./coordinator";

/** The one coordinator for this tab, wired to the real browser APIs. */
export function createBrowserCoordinator(): AuthCoordinator {
  const hasLocks = typeof navigator !== "undefined" && "locks" in navigator;
  return new AuthCoordinator({
    // `bind` keeps `this` pointing at the window; a detached `fetch` throws.
    fetch: globalThis.fetch.bind(globalThis),
    locks: hasLocks
      ? { request: (name, callback) => navigator.locks.request(name, callback) }
      : undefined,
    createChannel:
      typeof BroadcastChannel === "function"
        ? (name) => {
            const channel = new BroadcastChannel(name);
            return {
              postMessage: (message) => channel.postMessage(message),
              onMessage: (handler) => {
                channel.onmessage = (event: MessageEvent<unknown>) => handler(event.data);
              },
            };
          }
        : undefined,
  });
}

export const coordinator = createBrowserCoordinator();
