import { useSyncExternalStore } from "react";
import { coordinator } from "./auth/browser";
import ConnectionPage from "./pages/ConnectionPage";
import LoginScreen from "./pages/LoginScreen";

/**
 * React "state" is data that, when changed, re-renders components. The auth
 * state lives outside React (in the coordinator, shared with other code), so
 * `useSyncExternalStore` subscribes a component to it: React re-renders
 * whenever `getState()` returns a different object.
 */
export default function App() {
  const auth = useSyncExternalStore(coordinator.subscribe, coordinator.getState);

  switch (auth.status) {
    case "initializing":
      return <Centered>Restoring session…</Centered>;
    case "unsupported":
      return (
        <Centered>
          <h1 className="mb-2 text-lg font-semibold">Browser not supported</h1>
          <p className="text-zinc-400">
            velolab needs Web Locks and BroadcastChannel to keep your session safe across tabs.
            Please use a current version of Chrome, Edge, Firefox or Safari.
          </p>
        </Centered>
      );
    case "login-required":
      return <LoginScreen reason={auth.reason} />;
    case "authenticated":
      return <ConnectionPage />;
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center p-6">{children}</main>;
}
