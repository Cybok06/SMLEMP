import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CalendarDays, CheckCircle2, CheckSquare, ChevronLeft, ChevronRight, Clock, Download, Edit2, LayoutGrid, Plus, RefreshCw, Search, Table2, Trash2, X } from "lucide-react";
import { DirectorySelect, type DirectoryOption } from "./DirectorySelect";
import { useTheme } from "./ThemeContext";
import { FieldVisits } from "./FieldVisits";
import { DeliveryFollowupModal } from "./DeliveryFollowup";

type Task = SupportAttribution & { _id: string; id: string; title: string; description: string; customer: string; customerId: string; assignee: string; assigneeId: string; assigneeInitials: string; dueDate: string; priority: string; status: string; category: string; relatedTo: string; kind?: string; deliveryFollowupId?: string };
const STATUSES = ["All Statuses", "Pending", "In Progress", "Completed", "Overdue"];
const PRIORITIES = ["All Priorities", "Critical", "High", "Medium", "Low"];
const CATEGORIES = ["All Categories", "Support", "Delivery", "Admin", "Reporting", "General"];
const statusStyle: Record<string, string> = { Pending: "bg-amber-50 text-amber-700", "In Progress": "bg-blue-50 text-blue-700", Completed: "bg-green-50 text-green-700", Overdue: "bg-red-50 text-red-700" };
const priorityStyle: Record<string, string> = { Critical: "text-red-700", High: "text-orange-700", Medium: "text-amber-700", Low: "text-green-700" };

