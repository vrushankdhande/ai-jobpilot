import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, DataTable, Empty, ErrorBox, Field, Loading, MetricRow, Tabs } from "../components/ui";
import { downloadBlob } from "../format";
import { useFetch } from "../hooks";
import { addDays, EXPORTS, exportName, fmtPct, isEmptyRange, QUICK_RANGES, quickRange, renameColumns, scoreChartData, statusChartData, toYmd } from "../lib/analytics";

/** Analytics: every figure comes from /api/analytics/overview for the selected range (real stored records); exports use the same numbers. */
export default function Analytics() {
  const today = toYmd(new Date());
  const [from, setFrom] = useState(addDays(today, -29));
  const [to, setTo] = useState(today);
  const [preset, setPreset] = useState("Custom");
  const range = quickRange(preset, today);
  const dFrom = range ? range[0] : from, dTo = range ? range[1] : to;
  const res = useFetch<any>("/api/analytics/overview", { date_from: dFrom, date_to: dTo });
  const o = res.data;

  return (
    <>
      <PageHeader icon="📈" title="Analytics" />
      <div className="grid" style={{ gridTemplateColumns: "2fr 2fr 1fr" }}>
        <Field label="From"><input type="date" value={dFrom} disabled={Boolean(range)} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="To"><input type="date" value={dTo} disabled={Boolean(range)} onChange={(e) => setTo(e.target.value)} /></Field>
        <Field label="Quick range"><select value={preset} onChange={(e) => setPreset(e.target.value)}>{Object.keys(QUICK_RANGES).map((p) => <option key={p}>{p}</option>)}</select></Field>
      </div>
      {res.loading && <Loading text="Calculating…" />}
      <ErrorBox result={res.result} />
      {o && !res.loading && <Body o={o} from={dFrom} to={dTo} />}
    </>
  );
}

function Body({ o, from, to }: { o: any; from: string; to: string }) {
  const t = o.totals, oc = o.application_outcomes, ic = o.interview_conversion;
  return (
    <>
      <p className="muted">{o.from} → {o.to} ({o.days} days, {o.timezone}) · days are local days · all figures are computed from stored records</p>
      <MetricRow items={[["Jobs discovered", t.jobs_discovered], ["Jobs matched", t.jobs_matched, "Latest match per job created in the range"], ["Applications prepared", t.applications_prepared],
        ["Submitted", oc.submitted, "Confirmed by the site or recorded by you"], ["Interviews", ic.interviews, "Scheduled in the range"]]} />
      {isEmptyRange(t) && <Empty message="No records in this date range." hint="Pick a wider range, or run discovery / prepare applications first." />}

      <h2>Jobs over time</h2>
      <div style={{ height: 260 }}><ResponsiveContainer><BarChart data={o.daily}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis allowDecimals={false} /><Tooltip /><Legend />
        <Bar dataKey="jobs_discovered" name="Discovered" fill="#2563eb" /><Bar dataKey="jobs_matched" name="Matched" fill="#16794c" /></BarChart></ResponsiveContainer></div>

      <div className="grid cols-2">
        <div>
          <h2>Match score distribution</h2>
          {o.scored_jobs ? (
            <>
              <div style={{ height: 240 }}><ResponsiveContainer><BarChart data={scoreChartData(o.score_distribution)}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="range" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="jobs" fill="#c0304a" /></BarChart></ResponsiveContainer></div>
              <p className="muted">{o.scored_jobs} jobs with a match in the range (latest score per job).</p>
            </>
          ) : <Empty message="No matches in this range." />}
        </div>
        <div>
          <h2>Applications by status</h2>
          {Object.keys(o.applications_by_status ?? {}).length ? (
            <div style={{ height: 240 }}><ResponsiveContainer><BarChart data={statusChartData(o.applications_by_status)}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="status" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="applications" fill="#d97706" /></BarChart></ResponsiveContainer></div>
          ) : <Empty message="No applications created in this range." />}
        </div>
      </div>

      <h2>Application success and failure</h2>
      <MetricRow items={[["Attempts", oc.attempts, "Submitted + pending verification + failed after sending. Prepared applications are not attempts."], ["Success rate", fmtPct(oc.success_rate), "Submitted ÷ attempts"],
        ["Failure rate", fmtPct(oc.failure_rate), "Rejected / failed after sending ÷ attempts"], ["Unknown outcome", fmtPct(oc.unknown_rate), "Pending verification ÷ attempts"]]} />
      <p className="muted">Confirmed by the site: {oc.confirmed} · recorded by you: {oc.self_reported} · pending verification: {oc.pending_verification} · failed after sending: {oc.failed_after_send}. {oc.note}</p>

      <h2>Job source performance</h2>
      {o.sources?.length ? <DataTable rows={o.sources} columns={[
        { header: "Source", render: (s: any) => s.source }, { header: "Jobs discovered", render: (s: any) => s.jobs }, { header: "Matched", render: (s: any) => s.scored },
        { header: "Avg score", render: (s: any) => s.avg_match_score ?? "" }, { header: "Applications", render: (s: any) => s.applications }, { header: "Submitted", render: (s: any) => s.submitted },
      ]} /> : <Empty message="No source data in this range." />}

      <h2>Interview conversion</h2>
      <MetricRow items={[["Interviews", ic.interviews], ["Completed", ic.completed], ["Advanced", ic.advanced], ["Offers", ic.offers],
        ["Interviews per submission", ic.interviews_per_submission, "Interviews ÷ submitted applications in the range"], ["Offer rate (completed)", fmtPct(ic.offer_rate_of_completed)]]} />

      <h2>Activity reports</h2>
      <ActivityTabs daily={o.daily ?? []} weekly={o.weekly ?? []} />

      <h2>Export</h2>
      <Exports from={from} to={to} />
    </>
  );
}

function ActivityTabs({ daily, weekly }: { daily: any[]; weekly: any[] }) {
  const [tab, setTab] = useState("Daily");
  const rows = tab === "Daily" ? daily : weekly;
  const { keys, headers } = renameColumns(rows);
  return (
    <>
      <Tabs tabs={["Daily", "Weekly"]} value={tab} onChange={setTab} />
      {rows.length ? <DataTable rows={rows} columns={keys.map((k, i) => ({ header: headers[i], render: (r: any) => (r[k] === null || r[k] === undefined ? "" : String(r[k])) }))} /> : <Empty message="No rows." />}
    </>
  );
}

function Exports({ from, to }: { from: string; to: string }) {
  const [state, setState] = useState<Record<string, { ok: boolean; csv?: string; error?: string }>>({});
  useEffect(() => {
    let live = true;
    setState({});
    EXPORTS.forEach(([kind]) => {
      api.get<string>("/api/analytics/export", { params: { kind, date_from: from, date_to: to } }).then((r) => {
        if (live) setState((s) => ({ ...s, [kind]: r.ok ? { ok: true, csv: typeof r.data === "string" ? r.data : "" } : { ok: false, error: r.error } }));
      });
    });
    return () => { live = false; };
  }, [from, to]);
  return (
    <div className="row">
      {EXPORTS.map(([kind, label]) => {
        const s = state[kind];
        if (s && !s.ok) return <Alert key={kind} kind="error">{s.error}</Alert>;
        return <button key={kind} disabled={!s?.csv} onClick={() => downloadBlob(exportName(kind, from, to), s!.csv!)}>⬇️ {label} CSV</button>;
      })}
    </div>
  );
}
