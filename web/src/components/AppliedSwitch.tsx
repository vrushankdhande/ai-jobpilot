import { useEffect, useState } from "react";
import { api } from "../api";
import { isApplied } from "../lib/matching";

/** "I applied to this job" switch. JobPilot never applies for you: flipping it on only RECORDS that you applied yourself (after a confirmation).
 *  Flipping it off undoes YOUR record (e.g. a mistaken click); the server refuses that for an application JobPilot submitted or one that is
 *  verified. Both steps stay in the application's history. */
export function AppliedSwitch({ jobId, title, company, state, onRecorded, onError }:
  { jobId: string; title?: string | null; company?: string | null; state: string | null | undefined; onRecorded: () => void; onError?: (msg: string) => void }) {
  const [busy, setBusy] = useState(false);
  const on = isApplied(state);
  const name = `${title || "job"}${company ? ` at ${company}` : ""}`;
  async function flip() {
    if (busy) return;
    const what = `"${title || "this job"}"${company ? ` at ${company}` : ""}`;
    const ask = on
      ? `Mark ${what} as NOT applied?\n\nThis undoes the "applied" you recorded (for example after a mistaken click). The job goes back to "apply yourself".`
      : `Record that you applied to ${what}?\n\nJobPilot does not apply for you - this only records that YOU applied. You can switch it off again if it was a mistake.`;
    if (!window.confirm(ask)) return;
    setBusy(true);
    const res = await api.post(on ? "/api/applications/undo-applied" : "/api/applications/record-applied", { json: { job_id: jobId } });
    setBusy(false);
    if (!res.ok) { onError?.(res.error ?? (on ? "Could not undo it" : "Could not record it")); return; }
    onRecorded();
  }
  return (
    <label className="switch" title={on ? "You applied (recorded) - click to undo a mistake" : "Click when you have applied to this job"}>
      <input type="checkbox" role="switch" aria-label={`Applied: ${name}`} checked={on} disabled={busy} onChange={() => void flip()} />
      <span className="slider" /><span className="switch-text">{busy ? "Saving…" : on ? "Applied" : "No"}</span>
    </label>
  );
}

/** Application state per job for the jobs on screen (read-only lookup). */
export function useApplicationStates(jobIds: string[]): { states: Record<string, string>; reload: () => void } {
  const [states, setStates] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const key = jobIds.join(",");
  useEffect(() => {
    if (!jobIds.length) { setStates({}); return; }
    let live = true;
    api.post<Record<string, string>>("/api/applications/states", { json: { job_ids: jobIds } }).then((r) => { if (live && r.ok) setStates(r.data ?? {}); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);
  return { states, reload: () => setTick((t) => t + 1) };
}
