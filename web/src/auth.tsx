import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "./api";
import { Alert, Loading } from "./components/ui";

export interface User { user_id: string; name: string; role: "owner" | "user" }
interface AuthState { user: User | null; authEnabled: boolean; logout: () => Promise<void> }

const AuthCtx = createContext<AuthState>({ user: null, authEnabled: false, logout: async () => {} });
export const useAuth = () => useContext(AuthCtx);

/** Saved form values and search history belong to the person who typed them: forget them when someone signs in or out. */
export function forgetBrowserState() {
  try { sessionStorage.clear(); } catch { /* storage unavailable */ }
  try { localStorage.clear(); } catch { /* storage unavailable */ }
}

/** Shows the app only to a signed-in person (or when sign-in is switched off). First start: create the owner account. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ loaded: boolean; enabled: boolean; setup: boolean; user: User | null; error: string | null }>(
    { loaded: false, enabled: false, setup: false, user: null, error: null });
  const load = useCallback(async () => {
    const r = await api.get("/api/auth/status");
    // backend down: open the app anyway (its pages say "Backend unreachable"; no data can be read). Once it is back, any call answers 401 -> sign-in.
    if (!r.ok) { setState((s) => ({ ...s, loaded: true, error: r.error ?? "Backend unreachable" })); return; }
    setState({ loaded: true, enabled: r.data.auth_enabled, setup: r.data.setup_needed, user: r.data.user, error: null });
  }, []);
  useEffect(() => {
    load();
    const onExpired = () => load();
    window.addEventListener("jobpilot:login-required", onExpired);
    return () => window.removeEventListener("jobpilot:login-required", onExpired);
  }, [load]);
  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    forgetBrowserState();
    window.location.assign("/");
  }, []);
  const signedIn = (user: User) => { forgetBrowserState(); setState((s) => ({ ...s, setup: false, user })); };

  if (!state.loaded) return <Loading text="Starting…" />;
  if (state.enabled && !state.user) return state.setup ? <SetupForm onDone={signedIn} /> : <LoginForm onDone={signedIn} />;
  return <AuthCtx.Provider value={{ user: state.user, authEnabled: state.enabled, logout }}>{children}</AuthCtx.Provider>;
}

function LoginForm({ onDone }: { onDone: (u: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = await api.post("/api/auth/login", { json: { email, password } });
    setBusy(false);
    if (r.ok) onDone(r.data.user); else { setErr(r.error ?? "Sign-in failed"); setPassword(""); }
  }
  return (
    <form className="auth-box" onSubmit={submit} aria-label="Sign in">
      <h2>🧭 AI JobPilot</h2>
      <p className="muted">Sign in to see your own resume, jobs and applications.</p>
      <label>E-mail<input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      {err && <Alert kind="error">{err}</Alert>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <p className="muted">No account? Ask the owner of this JobPilot to add you. Forgot your password? The owner can set a new one.</p>
    </form>
  );
}

function SetupForm({ onDone }: { onDone: (u: User) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== again) { setErr("The two passwords are not the same."); return; }
    setBusy(true); setErr(null);
    const r = await api.post("/api/auth/setup", { json: { name, email, password } });
    setBusy(false);
    if (r.ok) onDone(r.data.user); else setErr(r.error ?? "Could not create the account");
  }
  return (
    <form className="auth-box" onSubmit={submit} aria-label="Create the owner account">
      <h2>🧭 Welcome to AI JobPilot</h2>
      <p>Create <b>your</b> account first. You become the <b>owner</b>: everything already in JobPilot (resume, jobs, applications, Gmail) stays yours,
        and you can add other people later. Each of them gets their own, separate data.</p>
      <label>Your name<input autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} /></label>
      <label>E-mail (used to sign in)<input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>Password (at least 8 characters)<input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} /></label>
      <label>Password again<input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required minLength={8} /></label>
      {err && <Alert kind="error">{err}</Alert>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Creating…" : "Create my account"}</button>
    </form>
  );
}
