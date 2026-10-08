import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, fetchIdentity } from "../api";
import { coordinator } from "../auth/browser";
import ActivitiesSection from "./ActivitiesSection";
import {
  CONNECTION_QUERY_KEY, fetchConnection, saveConnection, testConnection,
} from "../connection";

const INPUT_STYLE = "rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-400";
const BUTTON_STYLE = "rounded border border-zinc-700 px-3 py-2 text-sm hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-400 disabled:cursor-not-allowed disabled:opacity-50";

function failureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "This connection is bound to a different athlete. Use the saved athlete ID.";
    if (error.status === 403) return "Connection setup is not enabled for this account.";
    if (error.status === 429) return "Too many requests. Wait before trying again.";
    if (error.status >= 400 && error.status < 500) return "Could not verify these credentials. Check the API key and athlete ID.";
  }
  return "Could not confirm the request. Saved status may be unchanged; check it before trying again.";
}

export default function ConnectionPage() {
  const identity = useQuery({ queryKey: ["me"], queryFn: fetchIdentity });
  const connection = useQuery({ queryKey: CONNECTION_QUERY_KEY, queryFn: fetchConnection });
  const queryClient = useQueryClient();
  const [signingOut, setSigningOut] = useState(false);
  const [athleteInput, setAthleteInput] = useState<string | null>(null);
  const athlete = athleteInput ?? connection.data?.athlete_id ?? "";
  const [hasKey, setHasKey] = useState(false);
  const [pending, setPending] = useState<"test" | "save" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Uncontrolled input: the key lives in the DOM, never React or query state.
  const keyInput = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const submitting = useRef(false);

  useEffect(() => {
    const input = keyInput.current;
    const controller = new AbortController();
    request.current = controller;
    return () => {
      if (input) input.value = "";
      controller.abort();
    };
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = keyInput.current;
    const controller = request.current;
    if (submitting.current || !input?.value || !athlete.trim() || !controller) return;
    const button = (event.nativeEvent as SubmitEvent).submitter;
    const action = button instanceof HTMLButtonElement && button.value === "save" ? "save" : "test";
    submitting.current = true;
    setPending(action);
    setError(null);
    setNotice(null);
    const generation = coordinator.getState().generation;
    const stillCurrent = () => !controller.signal.aborted && coordinator.getState().generation === generation;
    try {
      // Start exactly one direct request, then clear before awaiting its outcome.
      const result = action === "test"
        ? testConnection(input.value, athlete.trim(), controller.signal)
        : saveConnection(input.value, athlete.trim(), controller.signal);
      input.value = "";
      setHasKey(false);
      const response = await result;
      if (!stillCurrent()) return;
      if ("configured" in response) {
        // Cancel any earlier metadata read so it cannot overwrite this save.
        await queryClient.cancelQueries({ queryKey: CONNECTION_QUERY_KEY });
        if (!stillCurrent()) return;
        queryClient.setQueryData(CONNECTION_QUERY_KEY, response);
        setNotice("Connection saved.");
      } else {
        setNotice(`Test passed for ${response.athlete_id} (${response.timezone}). Not saved. Re-enter the API key to save.`);
      }
    } catch (caught) {
      if (stillCurrent()) setError(`${failureMessage(caught)} Re-enter the API key to try again.`);
    } finally {
      input.value = "";
      submitting.current = false;
      if (stillCurrent()) {
        setHasKey(false);
        setPending(null);
      }
    }
  }

  async function signOut() {
    setSigningOut(true);
    await coordinator.logout();
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight">velolab</h1>
        <div className="flex min-w-0 items-center gap-4 text-sm text-zinc-400">
          <span className="min-w-0 break-all">{identity.data?.email ?? (identity.isError ? "Signed in" : "…")}</span>
          <button type="button" onClick={() => void signOut()} disabled={signingOut} className={`${BUTTON_STYLE} shrink-0 text-zinc-100`}>
            Log out
          </button>
        </div>
      </header>
      <section aria-labelledby="connection-heading" className="max-w-lg">
        <h2 id="connection-heading" className="mb-2 text-lg font-medium">Connection</h2>
        <p className="mb-6 text-zinc-400">Connect intervals.icu. Testing checks your credentials; only Save stores them.</p>
        <div className="mb-6 border-y border-zinc-800 py-4 text-sm text-zinc-300" aria-live="polite">
          {connection.isPending && <p>Loading saved connection…</p>}
          {connection.data && (
            <>
              <p className="break-all">{connection.data.configured ? `Saved athlete: ${connection.data.athlete_id ?? "Unavailable"}` : "No saved connection."}</p>
              {connection.data.last_error_code && <p className="mt-2">The last connection attempt reported an error.</p>}
            </>
          )}
          {connection.isError && <p role="alert">Could not load saved connection status.</p>}
          <button type="button" disabled={connection.isFetching || pending !== null} onClick={() => void connection.refetch()} className={`${BUTTON_STYLE} mt-3`}>
            {connection.isFetching ? "Checking status…" : "Check saved status"}
          </button>
        </div>
        <form onSubmit={(event) => void onSubmit(event)} className="flex flex-col gap-4" aria-busy={pending !== null} autoComplete="off">
          <label className="flex flex-col gap-1 text-sm">
            Athlete ID
            <input
              type="text" name="athlete_id" required value={athlete} disabled={pending !== null}
              onChange={(event) => { setAthleteInput(event.target.value); setNotice(null); }}
              aria-describedby="athlete-hint" className={INPUT_STYLE}
            />
          </label>
          <p id="athlete-hint" className="-mt-2 text-sm text-zinc-400">Use your explicit intervals.icu athlete ID, not the alias 0. A saved connection cannot change athletes.</p>
          <label className="flex flex-col gap-1 text-sm">
            API key
            <input
              ref={keyInput} type="password" name="api_key" required autoComplete="off" spellCheck={false}
              disabled={pending !== null} onChange={(event) => setHasKey(event.target.value.length > 0)}
              aria-describedby="key-hint" className={INPUT_STYLE}
            />
          </label>
          <p id="key-hint" className="-mt-2 text-sm text-zinc-400">Cleared after every submission. Re-enter it for each Test or Save; saved keys are never shown here.</p>
          <p className="text-sm text-zinc-400">Local preview only. Enter a real key only after the owner has approved key custody and verified backup/restore.</p>
          <div className="flex flex-wrap gap-3">
            <button type="submit" value="test" disabled={pending !== null || !hasKey || !athlete.trim()} className={BUTTON_STYLE}>
              {pending === "test" ? "Testing…" : "Test connection"}
            </button>
            <button type="submit" value="save" disabled={pending !== null || !hasKey || !athlete.trim()} className={`${BUTTON_STYLE} bg-zinc-100 font-medium text-zinc-950 hover:bg-white`}>
              {pending === "save" ? "Saving…" : "Save connection"}
            </button>
          </div>
          <div aria-live="polite" className="break-words text-sm text-zinc-300">
            {notice && <p role="status">{notice}</p>}
          </div>
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        </form>
      </section>
      <ActivitiesSection configured={connection.data?.configured === true} />
    </main>
  );
}