export function TasksPage() {
  const { t } = useTheme();
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState({total: 0, totalPages: 1});
  const [counts, setCounts] = useState<Record<string, number>>({});
  const sequence = useRef(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(STATUSES[0]);
  const [priority, setPriority] = useState(PRIORITIES[0]);
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [view, setView] = useState<"table" | "kanban" | "calendar">("table");
  const [followupId, setFollowupId] = useState("");
  const [editing, setEditing] = useState<Task | null | undefined>(undefined);
  useEffect(() => { const apply = (event?: Event) => { let payload: any = event ? (event as CustomEvent).detail : null; if (!payload) { try { payload = JSON.parse(window.sessionStorage.getItem("customer-support-global-search") || "null"); } catch { payload = null; } } if (payload?.page === "tasks") { setSearch(payload.query || ""); window.sessionStorage.removeItem("customer-support-global-search"); } }; apply(); window.addEventListener("customer-support-global-search", apply); return () => window.removeEventListener("customer-support-global-search", apply); }, []);

  const load = async () => {
    const ticket = ++sequence.current;
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/customer-support/tasks?" + new URLSearchParams({paginated: "1", page: String(page), per_page: "25", q: search, status, priority, category}), { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load tasks.");
      if (ticket !== sequence.current) return;
      if (page > data.pagination.totalPages) { setPage(data.pagination.totalPages); return; }
      setTasks(data.tasks || []); setCounts(data.counts || {}); setPagination(data.pagination);
      window.dispatchEvent(new CustomEvent("customer-support-task-count", { detail: data.counts?.Pending || 0 }));
    } catch (e) { if(ticket === sequence.current) setError(e instanceof Error ? e.message : "Unable to load tasks."); }
    finally { if(ticket === sequence.current) setLoading(false); }
  };
  useEffect(() => { const timer = setTimeout(() => void load(), 200); return () => { clearTimeout(timer); ++sequence.current; }; }, [page, search, status, priority, category]);
  useEffect(() => { setPage(1); }, [search, status, priority, category]);

  const openTask = (task: Task) => task.deliveryFollowupId ? setFollowupId(task.deliveryFollowupId) : setEditing(task);

  const filtered = tasks;
  const count = (value: string) => counts[value] || 0;

  const updateStatus = async (task: Task, next: string) => {
    if (task.deliveryFollowupId) { setFollowupId(task.deliveryFollowupId); return; }
    const response = await fetch(`/api/customer-support/tasks/${task._id}`, { method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: next }) });
    const data = await response.json();
    if (!response.ok || !data.ok) { setError(data.message || "Unable to update task."); return; }
    await load();
  };
  const remove = async (task: Task) => {
    if (!window.confirm(`Delete ${task.id}?`)) return;
    const response = await fetch(`/api/customer-support/tasks/${task._id}`, { method: "DELETE", credentials: "same-origin" });
    if (response.ok) await load(); else setError("Unable to delete task.");
  };

  const metrics = [
    ["Total Tasks", Object.values(counts).reduce((sum, value) => sum + value, 0), CheckSquare, "text-gray-700", "All Statuses"],
    ["Pending", count("Pending"), Clock, "text-amber-700", "Pending"],
    ["In Progress", count("In Progress"), RefreshCw, "text-blue-700", "In Progress"],
    ["Completed", count("Completed"), CheckCircle2, "text-green-700", "Completed"],
    ["Overdue", count("Overdue"), AlertTriangle, "text-red-700", "Overdue"],
  ] as const;

  return <div className="min-h-full overflow-y-auto p-6" style={{ background: t.pageBg }}><div className="mx-auto max-w-[1500px] space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-extrabold text-gray-900">Task Management</h1><p className="mt-1 text-sm text-gray-500">{tasks.length} of {pagination.total} matching tasks</p></div><div className="flex flex-wrap gap-2">
      <div className="flex overflow-hidden rounded-lg border bg-white">{([{ key: "table", icon: Table2 }, { key: "kanban", icon: LayoutGrid }, { key: "calendar", icon: CalendarDays }] as const).map(item => <button key={item.key} title={`${item.key} view`} onClick={() => setView(item.key)} className={`p-2.5 ${view === item.key ? "bg-blue-600 text-white" : "text-gray-500"}`}><item.icon size={16} /></button>)}</div>
      <button title="Refresh tasks" onClick={() => void load()} className="rounded-lg border bg-white p-2.5 text-gray-600"><RefreshCw size={16} /></button>
      <a href="/api/customer-support/tasks/export" className="flex items-center gap-2 rounded-lg border bg-white px-3 py-2 text-sm font-semibold text-gray-700"><Download size={15} />Export</a>
      <button onClick={() => setEditing(null)} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white"><Plus size={15} />New Task</button>
    </div></div>
    {error && <div className="flex justify-between rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{metrics.map(([label, value, Icon, color, filter]) => <button key={label} onClick={() => setStatus(filter)} className={`rounded-lg border bg-white p-4 text-left ${status === filter ? "border-blue-500 ring-1 ring-blue-500" : "border-gray-200"}`}><Icon size={18} className={color} /><strong className="mt-3 block text-2xl text-gray-900">{value}</strong><span className="text-xs font-semibold uppercase text-gray-400">{label}</span></button>)}</div>
    <div className="flex flex-wrap gap-3 rounded-lg border border-gray-200 bg-white p-4"><label className="relative min-w-[260px] flex-1"><Search size={15} className="absolute left-3 top-3 text-gray-400" /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search ID, title, customer, or assignee" className="w-full rounded-lg border border-gray-200 py-2.5 pl-9 pr-3 text-sm" /></label><Filter value={status} options={STATUSES} onChange={setStatus} /><Filter value={priority} options={PRIORITIES} onChange={setPriority} /><Filter value={category} options={CATEGORIES} onChange={setCategory} /></div>
    <FieldVisits />
    {loading ? <div className="rounded-lg border bg-white p-16 text-center text-gray-500">Loading tasks...</div> : view === "table" ? <TaskTable tasks={filtered} onEdit={openTask} onStatus={updateStatus} onDelete={remove} /> : view === "kanban" ? <Kanban tasks={filtered} onStatus={updateStatus} onEdit={openTask} /> : <Calendar tasks={filtered} onEdit={openTask} />}
    <div className="flex items-center justify-between rounded border bg-white p-3"><span>Page {page} of {pagination.totalPages} ? All views show this page of results</span><div className="flex gap-3"><button disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>Previous page</button><button disabled={page >= pagination.totalPages || loading} onClick={() => setPage(page + 1)}>Next page</button></div></div>
    {followupId && <DeliveryFollowupModal taskId={followupId} onClose={() => setFollowupId("")} onSaved={() => void load()} />}
    {editing !== undefined && <TaskModal task={editing} onClose={() => setEditing(undefined)} onSaved={saved => { setEditing(undefined); void load(); }} />}
  </div></div>;
}

function Filter({ value, options, onChange }: { value: string; options: string[]; onChange: (value: string) => void }) { return <select value={value} onChange={e => onChange(e.target.value)} className="rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-700">{options.map(option => <option key={option}>{option}</option>)}</select>; }

