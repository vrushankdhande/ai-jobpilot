"""Finds the application form on a page and describes every control - WITHOUT relying on CSS selectors.

Identification uses the accessible name (aria-labelledby / aria-label / <label for> / wrapping label / fieldset legend /
nearby text), the field's type, name, placeholder and DOM relationships. A small JavaScript snippet only READS the DOM and tags
each control with a throw-away ``data-jp`` marker so later steps can address exactly the control that was analysed; it never
clicks, types or submits. Everything that interprets the extracted data is plain Python (``snapshot_from_raw``) and is unit-tested
without a browser.

If something cannot be identified, the form is reported as unsupported / the field as unidentified - the caller must then stop and
ask the candidate (nothing is guessed here).
"""
import hashlib
import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------------------ data
KINDS = {"text", "textarea", "select", "radio", "checkbox", "file", "email", "tel", "url", "number", "date", "password"}


@dataclass
class FormField:
    id: str                                   # "f0", "f1" ... valid only for this snapshot
    kind: str
    label: str = ""
    name: str = ""
    dom_id: str = ""
    placeholder: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)       # select options / radio labels / checkbox label
    accept: str = ""
    multiple: bool = False
    visible: bool = True
    disabled: bool = False
    current_value: str = ""
    error: str = ""
    group: str = ""                           # fieldset / question text for radio groups
    option_marks: list[str] = field(default_factory=list)  # radio buttons: the marker of each option (aligned with ``options``)
    identified: bool = True                  # False if there was no usable label/name/placeholder at all

    def describe(self) -> str:
        return self.label or self.placeholder or self.name or self.dom_id or "(unlabelled)"


@dataclass
class SubmitControl:
    id: str
    text: str


@dataclass
class FormSnapshot:
    found: bool = False
    title: str = ""
    fields: list[FormField] = field(default_factory=list)
    submit: list[SubmitControl] = field(default_factory=list)
    next_controls: list[str] = field(default_factory=list)        # "Next"/"Continue": a multi-step form
    errors: list[str] = field(default_factory=list)               # visible validation messages (text only)
    has_captcha: bool = False
    has_login_wall: bool = False
    issues: list[str] = field(default_factory=list)               # reasons the form is not safely automatable
    fingerprint: str = ""

    @property
    def multi_step(self) -> bool:
        return bool(self.next_controls) and not self.submit

    @property
    def automatable(self) -> bool:
        return self.found and not self.issues


