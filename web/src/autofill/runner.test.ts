// The JobPilot Autofill bookmark code, run on made-up forms shaped like real ATS forms (Greenhouse-style labels, Lever-style wrapping labels).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AUTOFILL_MARK, RUNNER_SOURCE, autofillPayload, bookmarkletHref } from "../lib/autofill";

type Hook = { fill: (d: any, doc: Document) => any; parse: (t: string) => any; showPanel: (doc: Document, d: any, r: any) => void };
let hook: Hook;

beforeEach(() => {
  (window as any).__JOBPILOT_AUTOFILL_TEST__ = {};
  // the bookmark code is plain text; running it here registers its functions on the test hook instead of prompting
  new Function(RUNNER_SOURCE)();                     // eslint-disable-line no-new-func
  hook = (window as any).__JOBPILOT_AUTOFILL_TEST__;
});
afterEach(() => { document.body.replaceChildren(); delete (window as any).__JOBPILOT_AUTOFILL_TEST__; });

const DATA = {
  job: { title: "AI Engineer", company: "Acme" },
  fields: { first_name: "Jane", last_name: "Sample", full_name: "Jane Sample", email: "jane.sample@example.com", phone: "+91-9000000000",
    linkedin_url: "https://www.linkedin.com/in/janesample", github_url: "https://github.com/janesample", portfolio_url: "https://janesample.github.io",
    current_company: "Acme Analytics", current_title: "AI/ML Engineer", years_experience: "4.2", notice_period: "30 days", location: "Pune, Maharashtra, India",
    cover_letter_text: "Dear hiring team,\nI build retrieval systems.", relocation: "yes" },
  derived: ["first_name", "last_name", "years_experience"],
  answers: [{ question: "Why do you want to join Acme?", answer: "Because Acme builds search for engineers." }],
};

function form(html: string) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;                              // test fixture only (the bookmark itself never uses innerHTML)
  document.body.appendChild(tpl.content);
}
const val = (sel: string) => (document.querySelector(sel) as HTMLInputElement).value;

