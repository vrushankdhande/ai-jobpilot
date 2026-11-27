import { useEffect, useState } from "react";
import { api } from "../api";

export interface ListedSkillCheck { skills: string[]; have: string[]; related: Array<{ skill: string; via: string | null }>; missing: string[]; not_checked: string[]; checked: number }

/** Skills the job site listed for each job on screen (Instahyre alerts), compared with your profile. Information only - not a score. */
export function useListedSkills(jobIds: string[], reloadKey = 0): Record<string, ListedSkillCheck> {
  const [data, setData] = useState<Record<string, ListedSkillCheck>>({});
  const key = jobIds.join(",");
  useEffect(() => {
    if (!jobIds.length) { setData({}); return; }
    let live = true;
    api.post<Record<string, ListedSkillCheck>>("/api/matching/listed-skills", { json: { job_ids: jobIds } }).then((r) => { if (live) setData(r.ok ? r.data ?? {} : {}); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadKey]);
  return data;
}

export function ListedSkillsCell({ check }: { check: ListedSkillCheck | undefined }) {
  if (!check) return <span className="muted">—</span>;
  const parts = [`${check.have.length} you have`, check.related.length ? `${check.related.length} related` : "", check.missing.length ? `${check.missing.length} missing` : "",
    check.not_checked.length ? `${check.not_checked.length} not checked` : ""].filter(Boolean);
  return (
    <details className="listed-skills">
      <summary title="Skills the job site listed, compared with your profile (information only, not a match score)">
        {parts.join(" · ")}
      </summary>
      {check.have.length > 0 && <div>✅ {check.have.join(", ")}</div>}
      {check.related.length > 0 && <div>≈ {check.related.map((r) => (r.via ? `${r.skill} (you have ${r.via})` : r.skill)).join(", ")}</div>}
      {check.missing.length > 0 && <div>❌ {check.missing.join(", ")}</div>}
      {check.not_checked.length > 0 && <div className="muted">Not checked: {check.not_checked.join(", ")}</div>}
    </details>
  );
}
