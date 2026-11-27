import { describe, expect, it } from "vitest";
import { activitySentence, activitySummary, headline, nextDiscovery, shortDay, splitByValue, sumLastDays } from "./dashboard";

describe("dashboard summary logic", () => {
  it("sums only the last n days of the activity series", () => {
    const series = [{ date: "d1", jobs_discovered: 100 }, ...Array.from({ length: 7 }, (_, i) => ({ date: `x${i}`, jobs_discovered: 2, applications_submitted: i === 6 ? 19 : 0 }))];
    expect(sumLastDays(series, "jobs_discovered", 7)).toBe(14);
    expect(sumLastDays(series, "applications_submitted")).toBe(19);
    expect(sumLastDays(undefined, "jobs_discovered")).toBe(0);
  });

  it("finds the next daily job search only while the scheduler runs", () => {
    const st = { enabled: true, paused: false, jobs: [{ name: "application_worker", next_run_local: "soon" }, { name: "daily_discovery", next_run_local: "2026-11-25T07:00:00+05:30" }] };
    expect(nextDiscovery(st)).toBe("2026-11-25T07:00:00+05:30");
    expect(nextDiscovery({ ...st, paused: true })).toBeNull();
    expect(nextDiscovery(null)).toBeNull();
  });

  it("puts what needs the candidate first, then good jobs, then 'nothing waiting'", () => {
    expect(headline(2, { to_apply: 5 }, null)).toMatchObject({ tone: "warning", link: { to: "/application-queue" } });
    expect(headline(0, { to_apply: 1 }, null).text).toBe("1 good job you have not applied to yet.");
    const calm = headline(0, { to_apply: 0, eligible_not_applied: 1 }, "T", (v) => `at ${v}`);
    expect(calm.tone).toBe("success");
    expect(calm.text).toContain("Nothing is waiting for you right now. Next job search: at T.");
    expect(calm.text).toContain("1 eligible job below your score rule is in Job Matching.");
    expect(headline(0, null, null).text).toContain("daily job search is off");
  });

  it("describes the activity window in one sentence with the busiest day", () => {
    const s = activitySummary([{ date: "2026-11-21", jobs_discovered: 299, applications_prepared: 9 }, { date: "2026-11-22", jobs_discovered: 10, applications_submitted: 3 },
      { date: "2026-11-24", jobs_discovered: 17, applications_prepared: 17, applications_submitted: 19 }]);
    expect(s).toMatchObject({ days: 3, found: 326, prepared: 26, applied: 22, busiest: { date: "2026-11-21", jobs: 299 } });
    expect(activitySentence(s)).toBe("Last 3 days: 326 jobs found, 26 applications prepared, 22 applied. Busiest day: 21 Nov (299 jobs found).");
    expect(activitySentence(activitySummary([{ date: "2026-11-24" }]))).toBe("Last 1 day: 0 jobs found, 0 applications prepared, 0 applied.");
    expect(shortDay("2026-01-05")).toBe("5 Jan");
    expect(shortDay("not a date")).toBe("not a date");
  });

  it("splits status boxes into cards with a value and a quiet list of zeros", () => {
    const r = splitByValue([{ label: "Submitted", value: 22 }, { label: "Verified", value: 0 }, { label: "Failed", value: 0 }]);
    expect(r.shown.map((i) => i.label)).toEqual(["Submitted"]);
    expect(r.zero.map((i) => i.label)).toEqual(["Verified", "Failed"]);
  });
});
