import { useEffect, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { useSupportIdentity } from "./SupportIdentityContext";
import { useTheme } from "./ThemeContext";
import type { SupportActor } from "./RecordAttribution";

type Activity = { _id: string; actor: SupportActor; timestamp: string; branch: string; operation: string;
  action: string; record_label: string; reference: string; record_type: string; collection: string; fields: string[] };
const emptyFilters = {manager: "", source: "", branch: "", type: "", operation: "", from: "", to: "", search: ""};

export function ActivityLogsPage() {
  const { profile, loading: identityLoading } = useSupportIdentity();
  const { t } = useTheme();
  const [draft, setDraft] = useState(emptyFilters), [filters, setFilters] = useState(emptyFilters);
  const [page, setPage] = useState(1), [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<Activity[]>([]);
  const [managers, setManagers] = useState<Array<{_id: string; name: string; branch: string}>>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [pagination, setPagination] = useState({page: 1, pages: 1, total: 0});
  const [loading, setLoading] = useState(false), [error, setError] = useState("");

  useEffect(() => {
    if (!profile?.can_view_activity_logs) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    const params = new URLSearchParams({page: String(page)});
    Object.entries(filters).forEach(([key, value]) => {if (value) params.set(key, value);});
    fetch(`/api/customer-support/activity-logs?${params}`, {credentials: "same-origin", signal: controller.signal})
      .then(async response => {const data = await response.json(); if (!response.ok || !data.ok) throw Error(data.message || "Unable to load activity logs."); return data;})
      .then(data => {setRows(data.records); setManagers(data.managers); setBranches(data.branches); setPagination(data.pagination);})
      .catch(error => {if (!controller.signal.aborted) {setRows([]); setError(error.message || "Unable to load activity logs.");}})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [profile?.can_view_activity_logs, filters, page, revision]);

  if (identityLoading) return <p className="p-6">Loading profile…</p>;
  if (!profile?.can_view_activity_logs) return <p className="p-6" role="status">Activity logs are available to the main Customer Support team.</p>;
  const inputStyle = {background: t.surface, color: t.textPrimary, borderColor: t.border};
  const field = (key: keyof typeof draft, value: string) => setDraft(current => ({...current, [key]: value}));
  return <section className="p-4 md:p-6 space-y-5" style={{color: t.textPrimary}}>
    <div className="flex justify-between items-start gap-3"><div><h1 className="text-xl font-bold flex items-center gap-2"><History size={22}/>Activity logs</h1>
      <p className="text-sm mt-1" style={{color: t.textMuted}}>See what managers and the main Customer Support team recorded across all branches.</p></div>
      <button className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm" style={inputStyle} onClick={() => setRevision(value => value + 1)} disabled={loading}><RefreshCw size={15}/>Refresh</button></div>
    <form className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 rounded-xl border p-4" style={inputStyle}
      onSubmit={event => {event.preventDefault(); setPage(1); setFilters({...draft});}}>
      <label className="text-xs font-semibold">Manager<select aria-label="Manager" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.manager} onChange={event => field("manager", event.target.value)}>
        <option value="">All managers and support</option>{managers.map(manager => <option key={manager._id} value={manager._id}>{manager.name}{manager.branch ? ` · ${manager.branch}` : ""}</option>)}</select></label>
      <label className="text-xs font-semibold">Recorded by<select aria-label="Recorded by" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.source} onChange={event => field("source", event.target.value)}>
        <option value="">Everyone</option><option value="manager">Managers</option><option value="support">Main Customer Support</option></select></label>
      <label className="text-xs font-semibold">Branch<select aria-label="Branch" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.branch} onChange={event => field("branch", event.target.value)}>
        <option value="">All branches</option>{branches.map(branch => <option key={branch}>{branch}</option>)}</select></label>
      <label className="text-xs font-semibold">Record type<select aria-label="Record type" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.type} onChange={event => field("type", event.target.value)}>
        <option value="">All records</option>{["calls", "tasks", "tickets", "customers", "other"].map(type => <option key={type} value={type}>{type[0].toUpperCase() + type.slice(1)}</option>)}</select></label>
      <label className="text-xs font-semibold">Change<select aria-label="Change" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.operation} onChange={event => field("operation", event.target.value)}>
        <option value="">All changes</option>{["created", "updated", "deleted"].map(value => <option key={value} value={value}>{value[0].toUpperCase() + value.slice(1)}</option>)}</select></label>
      {(["from", "to"] as const).map(key => <label key={key} className="text-xs font-semibold">{key === "from" ? "From" : "To"}<input aria-label={key === "from" ? "From" : "To"} type="date" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft[key]} onChange={event => field(key, event.target.value)}/></label>)}
      <label className="text-xs font-semibold">Search<input aria-label="Search activity" maxLength={100} placeholder="Record, reference, manager…" className="block w-full rounded-lg border px-3 py-2 mt-1 text-sm" style={inputStyle} value={draft.search} onChange={event => field("search", event.target.value)}/></label>
      <div className="flex gap-3 sm:col-span-2 xl:col-span-4"><button type="submit" className="rounded-lg px-4 py-2 text-sm text-white" style={{background: t.accent}}>Apply filters</button>
        <button type="button" className="text-sm" onClick={() => {setDraft(emptyFilters); setFilters(emptyFilters); setPage(1);}}>Reset</button></div>
    </form>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <p className="text-xs" style={{color: t.textMuted}} aria-live="polite">{loading ? "Loading activity…" : `${pagination.total} ${pagination.total === 1 ? 'activity' : 'activities'} · Page ${pagination.page} of ${pagination.pages} · Ghana time (UTC)`}</p>
    <div className="overflow-x-auto rounded-xl border" style={inputStyle}><table className="w-full text-sm text-left"><thead style={{background: t.tableHeaderBg}}><tr>{["Time", "Recorded by", "Branch", "Activity", "Record"].map(title => <th key={title} className="px-4 py-3 font-semibold">{title}</th>)}</tr></thead>
      <tbody>{!loading && rows.map(row => <tr key={row._id} className="border-t" style={{borderColor: t.border}}>
        <td className="px-4 py-3 whitespace-nowrap text-xs">{new Date(row.timestamp).toLocaleString("en-GB", {timeZone: "Africa/Accra"})}</td>
        <td className="px-4 py-3"><span className={`inline-flex rounded-lg px-2 py-1 text-xs font-semibold ${row.actor.role === "manager" ? "bg-cyan-100 text-cyan-800" : "bg-violet-100 text-violet-800"}`}>{row.actor.role === "manager" ? "Manager" : "Main Customer Support"}</span><div className="mt-1">{row.actor.name}</div></td>
        <td className="px-4 py-3">{row.branch || "—"}</td><td className="px-4 py-3"><strong className="capitalize">{row.operation}</strong><div className="capitalize text-xs mt-1">{row.action}</div>{row.fields.length > 0 && <details className="text-xs mt-1"><summary>Changed fields</summary>{row.fields.join(", ")}</details>}</td>
        <td className="px-4 py-3"><div>{row.record_label}</div><div className="text-xs font-mono mt-1">{row.reference}</div><div className="text-xs capitalize mt-1" style={{color: t.textMuted}}>{row.collection.replaceAll("_", " ")}</div></td>
      </tr>)}{!loading && !rows.length && !error && <tr><td colSpan={5} className="p-10 text-center" style={{color: t.textMuted}}>No activities match these filters. New record changes will appear here.</td></tr>}</tbody></table></div>
    <nav className="flex items-center justify-between text-sm" aria-label="Activity pages"><button className="rounded-lg border px-3 py-2 disabled:opacity-40" style={inputStyle} disabled={loading || pagination.page <= 1} onClick={() => setPage(pagination.page - 1)}>Previous</button><span>Page {pagination.page} of {pagination.pages}</span><button className="rounded-lg border px-3 py-2 disabled:opacity-40" style={inputStyle} disabled={loading || pagination.page >= pagination.pages} onClick={() => setPage(pagination.page + 1)}>Next</button></nav>
  </section>;
}
