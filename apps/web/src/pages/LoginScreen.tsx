import { useState, type FormEvent } from "react";
import { coordinator } from "../auth/browser";
import { LoginFailedError, type LoginReason } from "../auth/coordinator";

const NOTICES: Partial<Record<LoginReason, string>> = {
  expired: "Your session ended. Please sign in again.",
  recovery: "We could not safely restore your session. Please sign in again.",
  "signed-out": "You have been signed out.",
  "signed-out-unconfirmed":
    "Logout could not be confirmed by the server, so reloading this page may sign you back in. The session expires on its own within 7 days; retry logout when online.",
  "signed-out-elsewhere": "You were signed out in another tab.",
};

export default function LoginScreen({ reason }: { reason: LoginReason | undefined }) {
  // useState gives a component its own remembered value; calling the setter
  // re-renders. These inputs are "controlled": React state is the source of truth.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault(); // stop the browser's own form submission/page reload
    setError(null);
    setPending(true);
    try {
      await coordinator.login(email, password);
      // Success flips the auth state and App swaps this screen out.
    } catch (caught) {
      setPassword("");
      setError(
        caught instanceof LoginFailedError && caught.kind === "invalid-credentials"
          ? "Invalid email or password."
          : "Could not sign in. Please try again.",
      );
      setPending(false);
    }
  }

  const notice = reason ? NOTICES[reason] : undefined;
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center p-6">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">velolab</h1>
      {notice && <p className="mb-4 rounded border border-zinc-800 bg-zinc-900 p-3 text-sm text-zinc-300">{notice}</p>}
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            name="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-base focus:border-zinc-400 focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-base focus:border-zinc-400 focus:outline-none"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-red-400">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-zinc-100 px-3 py-2 font-medium text-zinc-950 hover:bg-white disabled:opacity-50"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
