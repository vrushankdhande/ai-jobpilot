import { addDays, exportName, fmtPct, isEmptyRange, quickRange, renameColumns, statusChartData } from "./analytics";

describe("analytics helpers", () => {
  it("computes inclusive quick ranges ending today, none for Custom", () => {
    expect(quickRange("Last 7 days", "2026-11-13")).toEqual(["2026-11-07", "2026-11-13"]);
    expect(quickRange("Last 30 days", "2026-11-13")).toEqual(["2026-10-15", "2026-11-13"]);
    expect(quickRange("Custom", "2026-11-13")).toBeNull();
  });
  it("adds days across month and year boundaries", () => {
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
    expect(addDays("2026-02-27", 2)).toBe("2026-03-01");
  });
  it("shows a dash for missing rates, never 0", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtPct(0)).toBe("0%");
    expect(fmtPct(66.6667)).toBe("66.6667%");
  });
  it("detects an empty range from totals", () => {
    expect(isEmptyRange({ a: 0, b: 0 })).toBe(true);
    expect(isEmptyRange({ a: 0, b: 2 })).toBe(false);
  });
  it("sorts status chart data and renames columns", () => {
    expect(statusChartData({ VERIFIED: 2, APPROVED: 1 })).toEqual([{ status: "APPROVED", applications: 1 }, { status: "VERIFIED", applications: 2 }]);
    expect(renameColumns([{ date: "d", jobs_discovered: 1, other: 2 }])).toEqual({ keys: ["date", "jobs_discovered", "other"], headers: ["Date", "Discovered", "other"] });
    expect(renameColumns([])).toEqual({ keys: [], headers: [] });
  });
  it("names export files by kind and range", () => {
    expect(exportName("daily", "2026-11-01", "2026-11-13")).toBe("jobpilot_daily_2026-11-01_2026-11-13.csv");
  });
});