function TaskTable({ tasks, onEdit, onStatus, onDelete }: { tasks: Task[]; onEdit: (task: Task) => void; onStatus: (task: Task, status: string) => void; onDelete: (task: Task) => void }) {
  const [page, setPage] = useState(1); const perPage = 10; const pages = Math.max(Math.ceil(tasks.length / perPage), 1); const rows = tasks.slice((page - 1) * perPage, page * perPage);
  useEffect(() => { if (page > pages) setPage(pages); }, [page, pages]);
  return <div className="overflow-hidden rounded-lg border border-gray-200 bg-white"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-400"><tr>{["Task", "Customer", "Assignee", "Priority", "Category", "Due", "Status", "Actions"].map(h => <th key={h} className="px-4 py-3">{h}</th>)}</tr></thead><tbody>{rows.map(task => <tr key={task._id} className="border-t border-gray-100"><td className="px-4 py-3"><button onClick={() => onEdit(task)} className="text-left"><span className="font-mono text-xs font-bold text-blue-600">{task.id}</span><strong className="mt-1 block max-w-[280px] text-gray-800">{task.title}</strong><RecordAttribution record={task}/>{task.relatedTo && <span className="text-xs text-gray-400">{task.relatedTo}</span>}</button></td><td className="px-4 py-3 text-gray-600">{task.customer}</td><td className="px-4 py-3 text-gray-700">{task.assignee}</td><td className={`px-4 py-3 font-bold ${priorityStyle[task.priority] || "text-gray-600"}`}>{task.priority}</td><td className="px-4 py-3 text-gray-600">{task.category}</td><td className="px-4 py-3 text-gray-600">{task.dueDate || "Not set"}</td><td className="px-4 py-3">{task.deliveryFollowupId ? <button onClick={() => onEdit(task)} className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusStyle[task.status]}`}>{task.status}</button> : <select value={task.status} onChange={e => void onStatus(task, e.target.value)} className={`rounded-full border-0 px-2.5 py-1 text-xs font-bold ${statusStyle[task.status] || "bg-gray-100"}`}>{STATUSES.slice(1).map(s => <option key={s}>{s}</option>)}</select>}</td><td className="px-4 py-3"><div className="flex gap-1"><button title="Edit task" onClick={() => onEdit(task)} className="p-2 text-blue-600"><Edit2 size={15} /></button>{!task.deliveryFollowupId && <button title="Delete task" onClick={() => void onDelete(task)} className="p-2 text-red-600"><Trash2 size={15} /></button>}</div></td></tr>)}{rows.length === 0 && <tr><td colSpan={8} className="p-12 text-center text-gray-400">No tasks match the selected filters.</td></tr>}</tbody></table></div><div className="flex items-center justify-between border-t px-4 py-3 text-sm text-gray-500"><span>Page {page} of {pages}</span><div className="flex gap-2"><button disabled={page === 1} onClick={() => setPage(value => value - 1)} className="rounded border px-3 py-1.5 disabled:opacity-40">Previous</button><button disabled={page === pages} onClick={() => setPage(value => value + 1)} className="rounded border px-3 py-1.5 disabled:opacity-40">Next</button></div></div></div>;
}

function Kanban({ tasks, onStatus, onEdit }: { tasks: Task[]; onStatus: (task: Task, status: string) => void; onEdit: (task: Task) => void }) { return <div className="grid gap-4 xl:grid-cols-4">{STATUSES.slice(1).map(status => <section key={status} onDragOver={e => e.preventDefault()} onDrop={e => { const task = tasks.find(item => item._id === e.dataTransfer.getData("task")); if (task) void onStatus(task, status); }} className="min-h-[320px] rounded-lg border border-gray-200 bg-gray-50 p-3"><h2 className="mb-3 flex justify-between text-sm font-bold text-gray-700"><span>{status}</span><span>{tasks.filter(t => t.status === status).length}</span></h2><div className="space-y-2">{tasks.filter(t => t.status === status).map(task => <button draggable onDragStart={e => e.dataTransfer.setData("task", task._id)} onClick={() => onEdit(task)} key={task._id} className="w-full rounded-lg border bg-white p-3 text-left shadow-sm"><span className="font-mono text-xs font-bold text-blue-600">{task.id}</span><strong className="my-2 block text-sm text-gray-800">{task.title}</strong><RecordAttribution record={task}/><span className="text-xs text-gray-500">{task.assignee} · {task.dueDate}</span></button>)}</div></section>)}</div>; }

