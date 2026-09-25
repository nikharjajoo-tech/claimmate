import { useCallback, useEffect, useState } from "react";
import { adjusterApi, HttpError } from "../lib/adjuster";
import { ClaimDetailView } from "./ClaimDetailView";
import { QueueView } from "./QueueView";

type Auth = "checking" | "disabled" | "signed_out" | "signed_in";

export default function AdjusterApp() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [selected, setSelected] = useState<string | null>(null);
  const [queueVersion, setQueueVersion] = useState(0);

  const check = useCallback(async () => {
    try {
      const me = await adjusterApi.me();
      setAuth(!me.enabled ? "disabled" : me.signed_in ? "signed_in" : "signed_out");
    } catch {
      setAuth("disabled");
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  // Stable identity: the queue's polling effect depends on it and would otherwise restart every render.
  const onUnauthorized = useCallback((e: unknown) => {
    if (e instanceof HttpError && e.status === 401) setAuth("signed_out");
  }, []);

  return (
    <div className="app adjuster">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>
            ◉
          </span>
          ClaimVoice
          <span className="muted">Adjuster review</span>
        </div>
        <div className="actions">
          <a className="btn" href="#/">
            Claimant view
          </a>
          {auth === "signed_in" && (
            <button
              className="btn"
              onClick={async () => {
                await adjusterApi.logout().catch(() => undefined);
                setSelected(null);
                setAuth("signed_out");
              }}
            >
              Sign out
            </button>
          )}
        </div>
      </header>

      {auth === "checking" && <p className="muted">Loading…</p>}
      {auth === "disabled" && (
        <section className="panel narrow">
          <h2>Adjuster view is off</h2>
          <p>
            Set <code>CLAIMVOICE_ADJUSTER_PASSCODE</code> in the <code>.env</code> file and restart the server to turn
            it on.
          </p>
        </section>
      )}
      {auth === "signed_out" && <SignIn onSignedIn={() => setAuth("signed_in")} />}
      {auth === "signed_in" && (
        <main className="layout review">
          <QueueView
            selected={selected}
            onSelect={setSelected}
            version={queueVersion}
            onUnauthorized={onUnauthorized}
          />
          {selected ? (
            <ClaimDetailView
              key={selected}
              claimId={selected}
              onChanged={() => setQueueVersion((v) => v + 1)}
              onUnauthorized={onUnauthorized}
            />
          ) : (
            <section className="panel empty-detail">
              <p className="muted">Select a claim from the queue. Urgent claims are listed first.</p>
            </section>
          )}
        </main>
      )}
    </div>
  );
}

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await adjusterApi.login(passcode);
      onSignedIn();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel narrow signin" onSubmit={submit}>
      <h2>Sign in to review claims</h2>
      <label htmlFor="passcode">Adjuster passcode</label>
      <input
        id="passcode"
        type="password"
        autoComplete="current-password"
        value={passcode}
        onChange={(e) => setPasscode(e.target.value)}
        autoFocus
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={!passcode || busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
