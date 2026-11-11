import type { SupportAttribution } from "./RecordAttribution";
import { poll, snapshot } from "./polling";
import { PerformanceSummary, type PerformancePeriods } from "./PerformanceSummary";
import { useCallback, useEffect, useState } from "react";
import { MapPin, Phone, RefreshCw, Target, Users } from "lucide-react";
import { useTheme } from "./ThemeContext";

type TargetProgress = { metric: string; period: string; amount: number; actual: number; goal: number; percent: number; remaining: number; status: string; start: string; end: string };
export type FieldVisit = SupportAttribution & { id: string; location: string; notes: string; started_at: string; ended_at: string; duration_minutes: number; interactions: number; interaction_target?: number; remaining?: number };
type Progress = { performance?: PerformancePeriods; targets: TargetProgress[]; visits: FieldVisit[] };
const labels: Record<string, string> = { calls: "Calls", field_trips: "Field trips", interactions: "Customer interactions" };
const periods: Record<string, string> = { daily: "Daily", weekly: "Weekly", per_trip: "Per trip" };
export const ghanaDay = () => new Date().toISOString().slice(0, 10);
export const visitTime = (value: string) => new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Accra", dateStyle: "medium", timeStyle: "short" });

export function TargetsPage() {
  const { t } = useTheme();
  const [day, setDay] = useState(ghanaDay);
  const [data, setData] = useState<Progress>({ targets: [], visits: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError("");
    try {
      const result = await snapshot(`/api/customer-support/targets?date=${day}`);
      if (!signal?.aborted) setData(result);
    } catch (e) { if (!signal?.aborted) { setError(e instanceof Error ? e.message : "Unable to load targets."); setData({ targets: [], visits: [] }); } throw e; }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [day]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = poll(() => load(controller.signal));
    const refresh = () => void timer.refresh();
    window.addEventListener("customer-support-progress-changed", refresh);
    return () => { controller.abort(); timer.stop(); window.removeEventListener("customer-support-progress-changed", refresh); };
  }, [load]);
  const card = { background: t.surface, borderColor: t.border, color: t.textPrimary };
  return <div className="mx-auto max-w-[1500px] space-y-6 p-6">
    <header className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="flex items-center gap-2 text-2xl font-extrabold" style={{ color: t.textPrimary }}><Target size={24} /> My Targets</h1><p className="mt-2 text-sm" style={{ color: t.textTertiary }}>Your management targets and progress from recorded work.</p></div><div className="flex gap-2"><label className="text-xs" style={{ color: t.textTertiary }}>Reporting date (Ghana)<input type="date" value={day} max={ghanaDay()} onChange={e => e.target.value && setDay(e.target.value)} className="mt-1 block rounded-lg border px-3 py-2 text-sm" style={card} /></label><button aria-label="Refresh progress" disabled={loading} onClick={() => void load().catch(() => undefined)} className="self-end rounded-lg border p-3 disabled:opacity-50" style={card}><RefreshCw size={17} className={loading ? "animate-spin" : ""} /></button></div></header>
    {error && <p role="alert" className="rounded-xl p-4" style={{ background: t.dangerBg, color: t.dangerText }}>{error}</p>}
    <div className="rounded-xl border p-4 text-sm" style={card}>Calls count once against the officer who logged them. Log completed field visits in <a href="#tasks" className="font-bold underline">Tasks</a>, then record each customer interaction in the customer's activity log and link it to that visit. Weekly totals run Monday–Sunday. All times use Ghana time.</div>
    {!error && <PerformanceSummary data={data.performance}/>}
    {!error && !data.targets.length && <div className="rounded-2xl border p-10 text-center" style={card}>{loading ? "Loading targets…" : "No targets set for this date. Your admin or executive can assign targets from Customer Support."}</div>}
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{data.targets.map(target => {
      const Icon = target.metric === "calls" ? Phone : target.metric === "field_trips" ? MapPin : Users;
      const achieved = target.status === "Achieved";
      return <section key={`${target.metric}-${target.period}`} className="rounded-2xl border p-5" style={card}>
        <div className="flex items-center justify-between"><Icon size={22} style={{ color: t.accent }} /><span className="rounded-full px-3 py-1 text-xs font-bold" style={{ background: achieved ? t.successBg : t.accentSubtle, color: achieved ? t.successText : t.accent }}>{periods[target.period]}</span></div>
        <h2 className="mt-4 font-bold">{labels[target.metric]}</h2><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>{target.start} – {target.end}</p>
        <p className="my-4"><strong className="text-3xl">{target.actual}</strong><span style={{ color: t.textTertiary }}> / {target.goal} {target.period === "per_trip" ? "trips meeting target" : "completed"}</span></p>
        {target.period === "per_trip" && <p className="mb-3 text-sm" style={{ color: t.textTertiary }}>At least {target.amount} interactions on each trip.</p>}
        <div role="progressbar" aria-label={`${labels[target.metric]} ${periods[target.period]} progress`} aria-valuenow={Math.min(target.percent, 100)} aria-valuemin={0} aria-valuemax={100} className="h-2 overflow-hidden rounded-full" style={{ background: t.accentSubtle }}><div className="h-full rounded-full" style={{ width: `${Math.min(target.percent, 100)}%`, background: achieved ? t.successText : t.accent }} /></div>
        <div className="mt-3 flex justify-between text-xs"><span style={{ color: achieved ? t.successText : t.textTertiary }}>{target.status}{target.remaining > 0 ? ` · ${target.remaining} remaining` : ""}</span><strong>{target.percent}%</strong></div>
      </section>;
    })}</div>
    <section className="overflow-hidden rounded-2xl border" style={card}><div className="border-b p-5" style={{ borderColor: t.border }}><h2 className="font-bold">Field visits · reporting week</h2><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>Individual trip progress stays visible alongside daily and weekly totals.</p></div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead style={{ background: t.surfaceSubtle }}><tr>{["Location", "Start / end (Ghana)", "Duration", "Interactions", "Trip target", "Notes"].map(label => <th key={label} className="px-5 py-3">{label}</th>)}</tr></thead><tbody>{data.visits.map(visit => <tr key={visit.id} className="border-t" style={{ borderColor: t.border }}><td className="px-5 py-4 font-semibold">{visit.location}<RecordAttribution record={visit}/></td><td className="whitespace-nowrap px-5 py-4">{visitTime(visit.started_at)}<br />{visitTime(visit.ended_at)}</td><td className="px-5 py-4">{visit.duration_minutes} min</td><td className="px-5 py-4 font-bold">{visit.interactions}</td><td className="px-5 py-4">{visit.interaction_target ? `${visit.interaction_target} · ${visit.remaining === 0 ? "Achieved" : `${visit.remaining} remaining`}` : "Not set"}</td><td className="max-w-xs whitespace-pre-wrap px-5 py-4">{visit.notes}</td></tr>)}{!data.visits.length && <tr><td colSpan={6} className="p-8 text-center" style={{ color: t.textTertiary }}>No field visits recorded for this week.</td></tr>}</tbody></table></div></section>
  </div>;
}
