import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useState, type FormEvent } from "react";
import { MapPin, Plus, RefreshCw, X } from "lucide-react";
import { useTheme } from "./ThemeContext";
import { visitTime, type FieldVisit } from "./TargetsPage";

export function FieldVisits() {
  const { t } = useTheme();
  const [visits, setVisits] = useState<FieldVisit[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ location: "", started_at: "", ended_at: "", notes: "" });
  const load = async () => {
    setError(""); setLoading(true);
    try {
      const response = await fetch("/api/customer-support/field-visits", { credentials: "same-origin" });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || "Unable to load field visits.");
      setVisits(result.visits);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load field visits."); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const save = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setSaving(true);
    try {
      const response = await fetch("/api/customer-support/field-visits", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, started_at: `${form.started_at}:00Z`, ended_at: `${form.ended_at}:00Z` }) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || "Unable to save field visit.");
      setVisits(current => [result.visit, ...current].sort((a, b) => b.started_at.localeCompare(a.started_at))); setOpen(false);
      setForm({ location: "", started_at: "", ended_at: "", notes: "" });
      window.dispatchEvent(new Event("customer-support-progress-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to save field visit."); }
    finally { setSaving(false); }
  };
  const card = { background: t.surface, color: t.textPrimary, borderColor: t.border };
  const input = "mt-1 block w-full rounded-lg border px-3 py-2.5 text-sm";
  return <section className="rounded-xl border p-5" style={card}>
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="flex items-center gap-2 font-bold"><MapPin size={19} />Field visits</h2><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>Log a completed trip, then link customer activities to it. Each visit counts once toward your weekly target.</p></div><div className="flex gap-2"><button aria-label="Refresh field visits" disabled={loading} onClick={() => void load()} className="rounded-lg border p-2" style={card}><RefreshCw size={16} /></button><button onClick={() => { setError(""); setOpen(true); }} className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white" style={{ background: t.accent }}><Plus size={16} />Visit to field</button></div></div>
    {error && !open && <p role="alert" className="mt-3 text-sm" style={{ color: t.dangerText }}>{error}</p>}
    <div className="mt-4 max-h-80 overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Visited", "Start / end (Ghana)", "Duration", "Interactions", "Notes"].map(label => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{visits.map(visit => <tr key={visit.id} className="border-t" style={{ borderColor: t.border }}><td className="p-3 font-semibold">{visit.location}<RecordAttribution record={visit}/></td><td className="whitespace-nowrap p-3">{visitTime(visit.started_at)}<br />{visitTime(visit.ended_at)}</td><td className="p-3">{visit.duration_minutes} min</td><td className="p-3">{visit.interactions}</td><td className="max-w-xs whitespace-pre-wrap p-3">{visit.notes}</td></tr>)}{!visits.length && <tr><td colSpan={5} className="p-5 text-center" style={{ color: t.textTertiary }}>{loading ? "Loading visits…" : "No field visits logged yet."}</td></tr>}</tbody></table></div>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><form onSubmit={save} className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-2xl border p-6 shadow-xl" style={card}><div className="flex justify-between"><h3 className="text-lg font-bold">Visit to field</h3><button type="button" disabled={saving} aria-label="Close visit form" onClick={() => setOpen(false)}><X size={20} /></button></div><p className="mb-5 mt-2 text-sm" style={{ color: t.textTertiary }}>Record where you visited and the actual start and end times in Ghana time.</p><div className="space-y-4"><label className="block text-sm">Location visited *<input required maxLength={200} value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} className={input} style={card} placeholder="e.g. Madina market and surrounding shops" /></label><div className="grid gap-3 sm:grid-cols-2">{(["started_at", "ended_at"] as const).map(key => <label key={key} className="block text-sm">{key === "started_at" ? "Starting time" : "Ending time"} *<input required type="datetime-local" max={new Date().toISOString().slice(0, 16)} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} className={input} style={card} /></label>)}</div><label className="block text-sm">Visit notes *<textarea required maxLength={2000} rows={4} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} className={input} style={card} placeholder="Describe the places visited and work completed." /></label>{error && <p role="alert" className="text-sm" style={{ color: t.dangerText }}>{error}</p>}<div className="flex justify-end gap-3"><button type="button" disabled={saving} onClick={() => setOpen(false)} className="rounded-lg border px-4 py-2" style={card}>Cancel</button><button disabled={saving} className="rounded-lg px-4 py-2 font-bold text-white disabled:opacity-50" style={{ background: t.accent }}>{saving ? "Saving…" : "Log visit"}</button></div></div></form></div>}
  </section>;
}