function Calendar({ tasks, onEdit }: { tasks: Task[]; onEdit: (task: Task) => void }) {
  const today = new Date();
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [selectedDate, setSelectedDate] = useState(() => dateKey(today));
  const tasksByDate = useMemo(() => tasks.reduce<Record<string, Task[]>>((result, task) => {
    if (task.dueDate) (result[task.dueDate] ||= []).push(task);
    return result;
  }, {}), [tasks]);
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const gridStart = new Date(first); gridStart.setDate(first.getDate() - first.getDay());
  const days = Array.from({ length: 42 }, (_, index) => { const day = new Date(gridStart); day.setDate(gridStart.getDate() + index); return day; });
  const selectedTasks = tasksByDate[selectedDate] || [];
  const moveMonth = (offset: number) => { const next = new Date(month.getFullYear(), month.getMonth() + offset, 1); setMonth(next); setSelectedDate(dateKey(next)); };
  const goToday = () => { setMonth(new Date(today.getFullYear(), today.getMonth(), 1)); setSelectedDate(dateKey(today)); };

  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex items-center gap-2"><CalendarDays size={18} className="text-blue-600" /><h2 className="text-base font-bold text-gray-900">{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h2></div>
        <div className="flex items-center gap-2"><button onClick={goToday} className="rounded-md border px-3 py-1.5 text-xs font-bold text-gray-600">Today</button><button title="Previous month" onClick={() => moveMonth(-1)} className="rounded-md border p-1.5 text-gray-600"><ChevronLeft size={16} /></button><button title="Next month" onClick={() => moveMonth(1)} className="rounded-md border p-1.5 text-gray-600"><ChevronRight size={16} /></button></div>
      </header>
      <div className="overflow-x-auto"><div className="min-w-[760px]">
        <div className="grid grid-cols-7 border-b bg-gray-50">{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(day => <div key={day} className="px-2 py-2 text-center text-xs font-bold uppercase text-gray-400">{day}</div>)}</div>
        <div className="grid grid-cols-7">{days.map(day => {
          const key = dateKey(day); const rows = tasksByDate[key] || []; const inMonth = day.getMonth() === month.getMonth(); const isToday = key === dateKey(today); const isSelected = key === selectedDate;
          return <button key={key} onClick={() => setSelectedDate(key)} className={`h-[132px] overflow-hidden border-b border-r p-2 text-left align-top transition-colors ${isSelected ? "bg-blue-50 ring-1 ring-inset ring-blue-500" : inMonth ? "bg-white hover:bg-gray-50" : "bg-gray-50/60"}`}>
            <div className="mb-2 flex items-center justify-between"><span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${isToday ? "bg-blue-600 text-white" : inMonth ? "text-gray-700" : "text-gray-300"}`}>{day.getDate()}</span>{rows.length > 0 && <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-600">{rows.length}</span>}</div>
            <div className="space-y-1">{rows.slice(0, 3).map(task => <div key={task._id} title={task.title} className={`truncate rounded px-1.5 py-1 text-[10px] font-semibold ${statusStyle[task.status] || "bg-gray-100 text-gray-600"}`}>{task.title}</div>)}{rows.length > 3 && <div className="px-1 text-[10px] font-semibold text-blue-600">+{rows.length - 3} more</div>}</div>
          </button>;
        })}</div>
      </div></div>
    </section>
    <aside className="rounded-lg border border-gray-200 bg-white p-4 xl:sticky xl:top-0 xl:self-start">
      <h2 className="font-bold text-gray-900">{new Date(`${selectedDate}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</h2>
      <p className="mb-4 mt-1 text-xs text-gray-400">{selectedTasks.length} task{selectedTasks.length === 1 ? "" : "s"} due</p>
      <div className="space-y-2">{selectedTasks.map(task => <button key={task._id} onClick={() => onEdit(task)} className="w-full rounded-lg border border-gray-200 p-3 text-left hover:border-blue-300"><div className="flex items-center justify-between gap-2"><span className="font-mono text-xs font-bold text-blue-600">{task.id}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${statusStyle[task.status]}`}>{task.status}</span></div><strong className="mt-2 block text-sm text-gray-800">{task.title}</strong><RecordAttribution record={task}/><span className="mt-1 block text-xs text-gray-500">{task.assignee}</span></button>)}{selectedTasks.length === 0 && <div className="rounded-lg border border-dashed p-8 text-center text-sm text-gray-400">No tasks due on this day.</div>}</div>
      {tasks.some(task => !task.dueDate) && <p className="mt-4 border-t pt-3 text-xs text-amber-700">{tasks.filter(task => !task.dueDate).length} task(s) have no due date and are not shown on the calendar.</p>}
    </aside>
  </div>;
}

function dateKey(value: Date) { const year = value.getFullYear(); const month = String(value.getMonth() + 1).padStart(2, "0"); const day = String(value.getDate()).padStart(2, "0"); return `${year}-${month}-${day}`; }

function TaskModal({ task, onClose, onSaved }: { task: Task | null; onClose: () => void; onSaved: (task: Task) => void }) {
  const [form, setForm] = useState({ title: task?.title || "", description: task?.description || "", due_date: task?.dueDate || "", priority: task?.priority || "Medium", status: task?.status || "Pending", category: task?.category || "Support", related_to: task?.relatedTo || "" });
  const [customer, setCustomer] = useState<DirectoryOption | null>(task?.customerId ? { id: task.customerId, name: task.customer } : null);
  const [agent, setAgent] = useState<DirectoryOption | null>(task?.assigneeId ? { id: task.assigneeId, name: task.assignee } : null);
  const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const save = async () => { if (!form.title.trim() || !form.due_date) { setError("Title and due date are required."); return; } setSaving(true); setError(""); try { const response = await fetch(task ? `/api/customer-support/tasks/${task._id}` : "/api/customer-support/tasks", { method: task ? "PATCH" : "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, customer_id: customer?.id || "", assignee_id: agent?.id || "" }) }); const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.message || "Unable to save task."); onSaved(data.task); } catch (e) { setError(e instanceof Error ? e.message : "Unable to save task."); } finally { setSaving(false); } };
  return <div onClick={onClose} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><div onClick={e => e.stopPropagation()} className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white shadow-xl"><header className="flex items-center justify-between border-b p-5"><h2 className="font-bold text-gray-900">{task ? `Edit ${task.id}` : "Create Task"}</h2><button onClick={onClose}><X size={18} /></button></header><div className="grid gap-4 p-5 md:grid-cols-2"><label className="md:col-span-2 text-sm font-semibold">Title<input value={form.title} onChange={e => setForm(v => ({ ...v, title: e.target.value }))} className="mt-1 w-full rounded-lg border p-2.5 font-normal" /></label><label className="md:col-span-2 text-sm font-semibold">Description<textarea value={form.description} onChange={e => setForm(v => ({ ...v, description: e.target.value }))} rows={3} className="mt-1 w-full rounded-lg border p-2.5 font-normal" /></label><div><span className="mb-1 block text-sm font-semibold">Customer</span><DirectorySelect kind="customers" value={customer} onChange={setCustomer} placeholder="Search customer (optional)..." /></div><div><span className="mb-1 block text-sm font-semibold">Assigned user</span><DirectorySelect kind="agents" value={agent} onChange={setAgent} placeholder="Search agents..." /></div><label className="text-sm font-semibold">Due date<input type="date" value={form.due_date} onChange={e => setForm(v => ({ ...v, due_date: e.target.value }))} className="mt-1 w-full rounded-lg border p-2.5 font-normal" /></label><label className="text-sm font-semibold">Related ticket/call<input value={form.related_to} onChange={e => setForm(v => ({ ...v, related_to: e.target.value }))} className="mt-1 w-full rounded-lg border p-2.5 font-normal" /></label><FilterField label="Priority" value={form.priority} values={PRIORITIES.slice(1)} onChange={value => setForm(v => ({ ...v, priority: value }))} /><FilterField label="Status" value={form.status} values={STATUSES.slice(1)} onChange={value => setForm(v => ({ ...v, status: value }))} /><FilterField label="Category" value={form.category} values={CATEGORIES.slice(1)} onChange={value => setForm(v => ({ ...v, category: value }))} />{error && <p className="md:col-span-2 text-sm text-red-600">{error}</p>}</div><footer className="flex justify-end gap-2 border-t p-5"><button onClick={onClose} className="rounded-lg border px-4 py-2 text-sm">Cancel</button><button disabled={saving} onClick={() => void save()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{saving ? "Saving..." : "Save Task"}</button></footer></div></div>;
}

function FilterField({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) { return <label className="text-sm font-semibold">{label}<select value={value} onChange={e => onChange(e.target.value)} className="mt-1 w-full rounded-lg border bg-white p-2.5 font-normal">{values.map(item => <option key={item}>{item}</option>)}</select></label>; }
