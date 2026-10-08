# velolab-web

React + TypeScript frontend for velolab. Current scope is
[ADR-025](../../docs/decisions.md) slice 1 only: browser login, a protected
Connection placeholder, logout and reload recovery. No dashboard yet.

Stack: Vite (dev server + bundler), React 19, TypeScript, Tailwind CSS 4,
TanStack Query, ESLint, Vitest. Package manager: **npm** (`package-lock.json` is
committed; CI runs `npm ci`).

## Run locally

Everything binds to loopback (`127.0.0.1`) only (ADR-025).

1. Start FastAPI on `127.0.0.1:8000` with the browser-facing auth settings
   (ADR-021/025; set in your untracked `.env`, never committed):

   ```
   AUTH_TRUSTED_ORIGIN=http://127.0.0.1:5173
   AUTH_COOKIE_PATH=/api/auth
   AUTH_JWT_SECRET=<at least 32 random bytes>
   ```

2. Start the web app:

   ```
   npm ci          # once
   npm run dev     # http://127.0.0.1:5173
   ```

Open the exact `http://127.0.0.1:5173` origin (not `localhost`): the API
compares the browser `Origin` header with `AUTH_TRUSTED_ORIGIN` exactly.

## Checks (same as CI)

```
npm run typecheck   # tsc --noEmit
npm run lint
npm test            # vitest run
npm run build       # typecheck + production bundle in dist/
```

## Concepts for a Python developer

- **Bundler / dev server.** Browsers run JavaScript, not TypeScript or JSX.
  Vite compiles `src/` on the fly in `npm run dev` (with hot reload) and
  bundles it into static files in `dist/` for `npm run build`. TypeScript's
  types exist only at compile time (like `mypy`/`ty` annotations, but they are
  erased entirely); `tsc --noEmit` only type-checks.
- **Same-origin proxy.** The browser talks only to Vite at `/api/...`. Vite
  forwards to FastAPI and strips `/api` (`vite.config.ts`), so `/api/auth/login`
  becomes `/auth/login`. Same origin means no CORS; the browser still sends its
  `Origin` header, which FastAPI's CSRF check compares.
- **Where the session lives.** The 10-minute access token is held in memory
  inside the auth coordinator (`src/auth/coordinator.ts`) and is lost on reload.
  The 7-day refresh credential is an HttpOnly cookie that JavaScript cannot
  read. On page load the app calls `/api/auth/refresh` to restore the session.
- **Rotating cookie rules.** Each refresh replaces the cookie, and replaying an
  old one revokes the session. So refreshes are serialized across tabs with a
  Web Lock, the new token is shared with other tabs via `BroadcastChannel`, and
  if a refresh result is unknown (network error, timeout, 5xx) the app does
  **not** retry: it asks you to sign in again. Browsers without these APIs get
  an "unsupported" screen rather than an uncoordinated fallback.
- **React state.** A component re-renders when its state changes. `useState`
  holds per-component values (form inputs); `useSyncExternalStore` (see
  `App.tsx`) subscribes to state kept outside React, here the coordinator.
  TanStack Query (`useQuery`) caches server data; it is cleared on every login,
  logout or recovery and never holds tokens.

## Layout

- `src/auth/coordinator.ts` — auth state machine (framework-free, unit-tested).
- `src/auth/browser.ts` — wires it to `fetch`, `navigator.locks`, `BroadcastChannel`.
- `src/api.ts` — typed protected `getJson` and `/auth/me` parsing.
- `src/pages/` — login screen and Connection placeholder.
- `src/auth/coordinator.test.ts` — single-flight refresh, lost refresh response,
  logout, cross-tab and unsupported-browser behaviour.

Not covered by automated tests: real multi-tab behaviour in a browser (ADR-025
asks for manual verification with real tabs) and the React screens.
