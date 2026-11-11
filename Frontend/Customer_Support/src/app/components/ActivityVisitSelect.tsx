import { useEffect, useState } from "react";
import { visitTime, type FieldVisit } from "./TargetsPage";

export function ActivityVisitSelect({ value, onChange }: { value: string; onChange: (id: string, startedAt: string) => void }) {
  const [visits, setVisits] = useState<FieldVisit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/customer-support/field-visits", { credentials: "same-origin", signal: controller.signal })
      .then(async response => { const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load visits."); setVisits(data.visits || []); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  return <label className="block text-xs font-semibold text-gray-600">Field visit *
    <select value={value} disabled={loading} onChange={e => onChange(e.target.value, visits.find(v => v.id === e.target.value)?.started_at.slice(0, 16) || "")} className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">{loading ? "Loading visits…" : "Select your field visit"}</option>{visits.map(visit => <option key={visit.id} value={visit.id}>{visit.location} · {visitTime(visit.started_at)} – {visitTime(visit.ended_at)}</option>)}</select>
    <span className="mt-2 block font-normal text-gray-500">Each interaction counts once toward your daily, weekly, and per-trip interaction targets. Enter the actual interaction time within this visit (Ghana time). Log the completed visit in Tasks first.</span>
    {error && <span role="alert" className="mt-2 block text-red-600">{error}</span>}
    {!loading && !error && !visits.length && <span className="mt-2 block text-amber-700">No field visits logged. Go to Tasks → Visit to field to record one.</span>}
  </label>;
}
