import { useState, type ReactNode } from "react";
import { api, type Result } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, Check, DataTable, Empty, ErrorBox, Field, Loading, MetricRow, Tabs, useFlash } from "../components/ui";
import { useFetch, useWebConfig } from "../hooks";
import { num, parseWindow, prefsFromConfig, prefsToValues, weightsTotal, weightsValid, windowText, type Prefs, type Tri } from "../lib/settings";

/**
 * Settings: preferences, matching weights, automation limits, scheduler, retries, browser, notifications and AI status.
 * Every save goes through the backend, which validates BEFORE writing. Secrets are never shown or edited here - only whether they are configured.
 * Saving needs ADMIN_TOKEN (held by the web server's proxy, never by the browser).
 */
const TABS = ["Profile & preferences", "Matching", "Automation", "Scheduler & retries", "Browser", "Notifications", "AI", "Sources & platforms", "Security"];

interface Ctx { cfg: any; canAdmin: boolean; reload: () => void; saved: (label: string, r: Result, restart?: boolean) => boolean }

function NotSaved({ result }: { result: Result | null }) {
  if (!result || result.ok) return null;
  return <Alert kind="error">Not saved: {result.error}{result.problems.length > 0 && <ul>{result.problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Alert>;
}

function NumberField({ label, value, onChange, min, max, step = 1, help }: { label: string; value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number; help?: string }) {
  return <Field label={label}><input type="number" value={Number.isFinite(value) ? value : ""} min={min} max={max} step={step} title={help} onChange={(e) => onChange(num(e.target.value))} /></Field>;
}
function TriSelect({ label, value, onChange }: { label: string; value: Tri; onChange: (v: Tri) => void }) {
  return <Field label={label}><select value={value} onChange={(e) => onChange(e.target.value as Tri)}><option value="unknown">unknown</option><option value="yes">yes</option><option value="no">no</option></select></Field>;
}
function SaveButton({ ctx, label, disabled }: { ctx: Ctx; label: string; disabled?: boolean }) {
  return <button className="primary" type="submit" disabled={!ctx.canAdmin || disabled}>{label}</button>;
}
function Form({ onSubmit, children }: { onSubmit: () => void; children: ReactNode }) {
  // noValidate: the browser's built-in constraint check (e.g. step={100000} on the salary) would silently swallow the submit; the BACKEND validates and the page shows its 'Not saved: ...' reasons.
  return <form noValidate onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>{children}</form>;
}

export default function Settings() {
  const cfgRes = useFetch("/api/settings");
  const resumes = useFetch("/api/resumes");
  const web = useWebConfig();
  const { setFlash } = useFlash();
  const [tab, setTab] = useState(TABS[0]);
  const cfg = cfgRes.data;

  const ctx: Ctx | null = cfg ? {
    cfg, canAdmin: web.adminEnabled, reload: cfgRes.reload,
    saved: (label, r, restart = false) => {
      if (r.ok) { setFlash(`${label} saved.${restart ? " Restart the API for it to take effect." : ""}`); cfgRes.reload(); }
      return r.ok;
    },
  } : null;

  return (
    <>
      <PageHeader icon="⚙️" title="Settings" />
      {cfgRes.loading && !cfg && <Loading />}
      <ErrorBox result={cfgRes.result} />
      {ctx && (
        <>
          {web.loaded && !web.adminEnabled && <Alert kind="warning">Read-only: set ADMIN_TOKEN in .env (and restart the web server and the API) to save changes.</Alert>}
          <Tabs tabs={TABS} value={tab} onChange={setTab} />
          {tab === TABS[0] && <PreferencesTab ctx={ctx} active={(resumes.data as any[] | undefined)?.find((r) => r.is_active)} />}
          {tab === TABS[1] && <MatchingTab ctx={ctx} />}
          {tab === TABS[2] && <AutomationTab ctx={ctx} />}
          {tab === TABS[3] && <SchedulerTab ctx={ctx} />}
          {tab === TABS[4] && <BrowserTab ctx={ctx} />}
          {tab === TABS[5] && <NotificationsTab ctx={ctx} />}
          {tab === TABS[6] && <AiTab ctx={ctx} />}
          {tab === TABS[7] && <SourcesTab ctx={ctx} />}
          {tab === TABS[8] && <SecurityTab ctx={ctx} />}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- preferences
function PreferencesTab({ ctx, active }: { ctx: Ctx; active: any }) {
  const [f, setF] = useState<Prefs>(() => prefsFromConfig(ctx.cfg.preferences));
  const [res, setRes] = useState<Result | null>(null);
  const [info, setInfo] = useState("");
  const set = <K extends keyof Prefs>(k: K, v: Prefs[K]) => setF((p) => ({ ...p, [k]: v }));
  const submit = async () => {
    setInfo("");
    const r = await api.put("/api/settings/preferences", { admin: true, json: { values: prefsToValues(f) } });
    setRes(r);
    if (r.ok && !r.data.saved) setInfo("Nothing changed.");
    else ctx.saved("Preferences", r);
  };
  return (
    <>
      <p><b>Candidate profile:</b> {active ? <>active resume <i>{active.original_filename}</i> — edit and review it on the <b>Resume Manager</b> page.</> : <>no active resume yet — upload one on <b>Resume Manager</b>.</>}</p>
      <p className="muted">Job preferences drive discovery and eligibility checks. Anything left empty is treated as UNKNOWN, never as &apos;ok&apos;.</p>
      <Form onSubmit={submit}>
        <div className="grid cols-2">
          <Field label="Preferred roles (one per line or comma separated)"><textarea value={f.titles} onChange={(e) => set("titles", e.target.value)} /></Field>
          <Field label="Preferred locations"><textarea value={f.locations} onChange={(e) => set("locations", e.target.value)} /></Field>
          <Field label="Preferred skills"><textarea value={f.skills} onChange={(e) => set("skills", e.target.value)} /></Field>
          <Field label="Only these companies (empty = all configured)"><textarea value={f.companies} onChange={(e) => set("companies", e.target.value)} /></Field>
        </div>
        <div className="grid cols-4">
          <TriSelect label="Remote" value={f.remote} onChange={(v) => set("remote", v)} />
          <TriSelect label="Hybrid" value={f.hybrid} onChange={(v) => set("hybrid", v)} />
          <TriSelect label="On-site" value={f.onsite} onChange={(v) => set("onsite", v)} />
          <TriSelect label="Open to relocation" value={f.reloc} onChange={(v) => set("reloc", v)} />
        </div>
        <div>
          <Check label="Remote jobs must also be in my preferred locations (hides e.g. 'Remote - United States')" checked={f.remoteIn} onChange={(v) => set("remoteIn", v)} />
        </div>
        <div className="grid cols-4">
          <NumberField label="Experience min (years, 0 = unset)" value={f.xmin} min={0} max={60} step={0.5} onChange={(n) => set("xmin", n)} />
          <NumberField label="Experience max (years, 0 = unset)" value={f.xmax} min={0} max={60} step={0.5} onChange={(n) => set("xmax", n)} />
          <NumberField label="Minimum salary (annual, 0 = unset)" value={f.sal} min={0} step={100000} onChange={(n) => set("sal", n)} />
          <Field label="Salary currency"><input value={f.cur} maxLength={3} onChange={(e) => set("cur", e.target.value)} /></Field>
        </div>
        <div className="grid cols-2">
          <Field label="Notice period (used for screening answers; never guessed)"><input value={f.notice} onChange={(e) => set("notice", e.target.value)} /></Field>
          <NumberField label="Only jobs posted within (days, 0 = any)" value={f.posted} min={0} max={3650} onChange={(n) => set("posted", n)} />
        </div>
        <Field label="Employment types"><input value={f.types} onChange={(e) => set("types", e.target.value)} /></Field>
        <Field label="Work authorisation (countries)"><input value={f.auth} onChange={(e) => set("auth", e.target.value)} /></Field>
        <div className="grid cols-2">
          <Field label="Keywords to include"><input value={f.kin} onChange={(e) => set("kin", e.target.value)} /></Field>
          <Field label="Keywords to exclude"><input value={f.kex} onChange={(e) => set("kex", e.target.value)} /></Field>
        </div>
        <SaveButton ctx={ctx} label="💾 Save preferences" />
      </Form>
      {info && <Alert>{info}</Alert>}
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- matching (NOT a form: the weights total updates live as you type)
function MatchingTab({ ctx }: { ctx: Ctx }) {
  const sc = ctx.cfg.scoring;
  const [weights, setWeights] = useState<Record<string, number>>(() => ({ ...sc.weights }));
  const [th, setTh] = useState<Record<string, number>>(() => ({ excellent: sc.thresholds.excellent, strong: sc.thresholds.strong, good: sc.thresholds.good, moderate: sc.thresholds.moderate }));
  const [rec, setRec] = useState<number>(Number(sc.recommendation_threshold));
  const [cov, setCov] = useState<number>(Number(sc.min_data_coverage));
  const [pol, setPol] = useState<string>(sc.missing_data_policy);
  const [res, setRes] = useState<Result | null>(null);
  const [info, setInfo] = useState("");
  const { setFlash } = useFlash();
  const total = weightsTotal(weights), valid = weightsValid(weights);
  const submit = async () => {
    setInfo("");
    const r = await api.put("/api/settings/scoring", { admin: true, json: { weights, thresholds: th, recommendation_threshold: rec, min_data_coverage: cov, missing_data_policy: pol } });
    setRes(r);
    if (!r.ok) return;
    if (r.data.saved) { setFlash(`Saved. scoring_version is now ${r.data.scoring_version}. ${r.data.note}`); ctx.reload(); }
    else setInfo("Nothing changed.");
  };
  const title = (k: string) => k.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return (
    <>
      <p className="muted">Scoring version <b>{sc.scoring_version}</b> · config hash <code>{sc.config_hash}</code>. Changing weights bumps the version and keeps a backup; saved matches are not reused - re-run them to rescore.</p>
      <div className="grid cols-4">
        {Object.keys(weights).map((k) => <NumberField key={k} label={title(k)} value={weights[k]} min={0} max={100} onChange={(n) => setWeights((w) => ({ ...w, [k]: n }))} />)}
      </div>
      <Alert kind={valid ? "success" : "error"}>Weights total {Number(total.toFixed(6))} (must be 100)</Alert>
      <div className="grid cols-4">
        {(["excellent", "strong", "good", "moderate"] as const).map((k) => <NumberField key={k} label={`${title(k)} ≥`} value={th[k]} min={0} max={100} onChange={(n) => setTh((t) => ({ ...t, [k]: n }))} />)}
      </div>
      <div className="grid cols-3">
        <NumberField label="Recommendation threshold" value={rec} min={0} max={100} onChange={setRec} />
        <NumberField label="Minimum data coverage %" value={cov} min={0} max={100} onChange={setCov} />
        <Field label="Missing data policy"><select value={pol} onChange={(e) => setPol(e.target.value)}><option value="normalize">normalize</option><option value="zero">zero</option></select></Field>
      </div>
      <button className="primary" disabled={!ctx.canAdmin || !valid} onClick={submit}>💾 Save matching settings</button>
      {info && <Alert>{info}</Alert>}
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- automation caps
function AutomationTab({ ctx }: { ctx: Ctx }) {
  const { caps, effective: eff } = ctx.cfg.automation;
  const keys: Array<[string, string, number, number]> = [
    ["max_applications_per_day", "Applications submitted / day", 0, 100], ["max_applications_per_platform_per_day", "… per platform / day", 0, 50], ["daily_application_target", "Daily target (a goal, not a quota)", 0, 100],
    ["max_jobs_discovered_per_day", "Jobs discovered / day", 0, 2000], ["max_jobs_processed_per_day", "Jobs prepared / day", 0, 500], ["max_concurrent_workers", "Concurrent workers", 1, 4], ["min_match_score", "Minimum match score to queue", 0, 100],
  ];
  const [v, setV] = useState<Record<string, number>>(() => Object.fromEntries(keys.map(([k]) => [k, Number(caps[k])])));
  const [dw, setDw] = useState(windowText(caps.discovery_window));
  const [aw, setAw] = useState(windowText(caps.application_window));
  const [res, setRes] = useState<Result | null>(null);
  const submit = async () => {
    const r = await api.put("/api/settings/automation", { admin: true, json: { values: { ...v, discovery_window: parseWindow(dw), application_window: parseWindow(aw) } } });
    setRes(r); ctx.saved("Automation caps", r);
  };
  return (
    <>
      <p className="muted">These are the HARD CAPS in config/automation_limits.yaml. The Automation page can lower them day to day; they can never be raised there. The courtesy delay between actions is not editable.</p>
      <Form onSubmit={submit}>
        <div className="grid cols-3">{keys.map(([k, label, min, max]) => <NumberField key={k} label={label} value={v[k]} min={min} max={max} onChange={(n) => setV((p) => ({ ...p, [k]: n }))} />)}</div>
        <div className="grid cols-2">
          <Field label="Discovery window (HH:MM-HH:MM)"><input value={dw} onChange={(e) => setDw(e.target.value)} /></Field>
          <Field label="Application window (HH:MM-HH:MM)"><input value={aw} onChange={(e) => setAw(e.target.value)} /></Field>
        </div>
        <SaveButton ctx={ctx} label="💾 Save automation caps" />
      </Form>
      <NotSaved result={res} />
      <p className="muted">Currently effective (after dashboard overrides): {eff.max_applications_per_day} submissions/day · {eff.max_jobs_processed_per_day} prepared/day · min score {Number(eff.min_match_score)}</p>
    </>
  );
}

// ---------------------------------------------------------------- scheduler + retries
function SchedulerTab({ ctx }: { ctx: Ctx }) {
  const s = ctx.cfg.scheduler, rt = ctx.cfg.retry;
  const [tz, setTz] = useState<string>(s.timezone);
  const [dt, setDt] = useState<string>(s.discovery_time);
  const [rtm, setRtm] = useState<string>(s.report_time);
  const [retries, setRetries] = useState<number>(Number(rt.max_retries_per_application));
  const [backoff, setBackoff] = useState<number>(Number(rt.retry_backoff_seconds));
  const [subRetries, setSubRetries] = useState<number>(Number(rt.max_submit_retries));
  const [auto, setAuto] = useState<boolean>(Boolean(s.autostart));
  const [res, setRes] = useState<Result | null>(null);
  const submit = async () => {
    const r1 = await api.put("/api/settings/automation", { admin: true, json: { values: { discovery_time: dt, report_time: rtm, max_retries_per_application: Math.trunc(retries), retry_backoff_seconds: Math.trunc(backoff) } } });
    if (!r1.ok) { setRes(r1); return; }
    const r2 = await api.put("/api/settings/env", { admin: true, json: { values: { SCHEDULER_TIMEZONE: tz, SCHEDULER_AUTOSTART: auto, MAX_SUBMIT_RETRIES: Math.trunc(subRetries) } } });
    setRes(r2); ctx.saved("Scheduler and retry settings", r2, true);
  };
  return (
    <>
      <MetricRow items={[["Scheduler", s.enabled ? "enabled" : "stopped", "Start/stop it on the Automation page"], ["Time zone", s.timezone], ["Discovery at", s.discovery_time], ["Report at", s.report_time]]} />
      <Form onSubmit={submit}>
        <div className="grid cols-3">
          <Field label="Time zone (IANA name)"><input value={tz} onChange={(e) => setTz(e.target.value)} /></Field>
          <Field label="Daily discovery time (HH:MM)"><input value={dt} onChange={(e) => setDt(e.target.value)} /></Field>
          <Field label="Daily report time (HH:MM)"><input value={rtm} onChange={(e) => setRtm(e.target.value)} /></Field>
        </div>
        <div className="grid cols-3">
          <NumberField label="Retries per application" value={retries} min={0} max={10} onChange={setRetries} />
          <NumberField label="Retry back-off (seconds, doubles each time)" value={backoff} min={0} max={3600} onChange={setBackoff} />
          <NumberField label="Pre-submit page retries" value={subRetries} min={0} max={5} onChange={setSubRetries} help="Only before the submit click; never after." />
        </div>
        <Check label="Start the scheduler automatically when the API starts" checked={auto} onChange={setAuto} />
        <SaveButton ctx={ctx} label="💾 Save scheduler & retry settings" />
      </Form>
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- browser
function BrowserTab({ ctx }: { ctx: Ctx }) {
  const b = ctx.cfg.browser;
  const [headless, setHeadless] = useState<boolean>(Boolean(b.headless));
  const [channel, setChannel] = useState<string>(b.channel ?? "");
  const [timeout, setTimeout_] = useState<number>(Number(b.timeout_ms));
  const [slow, setSlow] = useState<number>(Number(b.slow_mo_ms));
  const [persist, setPersist] = useState<boolean>(Boolean(b.persist_sessions));
  const [res, setRes] = useState<Result | null>(null);
  const submit = async () => {
    const r = await api.put("/api/settings/env", { admin: true, json: { values: { BROWSER_HEADLESS: headless, BROWSER_CHANNEL: channel, BROWSER_TIMEOUT_MS: Math.trunc(timeout), BROWSER_SLOW_MO_MS: Math.trunc(slow), PERSIST_BROWSER_SESSIONS: persist } } });
    setRes(r); ctx.saved("Browser settings", r, true);
  };
  return (
    <>
      <p className="muted">Browser automation only runs where your platform policy allows it. The master switch APPLICATION_AUTOMATION_ENABLED is deliberately NOT editable here: {ctx.cfg.security.master_automation_switch ? <b>currently ON.</b> : <b>currently OFF (manual handoff only).</b>}</p>
      <Form onSubmit={submit}>
        <div className="grid cols-4">
          <Check label="Headless (no visible window)" checked={headless} onChange={setHeadless} />
          <Field label="Browser"><select value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">Playwright Chromium (auto-fallback)</option><option value="msedge">msedge</option><option value="chrome">chrome</option></select></Field>
          <NumberField label="Timeout (ms)" value={timeout} min={1000} max={120000} step={1000} onChange={setTimeout_} />
          <NumberField label="Slow-motion (ms)" value={slow} min={0} max={2000} onChange={setSlow} />
        </div>
        <Check label="Keep logins in an encrypted session file (needs SECRET_KEY; cookies only, never passwords)" checked={persist} onChange={setPersist} />
        <SaveButton ctx={ctx} label="💾 Save browser settings" />
      </Form>
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- notifications
function NotificationsTab({ ctx }: { ctx: Ctx }) {
  const n = ctx.cfg.notifications;
  const [en, setEn] = useState<boolean>(Boolean(n.enabled));
  const [rc, setRc] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const submit = async () => {
    const values: Record<string, unknown> = { NOTIFICATIONS_ENABLED: en };
    if (rc.trim()) values.NOTIFICATION_RECIPIENT = rc.trim();
    const r = await api.put("/api/settings/env", { admin: true, json: { values } });
    setRes(r); ctx.saved("Notification settings", r, true);
  };
  return (
    <>
      <MetricRow items={[["Status", n.configured ? "configured" : "not configured", "Works without e-mail"], ["Recipient", n.recipient || "not set"], ["Gmail credentials", n.credentials_stored ? "stored (encrypted)" : "not stored"]]} />
      <p className="muted">Gmail uses OAuth 2.0 with your own client. Credentials are never typed here or shown: store them once with <code>{n.setup_command}</code> (encrypted with SECRET_KEY).</p>
      <Form onSubmit={submit}>
        <Check label="Send e-mail notifications" checked={en} onChange={setEn} />
        <Field label="Recipient e-mail"><input type="email" value={rc} placeholder={n.recipient || "you@example.com"} title="Leave empty to keep the current recipient." onChange={(e) => setRc(e.target.value)} /></Field>
        <div className="muted">Leave empty to keep the current recipient.</div>
        <SaveButton ctx={ctx} label="💾 Save notification settings" />
      </Form>
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- AI
function AiTab({ ctx }: { ctx: Ctx }) {
  const ai = ctx.cfg.ai;
  const [e1, setE1] = useState<boolean>(Boolean(ai.matching_enabled));
  const [e2, setE2] = useState<boolean>(Boolean(ai.matching_consent));
  const [e3, setE3] = useState<boolean>(Boolean(ai.extraction_enabled));
  const [e4, setE4] = useState<boolean>(Boolean(ai.documents_consent));
  const [res, setRes] = useState<Result | null>(null);
  const submit = async () => {
    const r = await api.put("/api/settings/env", { admin: true, json: { values: { ENABLE_AI_MATCHING: e1, AI_CONSENT_EXTERNAL_PROCESSING: e2, AI_EXTRACTION_ENABLED: e3, AI_DOCUMENTS_CONSENT: e4 } } });
    setRes(r); ctx.saved("AI settings", r, true);
  };
  return (
    <>
      <MetricRow items={[["Provider", ai.provider], ["API key", ai.api_key_configured ? "configured" : "not configured", "The key is never shown; set AI_PROVIDER and ANTHROPIC_API_KEY or GEMINI_API_KEY in .env"], ["Model", ai.model], ["Active", ai.active ? "yes" : "no", "Needs key + AI matching + consent"]]} />
      <p className="muted">AI is optional: everything works without it. These switches control whether text may be sent to the provider (explicit consent).</p>
      <Form onSubmit={submit}>
        <Check label="Allow AI to refine job analysis / wording (ENABLE_AI_MATCHING)" checked={e1} onChange={setE1} />
        <Check label="I consent to sending job text to the AI provider (never resume PII)" checked={e2} onChange={setE2} />
        <Check label="AI-assisted resume extraction (sends resume text; off by default)" checked={e3} onChange={setE3} />
        <Check label="I consent to AI wording of documents (fact pack only, no contact details)" checked={e4} onChange={setE4} />
        <SaveButton ctx={ctx} label="💾 Save AI settings" />
      </Form>
      <NotSaved result={res} />
    </>
  );
}

// ---------------------------------------------------------------- sources & platforms (READ-ONLY by design)
function SourcesTab({ ctx }: { ctx: Ctx }) {
  const cfg = ctx.cfg;
  const sites = cfg.authorized_sites as unknown[];
  const cut = (s: string | null) => (s ?? "").slice(0, 160);
  return (
    <>
      <p className="muted">Platform permissions are READ-ONLY here: enabling a platform is a deliberate edit of config/platform_policies.yaml / application_policies.yaml after you have checked its terms. Unknown = disabled.</p>
      <h3>Discovery sources</h3>
      <DataTable rows={cfg.sources as any[]} columns={[{ header: "Source", render: (x) => x.source }, { header: "Status", render: (x) => x.status }, { header: "Automated discovery", render: (x) => (x.automated_discovery ? "yes" : "no") },
        { header: "Manual import", render: (x) => (x.manual_import ? "yes" : "no") }, { header: "Reviewed", render: (x) => x.reviewed_on || "" }, { header: "Basis", render: (x) => cut(x.basis) }]} />
      <h3>Application platforms</h3>
      <DataTable rows={cfg.application_platforms as any[]} columns={[{ header: "Platform", render: (x) => x.platform }, { header: "Application mode", render: (x) => x.status }, { header: "Basis", render: (x) => cut(x.basis) }]} />
      <p>Authorised sites for automated applying: <b>{sites.length}</b>{sites.length === 0 && " (none: everything is manual handoff)"}</p>
      <h3>Companies watched for discovery</h3>
      {cfg.companies?.length ? <DataTable rows={cfg.companies as any[]} columns={Object.keys(cfg.companies[0]).map((k) => ({ header: k, render: (x: any) => (x[k] === null || x[k] === undefined ? "" : String(x[k])) }))} />
        : <Empty message="No companies configured." hint="Add them in config/career_sources.yaml; nothing is discovered automatically until you do." />}
    </>
  );
}

// ---------------------------------------------------------------- security (status only: secrets are never shown)
function SecurityTab({ ctx }: { ctx: Ctx }) {
  const sec = ctx.cfg.security;
  return (
    <>
      <MetricRow items={[["Admin token", sec.admin_token_configured ? "configured" : "not set", "Needed to save settings and operate automation"], ["SECRET_KEY", sec.secret_key_configured ? "configured" : "not set", "Encrypts saved sessions and Gmail credentials"],
        ["Master automation switch", sec.master_automation_switch ? "ON" : "OFF", "Edit APPLICATION_AUTOMATION_ENABLED in .env"]]} />
      <p className="muted">Secrets (API keys, tokens, passwords, OAuth credentials) are never displayed or editable in this app. {ctx.cfg.restart_note}</p>
    </>
  );
}