# ------------------------------------------------------------------------------------------------------------ DOM reader
# Reads only. Returns {forms: [...]}; each control carries a data-jp="<form>:<n>" marker added to the element.
READ_FORMS_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0) && el.type !== 'hidden'; };
  const txt = el => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
  const byIds = ids => (ids || '').split(/\s+/).filter(Boolean).map(i => txt(document.getElementById(i))).filter(Boolean).join(' ');
  const labelOf = el => {
    let t = byIds(el.getAttribute('aria-labelledby')); if (t) return t;
    t = (el.getAttribute('aria-label') || '').trim(); if (t) return t;
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) { t = txt(l); if (t) return t; } }
    const w = el.closest('label'); if (w) { const c = w.cloneNode(true); c.querySelectorAll('input,select,textarea').forEach(x => x.remove()); t = txt(c); if (t) return t; }
    // nearby text: the closest ancestor (max 3 levels) that holds short text besides the control itself
    let p = el.parentElement;
    for (let i = 0; p && i < 3; i++, p = p.parentElement) {
      const c = p.cloneNode(true); c.querySelectorAll('input,select,textarea,button,option,script,style').forEach(x => x.remove());
      t = txt(c); if (t && t.length <= 160) return t;
    }
    return '';
  };
  const groupOf = el => { const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return txt(lg); }
    const g = el.closest('[role=radiogroup],[role=group]'); if (g) return (g.getAttribute('aria-label') || byIds(g.getAttribute('aria-labelledby'))); return ''; };
  const errOf = el => { let t = byIds(el.getAttribute('aria-errormessage')) || ''; if (!t && el.getAttribute('aria-invalid') === 'true') t = byIds(el.getAttribute('aria-describedby'));
    if (!t && el.getAttribute('aria-invalid') === 'true') t = 'invalid'; return t; };
  const roots = Array.from(document.querySelectorAll('form'));
  if (!roots.length) roots.push(document.body);
  const out = [];
  roots.forEach((root, fi) => {
    const fields = []; const seenRadio = {};
    let n = 0;
    root.querySelectorAll('input,select,textarea').forEach(el => {
      const type = (el.getAttribute('type') || (el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : 'text')).toLowerCase();
      if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
      const required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$|\(required\)/i.test(labelOf(el));
      const rec = { type: type, tag: el.tagName.toLowerCase(), name: el.name || '', id: el.id || '', placeholder: el.getAttribute('placeholder') || '',
        label: labelOf(el), group: groupOf(el), required: required, accept: el.getAttribute('accept') || '', multiple: !!el.multiple,
        visible: vis(el), disabled: !!el.disabled, value: (type === 'file' ? '' : (el.value || '')), error: errOf(el),
        invalid: (el.matches && el.matches(':invalid')) || false, options: [] };
      if (type === 'radio') {
        const key = (el.name || '') + '|' + fi; const lab = labelOf(el);
        if (seenRadio[key] !== undefined) { fields[seenRadio[key]].options.push(lab); fields[seenRadio[key]].optionMarks.push(fi + ':' + n); el.setAttribute('data-jp', fi + ':' + n++); return; }
        seenRadio[key] = fields.length; rec.options = [lab]; rec.optionMarks = [fi + ':' + n]; rec.label = rec.group || rec.label;
      } else if (type === 'checkbox') { rec.options = [labelOf(el)]; }
      else if (el.tagName === 'SELECT') { rec.options = Array.from(el.options).map(o => (o.textContent || '').trim()).filter(Boolean); }
      el.setAttribute('data-jp', fi + ':' + n); rec.mark = fi + ':' + n++;
      fields.push(rec);
    });
    const buttons = [];
    root.querySelectorAll('button,input[type=submit],[role=button]').forEach((b, bi) => {
      const t = b.tagName === 'INPUT' ? (b.value || '') : txt(b); if (!t || !vis(b)) return;
      b.setAttribute('data-jp-btn', fi + ':' + bi); buttons.push({ mark: fi + ':' + bi, text: t, type: (b.getAttribute('type') || (b.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase(), disabled: !!b.disabled });
    });
    const alerts = Array.from(root.querySelectorAll('[role=alert],[aria-live=assertive]')).map(txt).filter(Boolean);
    const classErr = Array.from(root.querySelectorAll('[class*=error],[class*=invalid-feedback]')).filter(vis).map(txt).filter(t => t && t.length < 200);
    out.push({ index: fi, isForm: root.tagName === 'FORM', title: txt(document.querySelector('h1,h2')), fields: fields, buttons: buttons,
      alerts: alerts, classErrors: classErr, formText: txt(root).slice(0, 4000) });
  });
  const html = document.documentElement.innerHTML.slice(0, 400000).toLowerCase();
  return { forms: out, url: location.href, pageText: txt(document.body).slice(0, 8000),
    captcha: !!document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="turnstile"],iframe[src*="challenges.cloudflare"],.g-recaptcha,.h-captcha,[data-sitekey],.grecaptcha-badge')
             || /g-recaptcha|h-captcha|cf-turnstile/.test(html),
    password: !!Array.from(document.querySelectorAll('input[type=password]')).find(vis) };
}
"""

# ------------------------------------------------------------------------------------------------------------ interpretation (pure)
_NEXT = re.compile(r"^\s*(next|continue|save (?:and|&) continue|next step|proceed|continue to)\b", re.I)
_SUBMIT = re.compile(r"^\s*(submit|apply|send|finish|complete|submit application|apply now|send application|confirm)\b", re.I)
_LOGIN_TEXT = re.compile(r"sign in to (?:continue|apply)|log ?in to (?:continue|apply)|please (?:sign|log) ?in|create an account to apply", re.I)
_APPLY_HINT = re.compile(r"appl(y|ication)|resume|cv\b|candidate", re.I)
_TYPE_KIND = {"email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date", "file": "file", "checkbox": "checkbox",
              "radio": "radio", "textarea": "textarea", "select": "select", "password": "password"}


def _clean_label(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return re.sub(r"\s*\*+\s*$", "", re.sub(r"\(required\)|\(optional\)", "", text, flags=re.I)).strip()


def _score_form(form: dict) -> int:
    fields = [f for f in form["fields"] if f["visible"]]
    if not fields:
        return 0
    score = len(fields)
    if any(f["type"] == "file" for f in fields):
        score += 4
    if _APPLY_HINT.search(form.get("formText", "") + " " + form.get("title", "")):
        score += 3
    if any(f["type"] == "password" for f in fields) and len(fields) <= 3:
        score -= 4   # a bare login box is not an application form
    return score


def snapshot_from_raw(raw: dict) -> FormSnapshot:
    """Turn the DOM reader's output into a FormSnapshot. Pure function."""
    snap = FormSnapshot(has_captcha=bool(raw.get("captcha")))
    page_text = raw.get("pageText", "") or ""
    forms = [f for f in raw.get("forms", []) if _score_form(f) > 0]
    if not forms:
        snap.has_login_wall = bool(raw.get("password")) or bool(_LOGIN_TEXT.search(page_text))
        snap.issues.append("No application form was found on the page.")
        if snap.has_login_wall:
            snap.issues.append("A sign-in is required before the application form is shown; log in yourself (manual handoff).")
        if snap.has_captcha:
            snap.issues.append("A CAPTCHA/bot check is present. It is never solved or bypassed; complete this application yourself.")
        return snap
    form = max(forms, key=_score_form)
    snap.found, snap.title = True, form.get("title", "")
    for i, f in enumerate(form["fields"]):
        kind = _TYPE_KIND.get(f["type"], "text")
        label = _clean_label(f.get("label", ""))
        ff = FormField(
            id=f.get("mark") or f"f{i}", kind=kind, label=label, name=f.get("name", ""), dom_id=f.get("id", ""),
            placeholder=f.get("placeholder", ""), required=bool(f.get("required")), options=[_clean_label(o) for o in f.get("options", []) if o],
            accept=f.get("accept", ""), multiple=bool(f.get("multiple")), visible=bool(f.get("visible")), disabled=bool(f.get("disabled")),
            current_value=f.get("value", ""), error=f.get("error") or "", group=_clean_label(f.get("group", "")),
            option_marks=list(f.get("optionMarks", [])))
        ff.identified = bool(label or ff.placeholder or ff.group)   # a bare name="x1" is not an identification
        if f.get("invalid") and f.get("visible") and (ff.current_value or f.get("error")):
            ff.error = ff.error or "invalid"
        snap.fields.append(ff)
    snap.fields = [f for f in snap.fields if f.visible]
    for b in form["buttons"]:
        text = b["text"].strip()
        if _NEXT.match(text):
            snap.next_controls.append(text)
        elif b["type"] in ("submit", "") and (_SUBMIT.match(text) or b["type"] == "submit") and not b.get("disabled"):
            snap.submit.append(SubmitControl(id=b["mark"], text=text))
    msgs = [m for m in (form.get("alerts", []) + form.get("classErrors", [])) if m]
    snap.errors = list(dict.fromkeys(msgs))[:10] + [f"{f.describe()}: {f.error}" for f in snap.fields if f.error][:10]

    snap.has_login_wall = bool(raw.get("password")) or bool(_LOGIN_TEXT.search(page_text))
    if snap.has_captcha:
        snap.issues.append("A CAPTCHA/bot check is present. It is never solved or bypassed; complete this application yourself.")
    if snap.has_login_wall:
        snap.issues.append("The page asks you to sign in; log in yourself (manual handoff) - credentials are never entered by this tool.")
    if snap.multi_step:
        snap.issues.append("This is a multi-step form (Next/Continue); only single-page forms are supported.")
    elif not snap.submit:
        snap.issues.append("No submit control was identified.")
    if len(snap.submit) > 1:
        snap.issues.append("More than one submit control was found; cannot tell which one submits the application.")
    unidentified = [f for f in snap.fields if not f.identified]
    if unidentified:
        snap.issues.append(f"{len(unidentified)} field(s) could not be identified (no label, name or placeholder).")
    snap.fingerprint = fingerprint(snap)
    return snap


def fingerprint(snap: FormSnapshot) -> str:
    """Stable hash of the form's structure: what the candidate approved must be what gets submitted."""
    parts = [f"{f.kind}|{f.label.lower()}|{f.name}|{int(f.required)}|{'/'.join(f.options)}" for f in snap.fields]
    parts.append("submit:" + ",".join(sorted(s.text.lower() for s in snap.submit)))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


# ------------------------------------------------------------------------------------------------------------ browser side
class LocatorEngine:
    """Thin Playwright wrapper: ``read`` (analyse) plus small, explicit element helpers used by the engine."""

    def __init__(self, page):
        self.page = page

    def read(self) -> FormSnapshot:
        return snapshot_from_raw(self.page.evaluate(READ_FORMS_JS))

    def locator(self, mark: str):
        return self.page.locator(f'[data-jp="{mark}"]')

    def submit_locator(self, mark: str):
        return self.page.locator(f'[data-jp-btn="{mark}"]')

    def page_text(self) -> str:
        return self.page.evaluate("() => (document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 20000)")