describe("JobPilot Autofill bookmark", () => {
  it("fills a Greenhouse-style form, marks derived values, and leaves what it must not touch", () => {
    form(`<h1>AI Engineer at Acme</h1><form>
      <label for="first_name">First Name *</label><input id="first_name" required>
      <label for="last_name">Last Name *</label><input id="last_name" required>
      <label for="email">Email *</label><input id="email" type="email" required>
      <label for="phone">Phone</label><input id="phone" type="tel">
      <label for="resume">Resume/CV *</label><input id="resume" type="file" required>
      <label for="q1">LinkedIn Profile</label><input id="q1">
      <label for="q2">Website</label><input id="q2">
      <label for="q3">How many years of experience do you have with Python? *</label><input id="q3" required>
      <label for="q4">Why do you want to join Acme? *</label><textarea id="q4" required></textarea>
      <label for="q5">Are you legally authorized to work in India? *</label><select id="q5" required><option value="">Select</option><option>Yes</option><option>No</option></select>
      <label for="q6">Gender</label><select id="q6"><option value="">Select</option><option>Female</option></select>
      <label for="q7">I agree to the privacy policy *</label><input id="q7" type="checkbox" required>
      <label for="q8">Current CTC *</label><input id="q8" required>
      <label for="q9">Cover Letter</label><textarea id="q9"></textarea>
      <textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea>
      <label for="q10">Notice period</label><input id="q10" value="Immediately">
      <input type="hidden" name="token" value="x"><button type="submit">Submit application</button>
    </form>`);
    let submitted = false;
    document.querySelector("form")!.addEventListener("submit", () => { submitted = true; });
    const r = hook.fill(DATA, document);
    expect([val("#first_name"), val("#last_name"), val("#email"), val("#phone"), val("#q1"), val("#q2")])
      .toEqual(["Jane", "Sample", "jane.sample@example.com", "+91-9000000000", "https://www.linkedin.com/in/janesample", "https://janesample.github.io"]);
    expect(val("#q3")).toBe("4.2");                                                      // years of experience
    expect(val("#q4")).toBe("Because Acme builds search for engineers.");               // approved answer matched by its question
    expect(val("#q9")).toBe("Dear hiring team,\nI build retrieval systems.");
    expect(val("#q10")).toBe("Immediately");                                             // never overwrites what is there
    expect(val("#q5")).toBe("") ; expect(val("#q6")).toBe("");                           // work authorisation / demographics: yours to answer
    expect((document.querySelector("#q7") as HTMLInputElement).checked).toBe(false);    // consent is never ticked
    expect(val("#q8")).toBe("");                                                         // current pay is never filled
    expect(val("#g-recaptcha-response")).toBe("");                                       // CAPTCHA untouched
    expect(submitted).toBe(false);                                                       // never submits
    expect((document.querySelector("#first_name") as HTMLElement).dataset.jobpilot).toBe("check");   // derived (split name) -> amber
    expect((document.querySelector("#email") as HTMLElement).dataset.jobpilot).toBe("filled");
    expect(r.files).toEqual(["Resume/CV"]);
    expect(r.todo).toEqual(expect.arrayContaining(["Are you legally authorized to work in India?", "I agree to the privacy policy", "Current CTC"]));
    expect(r.check).toEqual(expect.arrayContaining(["First Name", "Last Name", "How many years of experience do you have with Python?"]));
  });

  it("fills a Lever-style form whose labels wrap the fields, and tells React-style forms about the change", () => {
    form(`<form><ul>
      <li><label><div>Full name<span>✱</span></div><div><input name="name" required></div></label></li>
      <li><label><div>Email<span>✱</span></div><div><input name="email" type="email" required></div></label></li>
      <li><label><div>Current company</div><div><input name="org"></div></label></li>
      <li><label><div>LinkedIn URL</div><div><input name="urls[LinkedIn]"></div></label></li>
      <li><label><div>GitHub URL</div><div><input name="urls[GitHub]"></div></label></li>
      <li><label><div>Willing to relocate?</div><div><select name="reloc"><option value="">--</option><option value="y">Yes</option><option value="n">No</option></select></div></label></li>
      <li><label><div>Additional information</div><div>Add a cover letter or anything else you want to share.</div><textarea name="comments"></textarea></label></li>
    </ul></form>`);
    const seen: string[] = [];
    document.querySelector('[name="name"]')!.addEventListener("input", () => seen.push("input"));
    hook.fill(DATA, document);
    expect(val('[name="name"]')).toBe("Jane Sample");
    expect(val('[name="org"]')).toBe("Acme Analytics");
    expect(val('[name="urls[GitHub]"]')).toBe("https://github.com/janesample");
    expect(val('[name="comments"]')).toContain("I build retrieval systems");
    expect(seen).toEqual(["input"]);
    expect(val('[name="reloc"]')).toBe("");                                              // no relocation rule: not guessed
  });

  it("only accepts its own data, and its report panel is plain text with no submit button", () => {
    expect(hook.parse("hello")).toBeNull();
    expect(hook.parse(AUTOFILL_MARK + "{bad json")).toBeNull();
    expect(hook.parse(AUTOFILL_MARK + JSON.stringify({ fields: { email: "a@b.c", x: 5 }, answers: [{ question: "q", answer: 3 }] })))
      .toEqual({ job: {}, fields: { email: "a@b.c" }, derived: [], answers: [] });
    const text = autofillPayload({ job: { title: "AI Engineer", company: "<img src=x onerror=alert(1)>" }, fields: [{ key: "email", label: "E-mail", value: "a@b.c" }],
      answers: [], missing: [], problems: [] });
    expect(text.includes("\n")).toBe(false);                                             // survives the one-line paste box
    const data = hook.parse(text);
    hook.showPanel(document, data, { filled: ["E-mail"], check: [], todo: [], files: ["Resume"], frames: ["https://job-boards.greenhouse.io/acme/jobs/1"] });
    const panel = document.getElementById("jobpilot-autofill-panel")!;
    expect(panel.querySelector("img")).toBeNull();                                       // the company name is shown as text, never as HTML
    expect(panel.textContent).toContain("is not mentioned on this page");
    expect(panel.querySelector("a")!.getAttribute("href")).toBe("https://job-boards.greenhouse.io/acme/jobs/1");
    expect(panel.querySelector('button[type="submit"]')).toBeNull();
  });

  it("the bookmark makes no network requests and is a javascript: link", () => {
    expect(RUNNER_SOURCE).not.toMatch(/fetch\(|XMLHttpRequest|sendBeacon|WebSocket|\.innerHTML|eval\(|\.submit\(|\.click\(\)/);
    expect(bookmarkletHref().startsWith("javascript:")).toBe(true);
    expect(decodeURIComponent(bookmarkletHref().slice("javascript:".length))).toBe(RUNNER_SOURCE);
  });
});
