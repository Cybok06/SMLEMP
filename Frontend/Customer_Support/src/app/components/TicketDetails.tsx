import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useState, type ElementType } from "react";
import { AlertTriangle, Building2, Calendar, CheckCircle2, ChevronLeft, Clock, Edit2, MessageSquare, Save, Send, User, UserCheck, X } from "lucide-react";
import { DirectorySelect, type DirectoryOption } from "./DirectorySelect";
import { PRIORITY_CFG, type Ticket } from "./TicketsPage";
import { useTheme } from "./ThemeContext";

type Update = SupportAttribution & { kind: "reply" | "note"; text: string; author: string; created_at: string };
type LiveTicket = Ticket & { description?: string; phone?: string; rootCause?: string; resolutionNotes?: string; closureNotes?: string; updates?: Update[] };

export function TicketDetails({ ticket: initial, onBack }: { ticket: Ticket; onBack: () => void }) {
  const { t } = useTheme();
  const [ticket, setTicket] = useState<LiveTicket>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState<"reply" | "note">("note");
  const [agent, setAgent] = useState<DirectoryOption | null>(null);
  const [reassigning, setReassigning] = useState(false);
  const [editingResolution, setEditingResolution] = useState(false);
  const [detailTab, setDetailTab] = useState<"details" | "calls">("details");
  const [resolution, setResolution] = useState({ root_cause: "", resolution_notes: "", closure_notes: "" });
  const [linkedCalls, setLinkedCalls] = useState<Array<{id:string;type:string;purpose:string;duration:string;outcome:string;officer:string;date:string;time:string}>>([]);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/customer-support/tickets/${encodeURIComponent(initial.id)}`, { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load ticket.");
      setTicket(data.ticket);
      const callsResponse = await fetch(`/api/customer-support/tickets/${encodeURIComponent(initial.id)}/calls`, { credentials: "same-origin" });
      const callsData = await callsResponse.json();
      if (callsResponse.ok && callsData.ok) setLinkedCalls(callsData.calls || []);
      setResolution({ root_cause: data.ticket.rootCause || "", resolution_notes: data.ticket.resolutionNotes || "", closure_notes: data.ticket.closureNotes || "" });
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load ticket."); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [initial.id]);

  const patch = async (body: Record<string, unknown>) => {
    setError("");
    const response = await fetch(`/api/customer-support/tickets/${encodeURIComponent(ticket.id)}`, { method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok || !data.ok) { setError(data.message || "Unable to update ticket."); return false; }
    setTicket(data.ticket); return true;
  };

  const addUpdate = async () => {
    if (!message.trim()) return;
    const response = await fetch(`/api/customer-support/tickets/${encodeURIComponent(ticket.id)}/updates`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, text: message }) });
    const data = await response.json();
    if (!response.ok || !data.ok) { setError(data.message || "Unable to add update."); return; }
    setTicket(current => ({ ...current, updates: [...(current.updates || []), data.update], responses: (current.responses || 0) + 1 }));
    setMessage("");
  };

  const pc = PRIORITY_CFG[ticket.priority] || PRIORITY_CFG.Medium;
  const statuses = ["New", "Open", "Assigned", "In Progress", "Pending", "Resolved", "Closed"];

  return <div className="h-full overflow-y-auto" style={{ background: t.pageBg }}><div className="mx-auto max-w-[1300px] p-6">
    <button onClick={onBack} className="mb-5 flex items-center gap-2 text-sm font-semibold text-gray-600"><ChevronLeft size={16} />All Tickets</button>
    {error && <div className="mb-4 flex items-center justify-between rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
    {loading ? <div className="rounded-lg border bg-white p-12 text-center text-gray-500">Loading ticket details...</div> : <>
      <header className="mb-5 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4"><div>
          <div className="mb-2 flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-bold text-blue-600">{ticket.id}</span><span className="rounded-full px-2.5 py-1 text-xs font-bold" style={{ color: pc.text, background: pc.bg }}>{ticket.priority}</span>{ticket.slaStatus === "Breached" && <span className="flex items-center gap-1 rounded-full bg-red-50 px-2.5 py-1 text-xs font-bold text-red-700"><AlertTriangle size={12} />SLA breached</span>}</div>
          <h1 className="text-xl font-bold text-gray-900">{ticket.subject}</h1><RecordAttribution record={ticket}/><p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-gray-600">{ticket.description || "No description provided."}</p>
        </div><select value={ticket.status} onChange={e => void patch({ status: e.target.value })} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold">{statuses.map(status => <option key={status}>{status}</option>)}</select></div>
      </header>
      <div className="mb-5 flex border-b border-gray-200">
        <button onClick={() => setDetailTab("details")} className={`border-b-2 px-4 py-2.5 text-sm font-bold ${detailTab === "details" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500"}`}>Details</button>
        {linkedCalls.length > 0 && <button onClick={() => setDetailTab("calls")} className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-bold ${detailTab === "calls" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500"}`}>Calls <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">{linkedCalls.length}</span></button>}
      </div>
      <div className="grid gap-5 lg:grid-cols-[1fr_340px]"><main className="space-y-5">
        {detailTab === "details" ? <>
        <section className="rounded-lg border border-gray-200 bg-white p-5"><h2 className="mb-4 flex items-center gap-2 font-bold text-gray-900"><MessageSquare size={17} />Ticket updates</h2>
          <div className="space-y-3">{(ticket.updates || []).map((update, index) => <div key={`${update.created_at}-${index}`} className={`rounded-lg border p-3 ${update.kind === "note" ? "border-amber-200 bg-amber-50" : "border-blue-200 bg-blue-50"}`}><div className="mb-1 flex justify-between gap-3 text-xs"><strong>{update.author}</strong><RecordAttribution record={update}/><span className="text-gray-500">{new Date(update.created_at).toLocaleString()}</span></div><p className="whitespace-pre-wrap text-sm text-gray-700">{update.text}</p></div>)}{!(ticket.updates || []).length && <p className="text-sm text-gray-500">No updates have been recorded.</p>}</div>
          <div className="mt-4 border-t pt-4"><div className="mb-2 flex gap-2">{(["note", "reply"] as const).map(value => <button key={value} onClick={() => setKind(value)} className={`rounded-md px-3 py-1.5 text-xs font-bold ${kind === value ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}>{value === "note" ? "Internal note" : "Customer reply"}</button>)}</div><textarea value={message} onChange={e => setMessage(e.target.value)} rows={4} placeholder={kind === "note" ? "Add an internal note..." : "Record the reply sent to the customer..."} className="w-full rounded-lg border border-gray-200 p-3 text-sm outline-none focus:border-blue-600" /><button onClick={() => void addUpdate()} disabled={!message.trim()} className="mt-2 flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"><Send size={14} />Save update</button></div>
        </section>
        <section className="rounded-lg border border-gray-200 bg-white p-5"><div className="mb-4 flex items-center justify-between"><h2 className="font-bold text-gray-900">Resolution</h2><button onClick={() => setEditingResolution(value => !value)} className="flex items-center gap-1 text-sm font-semibold text-blue-600"><Edit2 size={14} />{editingResolution ? "Cancel" : "Edit"}</button></div>
          {editingResolution ? <div className="space-y-3">{[["root_cause", "Root cause"], ["resolution_notes", "Resolution notes"], ["closure_notes", "Closure notes"]].map(([key, label]) => <label key={key} className="block text-sm font-semibold text-gray-700">{label}<textarea value={resolution[key as keyof typeof resolution]} onChange={e => setResolution(value => ({ ...value, [key]: e.target.value }))} rows={3} className="mt-1 w-full rounded-lg border border-gray-200 p-3 text-sm font-normal" /></label>)}<button onClick={async () => { if (await patch(resolution)) setEditingResolution(false); }} className="flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-bold text-white"><Save size={14} />Save resolution</button></div>
            : <div className="space-y-3 text-sm"><Text label="Root cause" value={ticket.rootCause} /><Text label="Resolution notes" value={ticket.resolutionNotes} /><Text label="Closure notes" value={ticket.closureNotes} /></div>}
        </section>
        </> : <section className="rounded-lg border border-gray-200 bg-white p-5"><h2 className="mb-4 flex items-center gap-2 font-bold text-gray-900"><Clock size={17} />Calls linked to {ticket.id}</h2><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-xs uppercase text-gray-400"><th className="py-2">Call ID</th><th>Type</th><th>Purpose</th><th>Officer</th><th>Outcome</th><th>Date</th></tr></thead><tbody>{linkedCalls.map(call=><tr key={call.id} className="border-b border-gray-100 last:border-0"><td className="py-3 font-mono font-bold text-blue-600">{call.id}</td><td>{call.type}</td><td>{call.purpose}</td><td>{call.officer}</td><td>{call.outcome}</td><td>{call.date} {call.time}</td></tr>)}</tbody></table></div></section>}
      </main><aside className="space-y-5">
        <section className="rounded-lg border border-gray-200 bg-white p-5"><h2 className="mb-2 font-bold text-gray-900">Ticket information</h2><Info icon={User} label="Customer" value={ticket.customer} /><Info icon={Building2} label="Branch" value={ticket.branch} /><Info icon={UserCheck} label="Assigned agent" value={ticket.owner} /><Info icon={Calendar} label="Created" value={`${ticket.created}, ${ticket.createdTime}`} /><Info icon={Clock} label="SLA due" value={ticket.dueDate} /><Info icon={CheckCircle2} label="Issue type" value={ticket.issueType} /></section>
        <section className="rounded-lg border border-gray-200 bg-white p-5"><div className="mb-3 flex items-center justify-between"><h2 className="font-bold text-gray-900">Assignment</h2><button onClick={() => setReassigning(value => !value)} className="text-sm font-semibold text-blue-600">{reassigning ? "Cancel" : "Reassign"}</button></div>{reassigning ? <div><DirectorySelect kind="agents" value={agent} onChange={setAgent} placeholder="Search all agents..." /><button disabled={!agent} onClick={async () => { if (agent && await patch({ agent_id: agent.id })) { setAgent(null); setReassigning(false); } }} className="mt-3 w-full rounded-lg bg-blue-600 py-2 text-sm font-bold text-white disabled:opacity-50">Assign agent</button></div> : <p className="text-sm text-gray-600">{ticket.owner} · {ticket.branch}</p>}</section>
      </aside></div>
    </>}
  </div></div>;
}

function Info({ icon: Icon, label, value }: { icon: ElementType; label: string; value: string }) { return <div className="flex gap-3 border-b border-gray-100 py-3 last:border-0"><Icon size={16} className="mt-0.5 text-gray-400" /><div><div className="text-xs font-semibold uppercase text-gray-400">{label}</div><div className="mt-1 text-sm font-semibold text-gray-800">{value || "Not set"}</div></div></div>; }
function Text({ label, value }: { label: string; value?: string }) { return <div><strong>{label}</strong><p className="mt-1 whitespace-pre-wrap text-gray-600">{value || "Not recorded."}</p></div>; }
