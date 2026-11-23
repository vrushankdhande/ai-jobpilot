"""Plain-text e-mail bodies. They contain job titles/companies, counts and states only - never resume content, contact details or tokens."""


def _lines(pairs) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in pairs)


def discovery_summary(run: dict) -> tuple[str, str]:
    t = run.get("totals") or {}
    body = ("Job discovery finished.\n\n" + _lines([("New jobs", t.get("new", 0)), ("Duplicates removed", t.get("duplicates", 0)), ("Fetched", t.get("fetched", 0)),
                                                   ("Queued for preparation", run.get("queued", 0)), ("Matched", run.get("matched", 0))]) +
            "\n\nNothing is applied to automatically: prepared applications wait for your approval.")
    return f"Job discovery: {t.get('new', 0)} new jobs", body


def daily_report(rep: dict) -> tuple[str, str]:
    a, d, m, lim = rep["applications"], rep["discovery"], rep["matching"], rep["limits"]
    body = (f"AI JobPilot daily report - {rep['date']} ({rep['timezone']})\n\nDISCOVERY\n" +
            _lines([("Jobs discovered", d["discovered"]), ("Duplicates removed", d["duplicates_removed"]), ("By source", ", ".join(f"{k}: {v}" for k, v in d["by_source"].items()) or "-")]) +
            "\n\nMATCHING\n" + _lines([("Jobs matched", m["matched"]), ("Excellent / Strong / Good", f"{m['excellent']} / {m['strong']} / {m['good']}"), ("Eligible", m["eligible"])]) +
            "\n\nAPPLICATIONS (only confirmed submissions are counted as submitted)\n" +
            _lines([("Awaiting your approval", a["awaiting_approval"]), ("Submitted (confirmation seen)", a["submitted"]), ("Verified", a["verified"]),
                    ("Failed", a["failed"]), ("Manual handoffs", a["manual_handoff"]), ("Pending verification", a["pending_verification"])]) +
            "\n\nLIMITS TODAY\n" + _lines([(k, f"{v['used']} / {v['limit']}") for k, v in lim.items()]) + "\n")
    return f"Daily report {rep['date']}: {a['submitted']} submitted, {a['awaiting_approval']} awaiting approval", body
