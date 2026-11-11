import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Clock, Headphones, RefreshCw, Ticket, Truck, X } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTheme } from "./ThemeContext";
import { DeliveryReviewMetrics } from "./DeliveryReviewMetrics";

type Metrics = { totalTickets: number; openTickets: number; resolutionRate: number; slaCompliance: number; callResolutionRate: number; deliveryCompletionRate: number };
type Month = { month: string; tickets: number; resolved: number; resolutionRate: number; slaRate: number; calls: number; callResolutionRate: number };
type Branch = { branch: string; tickets: number; resolved: number; breached: number; resolutionRate: number; slaRate: number };
type Agent = { agent: string; assigned: number; resolved: number; open: number; resolutionRate: number };
type Issue = { issue: string; count: number };
const EMPTY: Metrics = { totalTickets: 0, openTickets: 0, resolutionRate: 0, slaCompliance: 0, callResolutionRate: 0, deliveryCompletionRate: 0 };

export function SatisfactionPage() {
  const { t } = useTheme();
  const [metrics, setMetrics] = useState(EMPTY);
  const [monthly, setMonthly] = useState<Month[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState("");

  const load = async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/customer-support/satisfaction", { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load service-quality data.");
      setMetrics(data.metrics || EMPTY); setMonthly(data.monthly || []); setBranches(data.branches || []); setAgents(data.agents || []); setIssues(data.issues || []); setUpdated(new Date().toLocaleTimeString());
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load service-quality data."); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const cards = [
    { label: "Resolution Rate", value: `${metrics.resolutionRate}%`, sub: `${metrics.totalTickets - metrics.openTickets} tickets resolved`, icon: CheckCircle2, tone: "text-green-700", bg: "bg-green-50" },
    { label: "SLA Compliance", value: `${metrics.slaCompliance}%`, sub: "tickets handled within SLA", icon: Clock, tone: metrics.slaCompliance >= 85 ? "text-blue-700" : "text-red-700", bg: metrics.slaCompliance >= 85 ? "bg-blue-50" : "bg-red-50" },
    { label: "Call Resolution", value: `${metrics.callResolutionRate}%`, sub: "calls with a resolved outcome", icon: Headphones, tone: "text-indigo-700", bg: "bg-indigo-50" },
    { label: "Delivery Completion", value: `${metrics.deliveryCompletionRate}%`, sub: "submitted cards delivered", icon: Truck, tone: "text-emerald-700", bg: "bg-emerald-50" },
    { label: "Open Workload", value: metrics.openTickets.toString(), sub: `of ${metrics.totalTickets} tickets`, icon: Ticket, tone: metrics.openTickets ? "text-amber-700" : "text-green-700", bg: metrics.openTickets ? "bg-amber-50" : "bg-green-50" },
  ];

  return <div className="min-h-full overflow-y-auto p-6" style={{ background: t.pageBg }}><div className="mx-auto max-w-[1500px] space-y-5">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-extrabold text-gray-900">Customer Experience Health</h1><p className="mt-1 text-sm text-gray-500">Operational quality across tickets, calls, and deliveries{updated ? ` · Updated ${updated}` : ""}</p></div><button title="Refresh analytics" onClick={() => void load()} className="flex items-center gap-2 rounded-lg border bg-white px-4 py-2 text-sm font-semibold text-gray-700"><RefreshCw size={15} />Refresh</button></header>
    {error && <div className="flex justify-between rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
    <DeliveryReviewMetrics />
    {loading ? <div className="rounded-lg border bg-white p-16 text-center text-gray-500">Loading service-quality analytics...</div> : <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">{cards.map(card => <article key={card.label} className="rounded-lg border border-gray-200 bg-white p-4"><div className={`flex h-9 w-9 items-center justify-center rounded-md ${card.bg}`}><card.icon size={18} className={card.tone} /></div><strong className="mt-4 block text-2xl text-gray-900">{card.value}</strong><h2 className="mt-1 text-xs font-bold uppercase text-gray-500">{card.label}</h2><p className="mt-1 text-xs text-gray-400">{card.sub}</p></article>)}</div>
      {(metrics.slaCompliance < 80 || metrics.resolutionRate < 70) && <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4"><AlertTriangle size={18} className="mt-0.5 text-amber-700" /><div><strong className="text-sm text-amber-900">Service threshold requires attention</strong><p className="mt-1 text-sm text-amber-700">Review overdue tickets and branch workload. The warning clears automatically when operational performance recovers.</p></div></div>}
      <div className="grid gap-5 xl:grid-cols-2"><Panel title="Six-Month Service Trend" subtitle="Resolution and SLA compliance percentages"><div className="h-[300px]"><ResponsiveContainer width="100%" height="100%"><LineChart data={monthly}><CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" /><XAxis dataKey="month" tick={{ fontSize: 11 }} /><YAxis domain={[0, 100]} tick={{ fontSize: 11 }} /><Tooltip /><Legend /><Line type="monotone" dataKey="resolutionRate" name="Resolution rate" stroke="#16A34A" strokeWidth={2.5} /><Line type="monotone" dataKey="slaRate" name="SLA compliance" stroke="#2563EB" strokeWidth={2.5} /><Line type="monotone" dataKey="callResolutionRate" name="Call resolution" stroke="#7C3AED" strokeWidth={2.5} /></LineChart></ResponsiveContainer></div></Panel><Panel title="Monthly Work Volume" subtitle="Tickets created, tickets resolved, and calls logged"><div className="h-[300px]"><ResponsiveContainer width="100%" height="100%"><BarChart data={monthly}><CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" /><XAxis dataKey="month" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend /><Bar dataKey="tickets" name="Tickets" fill="#93C5FD" radius={[3,3,0,0]} /><Bar dataKey="resolved" name="Resolved" fill="#4ADE80" radius={[3,3,0,0]} /><Bar dataKey="calls" name="Calls" fill="#C4B5FD" radius={[3,3,0,0]} /></BarChart></ResponsiveContainer></div></Panel></div>
      <div className="grid gap-5 xl:grid-cols-[1.25fr_1fr]"><Panel title="Branch Quality" subtitle="Resolution and SLA performance by branch"><DataTable headers={["Branch", "Tickets", "Resolved", "Resolution", "SLA", "Breached"]}>{branches.map(row => <tr key={row.branch} className="border-t"><Cell strong>{row.branch}</Cell><Cell>{row.tickets}</Cell><Cell>{row.resolved}</Cell><Cell><Rate value={row.resolutionRate} /></Cell><Cell><Rate value={row.slaRate} /></Cell><Cell danger={row.breached > 0}>{row.breached}</Cell></tr>)}{!branches.length && <Empty cols={6} />}</DataTable></Panel><Panel title="Issue Demand" subtitle="Ticket volume by issue type"><div className="space-y-4">{issues.map((item, index) => { const max = issues[0]?.count || 1; return <div key={item.issue}><div className="mb-1 flex justify-between text-sm"><span className="font-medium text-gray-700">{item.issue}</span><strong className="text-gray-900">{item.count}</strong></div><div className="h-2 rounded-full bg-gray-100"><div className="h-full rounded-full bg-blue-500" style={{ width: `${item.count * 100 / max}%`, opacity: Math.max(.45, 1 - index * .08) }} /></div></div>})}{!issues.length && <p className="py-8 text-center text-sm text-gray-400">No ticket demand data available.</p>}</div></Panel></div>
      <Panel title="Agent Workload and Resolution" subtitle="Assigned ticket outcomes; this is operational performance, not a survey score"><DataTable headers={["Agent", "Assigned", "Resolved", "Open", "Resolution Rate"]}>{agents.map(row => <tr key={row.agent} className="border-t"><Cell strong>{row.agent}</Cell><Cell>{row.assigned}</Cell><Cell>{row.resolved}</Cell><Cell danger={row.open > 0}>{row.open}</Cell><Cell><Rate value={row.resolutionRate} /></Cell></tr>)}{!agents.length && <Empty cols={5} />}</DataTable></Panel>
    </>}
  </div></div>;
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) { return <section className="rounded-lg border border-gray-200 bg-white p-5"><h2 className="font-bold text-gray-900">{title}</h2><p className="mb-5 mt-1 text-xs text-gray-400">{subtitle}</p>{children}</section>; }
function DataTable({ headers, children }: { headers: string[]; children: ReactNode }) { return <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-400"><tr>{headers.map(header => <th key={header} className="px-3 py-2.5">{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>; }
function Cell({ children, strong, danger }: { children: ReactNode; strong?: boolean; danger?: boolean }) { return <td className={`px-3 py-3 ${strong ? "font-bold text-gray-800" : danger ? "font-semibold text-red-600" : "text-gray-600"}`}>{children}</td>; }
function Rate({ value }: { value: number }) { return <span className={`rounded-full px-2 py-1 text-xs font-bold ${value >= 85 ? "bg-green-50 text-green-700" : value >= 70 ? "bg-amber-50 text-amber-700" : "bg-red-50 text-red-700"}`}>{value}%</span>; }
function Empty({ cols }: { cols: number }) { return <tr><td colSpan={cols} className="p-10 text-center text-gray-400">No records available.</td></tr>; }
