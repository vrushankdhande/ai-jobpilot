import { Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { activitySentence, activitySummary, shortDay } from "../lib/dashboard";

type Row = Record<string, any>;
const AXIS_TICK = { fill: "var(--muted)", fontSize: 12 };
const showIfPositive = (v: unknown) => (Number(v) > 0 ? String(v) : "");

/** Daily activity as two small charts, each on its own scale: jobs found run to hundreds a day and applications to a handful, so one shared
 *  axis would flatten the applications. Every number comes from the API's activity series. Colours: validated categorical slots 1-3 (.viz-root). */
export function ActivityCharts({ series }: { series: Row[] }) {
  const s = activitySummary(series);
  const rows: Row[] = series.map((r) => ({ ...r, day: shortDay(String(r.date)) }));
  const busiest = s.busiest?.jobs ?? 0;
  return (
    <div className="viz-root">
      <p data-testid="activity-summary"><b>{activitySentence(s)}</b></p>
      <div className="grid cols-2">
        <figure className="chart-card" aria-label="Jobs found per day">
          <figcaption><b>Jobs found per day</b><span className="muted"> · new jobs stored (daily search + your imports)</span></figcaption>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <BarChart data={rows} margin={{ top: 22, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "var(--line)" }} interval="preserveStartEnd" minTickGap={12} />
                <YAxis allowDecimals={false} width={38} tick={AXIS_TICK} tickLine={false} axisLine={false} />
                <Tooltip content={<DayTip />} cursor={{ fill: "var(--line)", opacity: 0.5 }} />
                <Bar dataKey="jobs_discovered" name="Jobs found" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28}>
                  {/* only the busiest day is labelled: a number on every bar is noise (hover shows each day) */}
                  <LabelList dataKey="jobs_discovered" position="top" fill="var(--text)" fontSize={12} formatter={(v: unknown) => (Number(v) > 0 && Number(v) === busiest ? String(v) : "")} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </figure>
        <figure className="chart-card" aria-label="Applications per day">
          <figcaption><b>Applications per day</b><span className="muted" title="Prepared = tailored documents made by JobPilot. Applied = submitted or recorded by you."> · prepared by JobPilot vs applied</span></figcaption>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <BarChart data={rows} margin={{ top: 22, right: 8, left: 0, bottom: 0 }} barGap={2}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "var(--line)" }} interval="preserveStartEnd" minTickGap={12} />
                <YAxis allowDecimals={false} width={38} tick={AXIS_TICK} tickLine={false} axisLine={false} />
                <Tooltip content={<DayTip />} cursor={{ fill: "var(--line)", opacity: 0.5 }} />
                <Legend verticalAlign="top" align="right" iconType="circle" iconSize={9} wrapperStyle={{ fontSize: 12, top: -4 }} formatter={(v) => <span style={{ color: "var(--text)" }}>{v}</span>} />
                <Bar dataKey="applications_prepared" name="Prepared" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={18}>
                  <LabelList dataKey="applications_prepared" position="top" fill="var(--text)" fontSize={11} formatter={showIfPositive} />
                </Bar>
                <Bar dataKey="applications_submitted" name="Applied" fill="var(--series-3)" radius={[4, 4, 0, 0]} maxBarSize={18}>
                  <LabelList dataKey="applications_submitted" position="top" fill="var(--text)" fontSize={11} formatter={showIfPositive} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </figure>
      </div>
      <details>
        <summary>Show as a table</summary>
        <div className="table-wrap"><table>
          <thead><tr><th>Day</th><th>Jobs found</th><th>Applications prepared</th><th>Applied</th></tr></thead>
          <tbody>{[...rows].reverse().map((r) => (
            <tr key={String(r.date)}><td>{r.day}</td><td>{r.jobs_discovered || 0}</td><td>{r.applications_prepared || 0}</td><td>{r.applications_submitted || 0}</td></tr>
          ))}</tbody>
        </table></div>
      </details>
    </div>
  );
}

/** Hover card: the day and each value in plain words; text stays in text colours, a coloured dot carries the series identity. */
function DayTip({ active, payload }: { active?: boolean; payload?: Array<{ name?: string; value?: number; color?: string; payload: Row }> }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="chart-tip">
      <div><b>{r.day}</b></div>
      {payload.map((p) => <div key={p.name}><span className="dot" style={{ background: p.color }} /> {p.name}: <b>{p.value || 0}</b></div>)}
    </div>
  );
}
