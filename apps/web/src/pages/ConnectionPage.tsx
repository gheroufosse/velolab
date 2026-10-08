import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchIdentity } from "../api";
import { coordinator } from "../auth/browser";

/** Protected placeholder; the intervals.icu connection form arrives in ADR-025 slice 2. */
export default function ConnectionPage() {
  // useQuery runs the fetch, caches the result under the key and exposes
  // loading/error state. Its cache is cleared on every auth generation change.
  const identity = useQuery({ queryKey: ["me"], queryFn: fetchIdentity });
  const [signingOut, setSigningOut] = useState(false);

  async function signOut() {
    setSigningOut(true);
    // The coordinator clears local state at once; App then shows the login screen.
    await coordinator.logout();
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-8 flex items-center justify-between border-b border-zinc-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight">velolab</h1>
        <div className="flex items-center gap-4 text-sm text-zinc-400">
          <span>{identity.data?.email ?? (identity.isError ? "Signed in" : "…")}</span>
          <button
            type="button"
            onClick={() => void signOut()}
            disabled={signingOut}
            className="rounded border border-zinc-700 px-3 py-1 text-zinc-100 hover:bg-zinc-900 disabled:opacity-50"
          >
            Log out
          </button>
        </div>
      </header>
      <section aria-labelledby="connection-heading">
        <h2 id="connection-heading" className="mb-2 text-lg font-medium">
          Connection
        </h2>
        <p className="text-zinc-400">
          intervals.icu connection is not set up yet. Testing and saving a connection comes next.
        </p>
      </section>
    </main>
  );
}
