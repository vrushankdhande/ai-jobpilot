import { describe, expect, it } from "vitest";
import { num, parseWindow, prefsFromConfig, prefsToValues, split, triToValue, valueToTri, weightsTotal, weightsValid, windowText } from "./settings";

describe("settings lib", () => {
  it("split handles commas and newlines", () => { expect(split("a, b\nc,, ")).toEqual(["a", "b", "c"]); expect(split("")).toEqual([]); });
  it("tri-state round trip: unknown is null, never false", () => {
    expect(triToValue("unknown")).toBeNull(); expect(triToValue("yes")).toBe(true); expect(triToValue("no")).toBe(false);
    expect(valueToTri(null)).toBe("unknown"); expect(valueToTri(undefined)).toBe("unknown"); expect(valueToTri(true)).toBe("yes"); expect(valueToTri(false)).toBe("no");
  });
  it("weights must total exactly 100", () => {
    expect(weightsTotal({ a: 60, b: 40 })).toBe(100);
    expect(weightsValid({ a: 60, b: 40 })).toBe(true);
    expect(weightsValid({ a: 60, b: 41 })).toBe(false);
    expect(weightsValid({ a: 33.5, b: 66.5 })).toBe(true);
  });
  it("windows parse HH:MM-HH:MM; anything else is sent as one item so the server rejects it", () => {
    expect(parseWindow("09:00-21:00")).toEqual(["09:00", "21:00"]);
    expect(parseWindow("nonsense")).toEqual(["nonsense"]);
    expect(parseWindow("1-2-3")).toEqual(["1-2-3"]);
    expect(windowText(["09:00", "21:00"])).toBe("09:00-21:00");
  });
  it("preferences: empty / zero means unset (null), currency upper-cased", () => {
    const form = prefsFromConfig({ titles: [], locations: [], skills: [], companies: [], remote: null, hybrid: null, onsite: null, open_to_relocation: null, experience_years_min: null,
      experience_years_max: null, min_salary: null, salary_currency: null, notice_period: null, posted_within_days: null, employment_types: [], work_authorization: [], keywords_include: [], keywords_exclude: [] });
    const v = prefsToValues({ ...form, titles: "ML Engineer, Data Scientist", sal: 1000000, cur: " inr ", remote: "yes", onsite: "no" });
    expect(v).toMatchObject({ titles: ["ML Engineer", "Data Scientist"], min_salary: 1000000, salary_currency: "INR", remote: true, onsite: false, hybrid: null, open_to_relocation: null,
      experience_years_min: null, notice_period: null, posted_within_days: null });
  });
  it("num falls back for invalid text", () => { expect(num("12")).toBe(12); expect(num("abc", 7)).toBe(7); });
});

describe("remote jobs within the preferred locations", () => {
  it("round-trips the setting between the file and the form", () => {
    const f = prefsFromConfig({ remote_within_locations: true, locations: ["Pune", "India"] });
    expect(f.remoteIn).toBe(true);
    expect(prefsToValues({ ...f, remoteIn: false }).remote_within_locations).toBe(false);
    expect(prefsFromConfig({}).remoteIn).toBe(false);                     // not set in the file = off (remote jobs kept wherever they are)
  });
});
