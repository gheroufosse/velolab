# velolab-web

React + TypeScript frontend for velolab. Current scope is
[ADR-025](../../docs/decisions.md) slices 1–2: browser login, a protected
Connection page with separate Test and Save actions, logout and reload recovery.
No sync or dashboard yet. Real-key entry still requires the operational approvals
and disposable backup/restore verification in ADR-025; use synthetic credentials
until those gates are met.

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
If you run `npm run preview` (`http://127.0.0.1:4173`), set `AUTH_TRUSTED_ORIGIN=http://127.0.0.1:4173`.

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
- **Connection form and secret lifetime.** Athlete ID and UI feedback use React
  state. The password-style API-key field is *uncontrolled*: its value stays in
  the DOM rather than React state. Test and Save send one direct authenticated
  request each, without TanStack mutations (which retain submitted variables),
  retries or automatic 401 replay. The field clears immediately on submit and
  on unmount/account change; every action requires key re-entry, even after a
  successful Test. JavaScript cannot guarantee secure memory erasure.
- **Test is not Save.** Test only verifies identity/timezone. Save independently
  verifies and persists credentials server-side. Only allowlisted connection
  metadata goes into the query cache; stored keys are never returned or
  prefilled. A failed/ambiguous save does not establish whether storage changed:
  use **Check saved status** before a manual retry. Errors use static UI copy,
  not server/provider bodies.

## Layout

- `src/auth/coordinator.ts` — auth state machine (framework-free, unit-tested).
- `src/auth/browser.ts` — wires it to `fetch`, `navigator.locks`, `BroadcastChannel`.
- `src/api.ts` — typed protected `getJson` and `/auth/me` parsing.
- `src/connection.ts` — safe metadata parsing and direct Test/Save requests.
- `src/pages/` — login screen and Connection form.
- `src/auth/coordinator.test.ts` — single-flight refresh, lost refresh response,
  logout, cross-tab and unsupported-browser behaviour.

- `src/pages/ConnectionPage.test.tsx` — React Testing Library/jsdom workflow
  tests with the real auth coordinator and synthetic HTTP/browser coordination
  boundaries: secret clearing, cache exclusion, Test versus Save, conflict,
  unauthorized and ambiguous failures, existing metadata and late-response cleanup.

Not covered by automated tests: real multi-tab behaviour in a browser (ADR-025
asks for manual verification with real tabs), visual browser inspection and live
backend/provider integration. No real keys or production data are used by tests.
