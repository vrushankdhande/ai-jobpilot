/* JobPilot Autofill - the code behind the "JobPilot Autofill" bookmark. It runs only when YOU click the bookmark on an application form that YOU
   opened, in your own browser. It fills EMPTY fields from the data you copied in JobPilot (paste it into the box it shows), outlines them, and
   shows a short report. It never presses Submit, never touches CAPTCHAs, file uploads, consent boxes or demographic questions, never overwrites
   what you typed, and sends nothing anywhere: no network request is made. Keep this file self-contained (it becomes a javascript: bookmark). */
(function () {
  "use strict";
  var MARK = "JOBPILOT-AUTOFILL:";
  var PANEL_ID = "jobpilot-autofill-panel";

  // label -> field key. Order matters (first match wins). Labels are lower-cased with *, ✱, "(required)" etc. removed.
  var RULES = [
    ["first_name", /^(legal |preferred |given |your )?first name$|^given name$|^first$|^fname$/],
    ["last_name", /^(legal |your )?(last|family) name$|^surname$|^last$|^lname$/],
    ["full_name", /^(your |legal |candidate )?(full )?name$|^full name$|^candidate name$/],
    ["email", /e-?mail/],
    ["phone", /phone|mobile|telephone|contact (number|no)|^cell/],
    ["linkedin_url", /linkedin/],
    ["github_url", /github/],
    ["portfolio_url", /portfolio|personal (web ?site|site|page)|^web ?site( url)?$|other (web ?site|link)/],
    ["current_company", /current (company|employer|organi[sz]ation)|present (company|employer)|^(company|employer|organi[sz]ation)( name)?$/],
    ["current_title", /current (job |work )?(title|role|position|designation)|present (title|role|position)|^(job )?title$|^designation$/],
    ["years_experience", /years? of (total |relevant |professional |work )?experience|total experience|experience in years|how many years/],
    ["notice_period", /notice period|how soon can you (start|join)|earliest (start|joining)|when can you (start|join)|availability to (start|join)/],
    ["salary_expectation", /expected (salary|ctc|compensation|pay|package)|salary expectation|desired (salary|compensation)|expected annual/],
    ["city", /^(current )?city$/],
    ["location", /current location|^location$|where are you (based|located)|city of residence|place of residence/],
    ["skills", /^skills$|key skills|technical skills|core skills|skill set/],
    ["education_degree", /highest (level of )?(education|qualification|degree)|^degree$|^qualification$/],
    ["education_institution", /^(school|university|college|institution)( name)?$|name of (your )?(school|university|college)/],
    ["cover_letter_text", /cover letter|covering letter|motivation letter|letter of motivation/]
  ];
  // never filled for you: legal, demographic, consent and current-pay questions are yours to answer
  var NEVER = /gender|\bsex\b|race|ethnic|veteran|disabilit|pronoun|sexual orientation|religio|marital|date of birth|\bdob\b|caste|nationality|sponsor|visa|authori[sz]ed to work|work authori[sz]ation|right to work|consent|agree|privacy|terms|certif|declar|current (salary|ctc|compensation|pay)|captcha|password|otp/;
  var STOP = { the: 1, a: 1, an: 1, of: 1, to: 1, "do": 1, you: 1, your: 1, are: 1, is: 1, "in": 1, "for": 1, and: 1, or: 1, "with": 1, "this": 1, our: 1,
    us: 1, we: 1, please: 1, what: 1, why: 1, how: 1, tell: 1, describe: 1, have: 1, has: 1, can: 1, will: 1, would: 1, be: 1, on: 1, at: 1, it: 1, that: 1 };

  function clean(t) {
    return String(t || "").replace(/[*✱]/g, " ").replace(/\((required|optional)\)/ig, " ").replace(/\s+/g, " ").trim();
  }
  function norm(t) { return clean(t).toLowerCase().replace(/[?:.!]+$/, "").trim(); }
  function words(t) {
    var out = {};
    norm(t).replace(/[^a-z0-9 ]+/g, " ").split(" ").forEach(function (w) { if (w.length > 2 && !STOP[w]) out[w] = 1; });
    return out;
  }
  function overlap(a, b) {
    var A = words(a), B = words(b), inter = 0, union = 0, k;
    for (k in A) { union++; if (B[k]) inter++; }
    for (k in B) { if (!A[k]) union++; }
    return union ? inter / union : 0;
  }
  function textOf(node) {                                 // visible words of a label, without the text of controls inside it (dropdown options)
    if (!node) return "";
    var out = [];
    (function walk(n) {
      for (var c = n.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) out.push(c.nodeValue);
        else if (c.nodeType === 1 && !/^(SELECT|OPTION|TEXTAREA|SCRIPT|STYLE|BUTTON)$/.test(c.tagName)) walk(c);
      }
    })(node);
    return clean(out.join(" "));
  }

  function labelOf(el, doc) {
    var t = "";
    if (el.id) {
      var lab = doc.querySelector('label[for="' + String(el.id).replace(/["\\]/g, "\\$&") + '"]');
      if (lab) t = textOf(lab);
    }
    if (!t) { var wrap = el.closest("label"); if (wrap) t = textOf(wrap); }
    if (!t && el.getAttribute("aria-labelledby")) {
      t = el.getAttribute("aria-labelledby").split(/\s+/).map(function (id) { return textOf(doc.getElementById(id)); }).join(" ");
    }
    if (!t) t = clean(el.getAttribute("aria-label") || el.getAttribute("placeholder") || "");
    if (!t) {                                              // the question text just above the field
      var node = el, i;
      for (i = 0; i < 3 && node && !t; i++) {
        var prev = node.previousElementSibling;
        while (prev && !t) { if (!prev.querySelector || !prev.querySelector("input,textarea,select")) t = textOf(prev); prev = prev.previousElementSibling; }
        node = node.parentElement;
      }
    }
    if (!t) t = clean((el.getAttribute("name") || "").replace(/[_\[\]-]+/g, " "));
    return t.length > 300 ? t.slice(0, 300) : t;
  }

  function hidden(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.hidden || n.getAttribute("aria-hidden") === "true") return true;
      var s = n.ownerDocument.defaultView.getComputedStyle(n);
      if (s && (s.display === "none" || s.visibility === "hidden")) return true;
    }
    return false;
  }

  function fillable(el) {
    var tag = el.tagName, type = (el.getAttribute("type") || "text").toLowerCase();
    if (el.disabled || el.readOnly || hidden(el)) return false;
    if (/captcha/i.test((el.id || "") + " " + (el.getAttribute("name") || "") + " " + (el.className || ""))) return false;
    if (tag === "INPUT" && ["text", "email", "tel", "url", "number", "search", ""].indexOf(type) < 0) return false;   // file, checkbox, radio, password, hidden...
    if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list") return false;      // custom dropdowns: you choose
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  function isRequired(el, label) {
    return el.required || el.getAttribute("aria-required") === "true" || /[*✱]\s*$/.test(String(label || "")) || /\(required\)/i.test(label || "");
  }

  function setValue(el, value) {
    var win = el.ownerDocument.defaultView;
    var proto = el.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? win.HTMLSelectElement.prototype : win.HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value); else el.value = value;           // the native setter, so React/Vue forms see the change
    ["input", "change", "blur"].forEach(function (name) { el.dispatchEvent(new win.Event(name, { bubbles: true })); });
  }

  function pickOption(sel, value) {
    var v = norm(value), opts = sel.options, i;
    for (i = 0; i < opts.length; i++) if (norm(opts[i].text) === v || norm(opts[i].value) === v) return opts[i].value;
    if (/^(yes|no)$/.test(v)) for (i = 0; i < opts.length; i++) if (norm(opts[i].text).indexOf(v) === 0) return opts[i].value;
    return null;                                                                       // never a guess
  }

  function keyFor(label, el) {
    var l = norm(label), type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "email") return "email";
    if (type === "tel") return "phone";
    for (var i = 0; i < RULES.length; i++) if (RULES[i][1].test(l)) return RULES[i][0];
    return null;
  }

  function answerFor(label, answers) {
    var best = null, score = 0;
    (answers || []).forEach(function (a) { var s = overlap(label, a.question); if (s > score) { score = s; best = a; } });
    return score >= 0.6 ? best.answer : null;
  }

  /** Fills the document; returns a report. `data` = {job:{title,company}, fields:{key:value}, derived:[keys], answers:[{question,answer}]}. */
  function fill(data, doc) {
    var report = { filled: [], check: [], todo: [], files: [], frames: [] };
    var els = doc.querySelectorAll("input, textarea, select"), i;
    for (i = 0; i < els.length; i++) {
      var el = els[i], type = (el.getAttribute("type") || "").toLowerCase();
      if (type === "file" && !hidden(el)) { report.files.push(labelOf(el, doc) || "File upload"); continue; }
      if ((type === "checkbox" || type === "radio") && !hidden(el)) {             // tick boxes are never ticked for you: list the required ones
        var tick = labelOf(el, doc);
        if (isRequired(el, tick) && !el.checked && report.todo.indexOf(clean(tick)) < 0) report.todo.push(clean(tick) || "A required tick box");
        continue;
      }
      if (!fillable(el)) continue;
      var label = labelOf(el, doc), l = norm(label);
      if (NEVER.test(l)) {
        if (isRequired(el, label) && !el.value) { el.style.outline = "2px dashed #e34948"; report.todo.push(clean(label)); }
        continue;
      }
      if (el.tagName !== "SELECT" && el.value && String(el.value).trim()) continue;   // never overwrite what is there
      var key = keyFor(label, el), value = null;
      if (key) value = data.fields[key];
      if (key === "cover_letter_text" && el.tagName !== "TEXTAREA") value = null;    // a cover letter goes into a text box, not a one-line field
      if (!value && !key && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) value = answerFor(label, data.answers);
      if (el.tagName === "SELECT") {
        if (el.value && el.selectedIndex > 0) continue;
        value = value ? pickOption(el, value) : null;
      }
      if (value) {
        setValue(el, value);
        var derived = key && (data.derived || []).indexOf(key) >= 0;
        el.style.outline = derived ? "2px solid #eda100" : "2px solid #1baf7a";
        el.style.outlineOffset = "1px";
        el.setAttribute("data-jobpilot", derived ? "check" : "filled");
        report.filled.push(clean(label) || key);
        if (derived) report.check.push(clean(label) || key);
      } else if (isRequired(el, label) && !(el.value && String(el.value).trim())) {
        el.style.outline = "2px dashed #e34948";
        report.todo.push(clean(label) || "A required field");
      }
    }
    var frames = doc.querySelectorAll("iframe[src]");
    for (i = 0; i < frames.length; i++) {
      var src = frames[i].getAttribute("src") || "";
      if (/^https:\/\/[^/]*(greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com)\//i.test(src)) report.frames.push(src);
    }
    return report;
  }

  function parse(text) {
    var t = String(text || "").trim();
    if (t.indexOf(MARK) !== 0) return null;
    try { var d = JSON.parse(t.slice(MARK.length)); } catch (e) { return null; }
    if (!d || typeof d !== "object" || typeof d.fields !== "object" || !d.fields) return null;
    var fields = {}, k;
    for (k in d.fields) if (typeof d.fields[k] === "string" && d.fields[k].length < 20000) fields[k] = d.fields[k];
    var answers = (Array.isArray(d.answers) ? d.answers : []).filter(function (a) { return a && typeof a.question === "string" && typeof a.answer === "string"; });
    return { job: d.job && typeof d.job === "object" ? d.job : {}, fields: fields, derived: Array.isArray(d.derived) ? d.derived : [], answers: answers };
  }

  function mk(doc, tag, text, style) {
    var e = doc.createElement(tag);
    if (text) e.textContent = text;
    if (style) e.setAttribute("style", style);
    return e;
  }

  function showPanel(doc, data, report) {
    var old = doc.getElementById(PANEL_ID);
    if (old) old.remove();
    var p = mk(doc, "div", "", "position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:380px;max-height:70vh;overflow:auto;background:#fff;color:#1f2430;" +
      "border:1px solid #c9ced6;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);padding:12px 14px;font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif");
    p.id = PANEL_ID;
    var close = mk(doc, "button", "✕", "float:right;border:0;background:none;font-size:16px;cursor:pointer;color:#52514e");
    close.setAttribute("aria-label", "Close JobPilot Autofill");
    close.addEventListener("click", function () { p.remove(); });
    p.appendChild(close);
    p.appendChild(mk(doc, "div", "⚡ JobPilot Autofill", "font-weight:700;margin-bottom:4px"));
    var job = [data.job.title, data.job.company].filter(Boolean).join(" at ");
    if (job) p.appendChild(mk(doc, "div", "For: " + job, "color:#52514e;margin-bottom:6px"));
    var page = (doc.title + " " + (doc.body ? doc.body.textContent.slice(0, 20000) : "")).toLowerCase();
    if (data.job.company && page.indexOf(String(data.job.company).toLowerCase()) < 0) {
      p.appendChild(mk(doc, "div", "⚠️ " + data.job.company + " is not mentioned on this page - is this the right job?", "color:#92610a;margin-bottom:6px"));
    }
    p.appendChild(mk(doc, "div", report.filled.length ? "✅ Filled " + report.filled.length + " field(s) (green outline)." : "No empty field on this page matched your details.", "margin-bottom:4px"));
    function list(title, items, color) {
      if (!items.length) return;
      p.appendChild(mk(doc, "div", title, "font-weight:600;margin-top:6px;color:" + color));
      var ul = mk(doc, "ul", "", "margin:2px 0 0 18px;padding:0");
      items.slice(0, 15).forEach(function (x) { ul.appendChild(mk(doc, "li", x)); });
      p.appendChild(ul);
    }
    list("Check these (amber outline):", report.check, "#92610a");
    list("Answer these yourself (red dashed outline):", report.todo, "#b42318");
    list("Attach yourself:", report.files, "#1f2430");
    report.frames.slice(0, 1).forEach(function (src) {
      p.appendChild(mk(doc, "div", "This form is inside a frame, so it cannot be filled from here. Open it on its own page, then click the bookmark again:", "margin-top:6px"));
      var a = mk(doc, "a", "Open the form ↗", "color:#2a78d6");
      a.href = src; a.target = "_blank"; a.rel = "noopener noreferrer";
      p.appendChild(a);
    });
    p.appendChild(mk(doc, "div", "Check every field, attach your resume and complete any CAPTCHA. JobPilot never presses Submit - you do. " +
      "Afterwards flip the Applied switch in JobPilot.", "margin-top:8px;color:#52514e"));
    (doc.body || doc.documentElement).appendChild(p);
  }

  var hook = typeof window !== "undefined" && window.__JOBPILOT_AUTOFILL_TEST__;
  if (hook) { hook.fill = fill; hook.parse = parse; hook.labelOf = labelOf; hook.showPanel = showPanel; return; }

  var text = window.prompt("JobPilot Autofill\n\nIn JobPilot press \"Copy autofill data\" for this job, then paste it here (Ctrl+V) and press OK.");
  if (text === null) return;
  var data = parse(text);
  if (!data) { window.alert("That is not JobPilot autofill data. In JobPilot open the job in the Application Queue and press \"Copy autofill data\" first."); return; }
  showPanel(document, data, fill(data, document));
})();
