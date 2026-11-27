import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { PageHeader } from "../components/Layout";
import { Alert, ConfirmButton, DataTable, ErrorBox, useFlash } from "../components/ui";
import { fmtDt } from "../format";
import { useFetch } from "../hooks";

/** Your own password; for the owner also: who can sign in, add a person, set a new password, remove a sign-in. */
export default function Account() {
  const { user, authEnabled } = useAuth();
  if (!authEnabled || !user) return <><PageHeader icon="👤" title="My account" /><Alert kind="info">Sign-in is switched off (AUTH_ENABLED=false): there is only one person.</Alert></>;
  return (
    <>
      <PageHeader icon="👤" title="My account" />
      <p>Signed in as <b>{user.name}</b>{user.role === "owner" ? " (owner)" : ""}. Everything you see in JobPilot (resume, jobs, applications, Gmail) is yours alone.</p>
      <ChangePassword />
      {user.role === "owner" && <Users me={user.user_id} />}
    </>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = await api.post("/api/auth/password", { json: { current_password: current, new_password: next } });
    setBusy(false);
    if (r.ok) window.location.assign("/");            // signed out everywhere: sign in with the new password
    else setErr(r.error ?? "Not changed");
  }
  return (
    <section className="card" aria-label="Change password">
      <h3>🔑 Change my password</h3>
      <form onSubmit={submit} className="row">
        <label>Current password <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>{" "}
        <label>New password (8+ characters) <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} /></label>{" "}
        <button type="submit" disabled={busy}>Change password</button>
      </form>
      <p className="muted">You are signed out everywhere afterwards and sign in again with the new password.</p>
      {err && <Alert kind="error">{err}</Alert>}
    </section>
  );
}

function Users({ me }: { me: string }) {
  const list = useFetch<any>("/api/auth/users");
  const { setFlash } = useFlash();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const users: any[] = list.data?.items ?? [];

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = await api.post("/api/auth/users", { json: { name, email, password } });
    setBusy(false);
    if (r.ok) { setFlash(`${name} can now sign in with ${email}. Give them the password yourself.`); setName(""); setEmail(""); setPassword(""); list.reload(); }
    else setErr(r.error ?? "Not added");                      // keeps what was typed
  }
  async function reset(u: any) {
    const pw = window.prompt(`New password for ${u.name} (at least 8 characters):`);
    if (!pw) return;
    const r = await api.post(`/api/auth/users/${u.user_id}/password`, { json: { new_password: pw } });
    if (r.ok) setFlash(`New password set for ${u.name}. Tell them yourself.`); else setErr(r.error ?? "Not changed");
  }
  async function remove(u: any) {
    const r = await api.del(`/api/auth/users/${u.user_id}`);
    if (r.ok) { setFlash(`${u.name} can no longer sign in. Their data is kept.`); list.reload(); } else setErr(r.error ?? "Not removed");
  }

  return (
    <section className="card" aria-label="Users">
      <h3>👥 People who can use this JobPilot</h3>
      <ErrorBox result={list.result} />
      <DataTable rows={users} rowKey={(u) => u.user_id} columns={[
        { header: "Name", render: (u) => u.name }, { header: "E-mail", render: (u) => u.email },
        { header: "Role", render: (u) => (u.role === "owner" ? "Owner" : "User") },
        { header: "Last sign-in", render: (u) => (u.last_login_at ? fmtDt(u.last_login_at) : "never") },
        { header: "", render: (u) => (u.user_id === me ? <span className="muted">you</span> : (
          <>
            <button type="button" className="link-btn" onClick={() => reset(u)}>Set new password</button>{" "}
            <ConfirmButton label="Remove" question={`Remove ${u.name}'s sign-in? Their data is kept.`} onConfirm={() => remove(u)} />
          </>)) },
      ]} />
      <h4>➕ Add a person</h4>
      <p className="muted">They get their own, empty JobPilot: their own resume, jobs, applications, preferences and Gmail. Nothing of yours is visible to them.</p>
      <form onSubmit={add} className="row" aria-label="Add a person">
        <label>Name <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} /></label>{" "}
        <label>E-mail <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>{" "}
        <label>Password (8+ characters) <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} /></label>{" "}
        <button type="submit" disabled={busy}>Add</button>
      </form>
      {err && <Alert kind="error">{err}</Alert>}
    </section>
  );
}
