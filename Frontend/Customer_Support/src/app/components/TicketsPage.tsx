import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useState } from "react";
import {
  Search, Plus, ChevronDown, ChevronLeft, ChevronRight, X,
  Clock, AlertTriangle, CheckCircle2, Circle, MoreHorizontal,
  Filter, SlidersHorizontal, Download, RefreshCw,
  Inbox, Loader, UserCheck, Zap, PauseCircle, XCircle,
  ArrowUpRight, Users, Phone, Mail, Monitor, MessageCircle,
} from "lucide-react";
import { TicketDetails } from "./TicketDetails";
import { useTheme } from "./ThemeContext";
import { DirectorySelect, type DirectoryOption } from "./DirectorySelect";

/* ─── Status Configuration (7 statuses) ──────────────────────────── */
export const STATUS_CFG: Record<string, {
  label: string; text: string; bg: string; border: string;
  dot: string; icon: React.ElementType; light: string;
}> = {
  New:         { label: "New",         text: "#475569", bg: "#F1F5F9", border: "#CBD5E1", dot: "#64748B", icon: Inbox,       light: "#F8FAFC" },
  Open:        { label: "Open",        text: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD", dot: "#2563EB", icon: Circle,      light: "#EFF6FF" },
  Assigned:    { label: "Assigned",    text: "#6D28D9", bg: "#EDE9FE", border: "#C4B5FD", dot: "#7C3AED", icon: UserCheck,   light: "#F5F3FF" },
  "In Progress":{ label: "In Progress",text: "#C2410C", bg: "#FFEDD5", border: "#FDBA74", dot: "#EA580C", icon: Loader,      light: "#FFF7ED" },
  Pending:     { label: "Pending",     text: "#B45309", bg: "#FEF3C7", border: "#FCD34D", dot: "#D97706", icon: PauseCircle, light: "#FFFBEB" },
  Resolved:    { label: "Resolved",    text: "#15803D", bg: "#DCFCE7", border: "#86EFAC", dot: "#16A34A", icon: CheckCircle2,light: "#F0FDF4" },
  Closed:      { label: "Closed",      text: "#374151", bg: "#F3F4F6", border: "#D1D5DB", dot: "#9CA3AF", icon: XCircle,     light: "#F9FAFB" },
};

export const PRIORITY_CFG: Record<string, { text: string; bg: string; border: string; dot: string }> = {
  Critical: { text: "#991B1B", bg: "#FEE2E2", border: "#FCA5A5", dot: "#DC2626" },
  High:     { text: "#C2410C", bg: "#FFEDD5", border: "#FDBA74", dot: "#EA580C" },
  Medium:   { text: "#B45309", bg: "#FEF3C7", border: "#FCD34D", dot: "#D97706" },
  Low:      { text: "#166534", bg: "#DCFCE7", border: "#86EFAC", dot: "#16A34A" },
};

const SLA_CFG: Record<string, { text: string; bg: string; border: string; label: string }> = {
  "On Track": { text: "#15803D", bg: "#DCFCE7", border: "#86EFAC", label: "On Track" },
  "At Risk":  { text: "#B45309", bg: "#FEF3C7", border: "#FCD34D", label: "At Risk" },
  "Breached": { text: "#991B1B", bg: "#FEE2E2", border: "#FCA5A5", label: "Breached" },
  "Resolved": { text: "#15803D", bg: "#DCFCE7", border: "#86EFAC", label: "Resolved" },
};

const CHANNEL_CFG: Record<string, { icon: React.ElementType; color: string }> = {
  Phone:     { icon: Phone,          color: "#16A34A" },
  Email:     { icon: Mail,           color: "#0B5FFF" },
  "Walk-in": { icon: Users,          color: "#8B5CF6" },
  App:       { icon: Monitor,        color: "#06B6D4" },
  WhatsApp:  { icon: MessageCircle,  color: "#16A34A" },
};

export type Ticket = SupportAttribution & {
  id: string; subject: string; description?: string; customer: string; customerId: string; phone?: string;
  issueType: string; priority: string; status: string; owner: string; branch: string; channel: string;
  created: string; createdTime: string; dueDate: string; slaStatus: string; slaRemaining: string;
  responses: number; tags: string[];
  phone?: string; location?: string; imageUrl?: string; customerBranch?: string;
};

const STATUS_ORDER = ["New","Open","Assigned","In Progress","Pending","Resolved","Closed"];
const ALL_ISSUES = "All Issue Types";
const ALL_BRANCHES = "All Branches";
const ALL_OWNERS = "All Owners";
const PRIORITIES   = ["All Priorities","Critical","High","Medium","Low"];

/* ─── Small helpers ────────────────────────────────────────────────── */
function StatusPill({ status }: { status: string }) {
  const cfg = STATUS_CFG[status];
  if (!cfg) return null;
  const Icon = cfg.icon;
  return (
    <div className="flex items-center gap-1.5 rounded-full px-2.5 py-1 w-fit" style={{ background: cfg.bg, border: `1px solid ${cfg.border}` }}>
      <Icon size={11} style={{ color: cfg.text }} />
      <span style={{ fontSize: "0.72rem", fontWeight: 700, color: cfg.text, whiteSpace: "nowrap" }}>{status}</span>
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const cfg = PRIORITY_CFG[priority];
  if (!cfg) return null;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-2 h-2 rounded-full" style={{ background: cfg.dot }} />
      <span style={{ fontSize: "0.78rem", fontWeight: 700, color: cfg.text }}>{priority}</span>
    </div>
  );
}

function SlaBadge({ slaStatus, remaining }: { slaStatus: string; remaining: string }) {
  const cfg = SLA_CFG[slaStatus] ?? SLA_CFG["On Track"];
  return (
    <div>
      <span className="px-2.5 py-1 rounded-full" style={{ fontSize: "0.7rem", fontWeight: 700, color: cfg.text, background: cfg.bg, border: `1px solid ${cfg.border}` }}>
        {cfg.label}
      </span>
      {remaining !== "—" && (
        <p style={{ fontSize: "0.65rem", color: slaStatus === "Breached" ? "#DC2626" : "#9CA3AF", marginTop: "3px", fontWeight: slaStatus === "Breached" ? 700 : 400 }}>
          {remaining}
        </p>
      )}
    </div>
  );
}

function DropFilter({ label, value, options, onChange, searchable = false }: { label: string; value: string; options: string[]; onChange: (v: string) => void; searchable?: boolean }) {
  const [open, setOpen] = useState(false);
  const [optionSearch, setOptionSearch] = useState("");
  const isActive = value !== options[0];
  const visibleOptions = searchable && optionSearch
    ? options.filter((option, index) => index === 0 || option.toLowerCase().includes(optionSearch.toLowerCase()))
    : options;
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all"
        style={{
          background: isActive ? "#EFF6FF" : "#FFFFFF",
          border: `1.5px solid ${isActive ? "#0B5FFF" : "#E5E7EB"}`,
          color: isActive ? "#0B5FFF" : "#374151",
          fontWeight: isActive ? 600 : 400,
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ maxWidth: "130px", overflow: "hidden", textOverflow: "ellipsis" }}>
          {isActive ? value : label}
        </span>
        {isActive
          ? <X size={12} onClick={e => { e.stopPropagation(); onChange(options[0]); }} style={{ flexShrink: 0 }} />
          : <ChevronDown size={13} style={{ color: "#9CA3AF", flexShrink: 0 }} />}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute top-10 left-0 rounded-xl z-50 overflow-hidden" style={{ minWidth: searchable ? "270px" : "190px", background: "#FFFFFF", boxShadow: "0 12px 36px rgba(0,0,0,0.12)", border: "1px solid #E5E7EB" }}>
            {searchable && <div className="border-b border-gray-100 p-2"><div className="relative"><Search size={14} className="absolute left-2.5 top-2.5 text-gray-400" /><input autoFocus value={optionSearch} onChange={event => setOptionSearch(event.target.value)} placeholder="Search owner..." className="w-full rounded-lg border border-gray-200 py-2 pl-8 pr-2 text-sm outline-none focus:border-blue-600" /></div></div>}
            <div className="max-h-64 overflow-y-auto py-1">
            {visibleOptions.map(opt => (
              <button key={opt} onClick={() => { onChange(opt); setOpen(false); }}
                className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-left transition-colors"
                style={{ background: value === opt ? "#EFF6FF" : "transparent", color: value === opt ? "#0B5FFF" : "#374151", fontWeight: value === opt ? 600 : 400 }}
                onMouseEnter={e => { if (value !== opt) (e.currentTarget as HTMLElement).style.background = "#F9FAFB"; }}
                onMouseLeave={e => { if (value !== opt) (e.currentTarget as HTMLElement).style.background = "transparent"; }}>
                <span>{opt}</span>
                {value === opt && <span style={{ color: "#0B5FFF", fontSize: "0.7rem" }}>✓</span>}
              </button>
            ))}
            {visibleOptions.length === 0 && <div className="px-4 py-5 text-center text-sm text-gray-500">No matching owner</div>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ─── Main Component ────────────────────────────────────────────────── */
export function TicketsPage() {
  const { t } = useTheme();
  const [search, setSearch]           = useState("");
  const [statusFilter, setStatus]     = useState("All");
  const [priorityFilter, setPriority] = useState(PRIORITIES[0]);
  const [branchFilter, setBranch]     = useState(ALL_BRANCHES);
  const [ownerFilter, setOwner]       = useState(ALL_OWNERS);
  const [issueFilter, setIssue]       = useState(ALL_ISSUES);
  const [page, setPage]               = useState(1);
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const [selectedRows, setSelectedRows]     = useState<string[]>([]);
  const [showCreate, setShowCreate]         = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [slaBreached, setSlaBreached] = useState(0);
  const [filterOptions, setFilterOptions] = useState({ branches: [] as string[], owners: [] as string[], issues: [] as string[] });
  const [searchTerm, setSearchTerm] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [customer, setCustomer] = useState<DirectoryOption | null>(null);
  const [agent, setAgent] = useState<DirectoryOption | null>(null);
  const [form, setForm] = useState({ issue_type: "General Enquiry", subject: "", description: "", priority: "Medium", channel: "Call", sla_hours: "24" });
  const [createError, setCreateError] = useState("");
  const [saving, setSaving] = useState(false);
  const rowsPerPage = 10;
  useEffect(() => { const apply = (event?: Event) => { let payload: any = event ? (event as CustomEvent).detail : null; if (!payload) { try { payload = JSON.parse(window.sessionStorage.getItem("customer-support-global-search") || "null"); } catch { payload = null; } } if (payload?.page === "tickets") { setSearch(payload.query || ""); setPage(1); window.sessionStorage.removeItem("customer-support-global-search"); } }; apply(); window.addEventListener("customer-support-global-search", apply); return () => window.removeEventListener("customer-support-global-search", apply); }, []);

  const loadTickets = async () => {
    setLoading(true); setLoadError("");
    try {
      const params = new URLSearchParams({ page: String(page), per_page: String(rowsPerPage) });
      if (searchTerm) params.set("q", searchTerm);
      if (statusFilter !== "All") params.set("status", statusFilter);
      if (priorityFilter !== PRIORITIES[0]) params.set("priority", priorityFilter);
      if (branchFilter !== ALL_BRANCHES) params.set("branch", branchFilter);
      if (ownerFilter !== ALL_OWNERS) params.set("owner", ownerFilter);
      if (issueFilter !== ALL_ISSUES) params.set("issue", issueFilter);
      const response = await fetch(`/api/customer-support/tickets?${params}`, { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load tickets.");
      setTickets(data.tickets ?? []);
      setTotal(data.pagination?.total ?? 0); setTotalPages(data.pagination?.total_pages ?? 1);
      setCounts(data.counts ?? {}); setSlaBreached(data.sla_breached ?? 0); setFilterOptions(data.filters ?? {branches:[],owners:[],issues:[]});
    } catch (error) { setLoadError(error instanceof Error ? error.message : "Unable to load tickets."); }
    finally { setLoading(false); }
  };
  useEffect(() => { const timer=window.setTimeout(()=>setSearchTerm(search.trim()),250); return()=>window.clearTimeout(timer); }, [search]);
  useEffect(() => { void loadTickets(); }, [page, searchTerm, statusFilter, priorityFilter, branchFilter, ownerFilter, issueFilter]);
  useEffect(() => { const open = () => { window.sessionStorage.removeItem("customer-support-open-new-ticket"); setShowCreate(true); }; if (window.sessionStorage.getItem("customer-support-open-new-ticket") === "1") open(); window.addEventListener("customer-support-open-new-ticket", open); return () => window.removeEventListener("customer-support-open-new-ticket", open); }, []);

  const createTicket = async () => {
    setCreateError("");
    if (!customer || !agent) { setCreateError("Select both a customer and an agent."); return; }
    setSaving(true);
    try {
      const response = await fetch("/api/customer-support/tickets", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, customer_id: customer.id, agent_id: agent.id, sla_hours: Number(form.sla_hours) }) });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to create ticket.");
      setShowCreate(false); setCustomer(null); setAgent(null); setPage(1); await loadTickets();
      setForm({ issue_type: "General Enquiry", subject: "", description: "", priority: "Medium", channel: "Call", sla_hours: "24" });
    } catch (error) { setCreateError(error instanceof Error ? error.message : "Unable to create ticket."); }
    finally { setSaving(false); }
  };

  const issueTypes = [ALL_ISSUES, ...filterOptions.issues];
  const branches = [ALL_BRANCHES, ...filterOptions.branches];
  const owners = [ALL_OWNERS, ...filterOptions.owners];

  /* Open ticket detail */
  if (selectedTicket) {
    return <TicketDetails ticket={selectedTicket} onBack={() => setSelectedTicket(null)} />;
  }

  const paginated = tickets;
  const activeFilters = [priorityFilter, branchFilter, ownerFilter, issueFilter].filter(f =>
    f !== PRIORITIES[0] && f !== ALL_BRANCHES && f !== ALL_OWNERS && f !== ALL_ISSUES).length
    + (statusFilter !== "All" ? 1 : 0);

  const toggleRow = (id: string) => setSelectedRows(prev => prev.includes(id) ? prev.filter(r => r !== id) : [...prev, id]);
  const toggleAll = () => setSelectedRows(selectedRows.length === paginated.length ? [] : paginated.map(t => t.id));

  const queryParams = () => { const params=new URLSearchParams(); if(searchTerm)params.set("q",searchTerm); if(statusFilter!=="All")params.set("status",statusFilter); if(priorityFilter!==PRIORITIES[0])params.set("priority",priorityFilter); if(branchFilter!==ALL_BRANCHES)params.set("branch",branchFilter); if(ownerFilter!==ALL_OWNERS)params.set("owner",ownerFilter); if(issueFilter!==ALL_ISSUES)params.set("issue",issueFilter); return params; };

  return (
    <div className="flex flex-col" style={{ background: t.pageBg, minHeight: "100%" }}>
      <div className="p-6 space-y-5">

        {/* ── Page Header ── */}
        <div className="flex items-center justify-between">
          <div>
            <h2 style={{ fontSize: "1.3rem", fontWeight: 800, color: "#111827", lineHeight: 1 }}>Ticket Management</h2>
            <p style={{ fontSize: "0.78rem", color: "#9CA3AF", marginTop: "5px" }}>
              {total} ticket{total !== 1 ? "s" : ""} · {slaBreached} SLA breached
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <button onClick={() => { window.location.href=`/api/customer-support/tickets/export?${queryParams()}`; }} className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm" style={{ background: "#FFFFFF", border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F9FAFB"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#FFFFFF"}>
              <Download size={14} style={{ color: "#6B7280" }} /> Export
            </button>
            <button onClick={() => void loadTickets()} disabled={loading} aria-label="Refresh tickets" className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm disabled:opacity-50" style={{ background: "#FFFFFF", border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F9FAFB"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#FFFFFF"}>
              <RefreshCw size={14} style={{ color: "#6B7280" }} />
            </button>
            <button onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm transition-all"
              style={{ background: "#0B5FFF", color: "#FFFFFF", fontWeight: 700, boxShadow: "0 2px 10px rgba(11,95,255,0.3)" }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#0040CC"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#0B5FFF"}>
              <Plus size={15} /> Create Ticket
            </button>
          </div>
        </div>

        {/* ── Status Cards Row ── */}
        <div className="grid gap-2.5" style={{ gridTemplateColumns: `repeat(${STATUS_ORDER.length}, 1fr)` }}>
          {STATUS_ORDER.map(s => {
            const cfg = STATUS_CFG[s];
            const Icon = cfg.icon;
            const isActive = statusFilter === s;
            return (
              <button key={s} onClick={() => { setStatus(isActive ? "All" : s); setPage(1); }}
                className="rounded-xl p-4 text-left transition-all"
                style={{
                  background: isActive ? cfg.bg : "#FFFFFF",
                  border: `1.5px solid ${isActive ? cfg.border : "#E8ECEF"}`,
                  boxShadow: isActive ? `0 0 0 3px ${cfg.dot}18` : "0 1px 4px rgba(0,0,0,0.04)",
                }}>
                <div className="flex items-center justify-between mb-2.5">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: isActive ? "#FFFFFF" : cfg.bg }}>
                    <Icon size={14} style={{ color: cfg.text }} />
                  </div>
                  {s === "New" && counts[s] > 0 && (
                    <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#0B5FFF" }} />
                  )}
                  {(s === "Assigned" || s === "Open") && counts[s] > 0 && slaBreached > 0 && (
                    <AlertTriangle size={12} style={{ color: "#F59E0B" }} />
                  )}
                </div>
                <p style={{ fontSize: "1.6rem", fontWeight: 800, color: isActive ? cfg.text : "#111827", lineHeight: 1 }}>{counts[s] ?? 0}</p>
                <p style={{ fontSize: "0.68rem", fontWeight: 600, color: isActive ? cfg.text : "#9CA3AF", marginTop: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>{s}</p>
              </button>
            );
          })}
        </div>

        {/* ── Filter + Search Bar ── */}
        <div className="rounded-xl p-4 space-y-3" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
          {/* Search row */}
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2" style={{ color: "#9CA3AF" }} />
              <input type="text" placeholder="Search by ticket ID, customer name, or phone number..."
                value={search} onChange={e => { setSearch(e.target.value); setPage(1); }}
                className="w-full rounded-lg pl-10 pr-4 py-2.5 text-sm outline-none transition-all"
                style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#111827" }}
                onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFFFFF"; e.target.style.boxShadow = "0 0 0 3px rgba(11,95,255,0.08)"; }}
                onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; e.target.style.boxShadow = "none"; }} />
              {search && (
                <button onClick={() => setSearch("")} className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "#9CA3AF" }}>
                  <X size={14} />
                </button>
              )}
            </div>
            {activeFilters > 0 && (
              <button
                onClick={() => { setStatus("All"); setPriority(PRIORITIES[0]); setBranch(ALL_BRANCHES); setOwner(ALL_OWNERS); setIssue(ALL_ISSUES); setSearch(""); }}
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-lg text-sm"
                style={{ background: "#FEF2F2", color: "#DC2626", border: "1px solid #FECACA", fontWeight: 600, whiteSpace: "nowrap" }}>
                <X size={13} /> Clear {activeFilters} filter{activeFilters > 1 ? "s" : ""}
              </button>
            )}
          </div>

          {/* Filters row */}
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 mr-1">
              <SlidersHorizontal size={14} style={{ color: "#9CA3AF" }} />
              <span style={{ fontSize: "0.78rem", color: "#9CA3AF", fontWeight: 500 }}>Filter:</span>
            </div>
            <DropFilter label="Status"     value={statusFilter}   options={["All",...STATUS_ORDER]} onChange={v => { setStatus(v); setPage(1); }} />
            <DropFilter label="Priority"   value={priorityFilter} options={PRIORITIES}              onChange={v => { setPriority(v); setPage(1); }} />
            <DropFilter label="Issue Type" value={issueFilter}    options={issueTypes}              onChange={v => { setIssue(v); setPage(1); }} />
            <DropFilter label="Branch"     value={branchFilter}   options={branches}                 onChange={v => { setBranch(v); setPage(1); }} />
            <DropFilter label="Owner"      value={ownerFilter}    options={owners} searchable        onChange={v => { setOwner(v); setPage(1); }} />
            <div className="flex-1" />
            <div className="flex items-center gap-2">
              <span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{total} result{total !== 1 ? "s" : ""}</span>
              <div className="w-px h-4" style={{ background: "#E5E7EB" }} />
              <select className="text-sm rounded-lg px-3 py-2 outline-none" style={{ border: "1px solid #E5E7EB", color: "#6B7280" }}>
                <option>Sort: Newest</option>
                <option>Sort: Due Date</option>
                <option>Sort: Priority</option>
                <option>Sort: SLA</option>
              </select>
            </div>
          </div>
        </div>

        {/* ── Bulk Actions ── */}
        {selectedRows.length > 0 && (
          <div className="rounded-xl px-5 py-3 flex items-center gap-4" style={{ background: "#EFF6FF", border: "1.5px solid #BFDBFE" }}>
            <span style={{ fontSize: "0.875rem", color: "#0B5FFF", fontWeight: 700 }}>{selectedRows.length} ticket{selectedRows.length > 1 ? "s" : ""} selected</span>
            <div className="h-4 w-px" style={{ background: "#BFDBFE" }} />
            {[
              { label: "Assign",   c: "#0B5FFF", bg: "#EFF6FF", b: "#BFDBFE" },
              { label: "Resolve",  c: "#16A34A", bg: "#DCFCE7", b: "#86EFAC" },
              { label: "Close",    c: "#374151", bg: "#F3F4F6", b: "#D1D5DB" },
              { label: "Escalate", c: "#DC2626", bg: "#FEE2E2", b: "#FCA5A5" },
            ].map(b => (
              <button key={b.label} className="px-3 py-1.5 rounded-lg text-xs" style={{ background: b.bg, color: b.c, border: `1px solid ${b.b}`, fontWeight: 600 }}>{b.label}</button>
            ))}
            <button onClick={() => setSelectedRows([])} className="ml-auto text-xs" style={{ color: "#6B7280" }}>Clear</button>
          </div>
        )}

        {/* ── Ticket Table ── */}
        <div className="rounded-xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
          <div className="overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "separate", borderSpacing: 0 }}>
              <thead>
                <tr style={{ background: "#F8FAFC" }}>
                  <th className="px-5 py-3.5 text-left" style={{ borderBottom: "1px solid #E8ECEF", width: "44px" }}>
                    <input type="checkbox" checked={selectedRows.length === paginated.length && paginated.length > 0} onChange={toggleAll} style={{ accentColor: "#0B5FFF", width: "15px", height: "15px" }} />
                  </th>
                  {[
                    { label: "Ticket ID",     w: "110px" },
                    { label: "Customer",      w: "170px" },
                    { label: "Issue Type",    w: "140px" },
                    { label: "Priority",      w: "110px" },
                    { label: "Owner",         w: "140px" },
                    { label: "Status",        w: "130px" },
                    { label: "Created Date",  w: "125px" },
                    { label: "Due Date",      w: "110px" },
                    { label: "SLA Status",    w: "110px" },
                    { label: "Actions",       w: "90px"  },
                  ].map(col => (
                    <th key={col.label} className="px-4 py-3.5 text-left" style={{ borderBottom: "1px solid #E8ECEF", minWidth: col.w }}>
                      <span style={{ fontSize: "0.67rem", fontWeight: 700, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.07em", whiteSpace: "nowrap" }}>
                        {col.label}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(loading || loadError) && (
                  <tr><td colSpan={11} className="px-5 py-16 text-center"><p style={{color: loadError ? "#DC2626" : "#667085", fontWeight:600}}>{loadError || "Loading tickets..."}</p>{loadError && <button onClick={() => void loadTickets()} className="mt-3 text-sm font-semibold text-blue-600">Try again</button>}</td></tr>
                )}
                {!loading && !loadError && paginated.length === 0 && (
                  <tr>
                    <td colSpan={11} className="px-5 py-16 text-center">
                      <div className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-3" style={{ background: "#F5F7FB" }}>
                        <Search size={22} style={{ color: "#D1D5DB" }} />
                      </div>
                      <p style={{ color: "#9CA3AF", fontWeight: 500 }}>No tickets match your filters</p>
                      <button onClick={() => { setStatus("All"); setPriority(PRIORITIES[0]); setBranch(ALL_BRANCHES); setOwner(ALL_OWNERS); setIssue(ALL_ISSUES); setSearch(""); }}
                        className="mt-3 text-sm" style={{ color: "#0B5FFF", fontWeight: 600 }}>
                        Clear all filters
                      </button>
                    </td>
                  </tr>
                )}
                {!loading && !loadError && paginated.map((t, i) => {
                  const isSelected = selectedRows.includes(t.id);
                  const isBreached = t.slaStatus === "Breached";
                  const chCfg = CHANNEL_CFG[t.channel] ?? { icon: Phone, color: "#9CA3AF" };
                  const ChIcon = chCfg.icon;

                  return (
                    <tr key={t.id}
                      onClick={() => setSelectedTicket(t)}
                      style={{
                        background: isSelected ? "#EFF6FF" : isBreached ? "#FFFBEB" : i % 2 === 0 ? "#FFFFFF" : "#FAFBFC",
                        borderBottom: "1px solid #F3F4F6",
                        cursor: "pointer",
                        transition: "background 0.1s",
                        borderLeft: isBreached ? "3px solid #DC2626" : "3px solid transparent",
                      }}
                      onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = "#F0F6FF"; }}
                      onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = isBreached ? "#FFFBEB" : i % 2 === 0 ? "#FFFFFF" : "#FAFBFC"; }}
                    >
                      {/* Checkbox */}
                      <td className="px-5 py-4" onClick={e => { e.stopPropagation(); toggleRow(t.id); }}>
                        <input type="checkbox" checked={isSelected} onChange={() => toggleRow(t.id)} style={{ accentColor: "#0B5FFF", width: "15px", height: "15px" }} />
                      </td>

                      {/* Ticket ID */}
                      <td className="px-4 py-4">
                        <div className="flex items-start gap-2">
                          {isBreached && <AlertTriangle size={12} style={{ color: "#DC2626", flexShrink: 0, marginTop: "2px" }} />}
                          <div>
                            <p style={{ fontSize: "0.8125rem", fontWeight: 800, color: "#0B5FFF", fontFamily: "monospace" }}>{t.id}</p>
                            <div className="flex items-center gap-1 mt-0.5">
                              <ChIcon size={10} style={{ color: chCfg.color }} />
                              <span style={{ fontSize: "0.65rem", color: "#9CA3AF" }}>{t.channel}</span>
                              {t.responses > 0 && (
                                <span style={{ fontSize: "0.65rem", color: "#9CA3AF" }}>· {t.responses} replies</span>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>

                      {/* Customer */}
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-2.5">
                          {t.imageUrl ? <img src={t.imageUrl} alt="" className="h-9 w-9 flex-shrink-0 rounded-full object-cover" /> : <div className="w-9 h-9 rounded-full flex items-center justify-center text-white flex-shrink-0" style={{ background: "#0B5FFF", fontSize: "0.62rem", fontWeight: 700 }}>{t.customer.split(" ").map(n => n[0]).slice(0, 2).join("")}</div>}
                          <div className="min-w-0">
                            <p style={{ fontSize: "0.8rem", fontWeight: 600, color: "#111827", whiteSpace: "nowrap" }}>{t.customer}</p>
                            <p style={{ fontSize: "0.68rem", color: "#667085" }}>{t.phone || "No phone"}</p>
                            <p style={{ fontSize: "0.65rem", color: "#9CA3AF" }}>{[t.location, t.customerBranch].filter(Boolean).join(" · ") || "Location not set"}</p>
                          </div>
                        </div>
                      </td>

                      {/* Issue Type */}
                      <td className="px-4 py-4">
                        <div>
                          <p style={{ fontSize: "0.8rem", color: "#374151", fontWeight: 500 }}>{t.issueType}</p>
                          <p className="mt-0.5" style={{ fontSize: "0.68rem", color: "#9CA3AF", maxWidth: "130px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.subject}</p><RecordAttribution record={t}/>
                        </div>
                      </td>

                      {/* Priority */}
                      <td className="px-4 py-4"><PriorityBadge priority={t.priority} /></td>

                      {/* Owner */}
                      <td className="px-4 py-4">
                        {t.owner === "Unassigned" ? (
                          <span className="px-2 py-1 rounded-full text-xs" style={{ background: "#FEF2F2", color: "#DC2626", fontWeight: 600 }}>Unassigned</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <div className="w-6 h-6 rounded-full flex items-center justify-center text-white flex-shrink-0"
                              style={{ background: "#8B5CF6", fontSize: "0.55rem", fontWeight: 700 }}>
                              {t.owner.split(" ").map(n => n[0]).slice(0, 2).join("")}
                            </div>
                            <span style={{ fontSize: "0.8rem", color: "#374151" }}>{t.owner.split(" ")[0]}</span>
                          </div>
                        )}
                      </td>

                      {/* Status */}
                      <td className="px-4 py-4"><StatusPill status={t.status} /></td>

                      {/* Created Date */}
                      <td className="px-4 py-4">
                        <p style={{ fontSize: "0.78rem", color: "#374151" }}>{t.created}</p>
                        <p style={{ fontSize: "0.68rem", color: "#9CA3AF", marginTop: "2px" }}>{t.createdTime}</p>
                      </td>

                      {/* Due Date */}
                      <td className="px-4 py-4">
                        <p style={{ fontSize: "0.78rem", color: isBreached ? "#DC2626" : "#374151", fontWeight: isBreached ? 700 : 400 }}>{t.dueDate}</p>
                      </td>

                      {/* SLA Status */}
                      <td className="px-4 py-4"><SlaBadge slaStatus={t.slaStatus} remaining={t.slaRemaining} /></td>

                      {/* Actions */}
                      <td className="px-4 py-4" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-0.5">
                          <button onClick={() => setSelectedTicket(t)}
                            className="p-2 rounded-lg transition-all" title="Open Ticket"
                            style={{ color: "#9CA3AF" }}
                            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#EFF6FF"; (e.currentTarget as HTMLElement).style.color = "#0B5FFF"; }}
                            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "#9CA3AF"; }}>
                            <ArrowUpRight size={15} />
                          </button>
                          <button className="p-2 rounded-lg transition-all" title="More"
                            style={{ color: "#9CA3AF" }}
                            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#F5F7FB"; (e.currentTarget as HTMLElement).style.color = "#374151"; }}
                            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "#9CA3AF"; }}>
                            <MoreHorizontal size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-5 py-4" style={{ borderTop: "1px solid #F3F4F6" }}>
            <span style={{ fontSize: "0.8rem", color: "#9CA3AF" }}>
              Showing <strong style={{ color: "#374151" }}>{total ? (page - 1) * rowsPerPage + 1 : 0}</strong>–<strong style={{ color: "#374151" }}>{Math.min(page * rowsPerPage, total)}</strong> of <strong style={{ color: "#374151" }}>{total}</strong> tickets
            </span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(1)} disabled={page === 1}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs"
                style={{ border: "1px solid #E5E7EB", color: page === 1 ? "#D1D5DB" : "#374151" }}>«</button>
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{ border: "1px solid #E5E7EB", color: page === 1 ? "#D1D5DB" : "#374151" }}>
                <ChevronLeft size={14} />
              </button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const p = Math.max(1, Math.min(page - 2, totalPages - 4)) + i;
                if (p < 1 || p > totalPages) return null;
                return (
                  <button key={p} onClick={() => setPage(p)}
                    className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                    style={{ background: p === page ? "#0B5FFF" : "transparent", color: p === page ? "#FFF" : "#374151", fontWeight: p === page ? 700 : 400, border: `1px solid ${p === page ? "#0B5FFF" : "#E5E7EB"}` }}>
                    {p}
                  </button>
                );
              })}
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages || totalPages === 0}
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{ border: "1px solid #E5E7EB", color: page === totalPages || totalPages === 0 ? "#D1D5DB" : "#374151" }}>
                <ChevronRight size={14} />
              </button>
              <button onClick={() => setPage(totalPages)} disabled={page === totalPages || totalPages === 0}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs"
                style={{ border: "1px solid #E5E7EB", color: page === totalPages || totalPages === 0 ? "#D1D5DB" : "#374151" }}>»</button>
            </div>
          </div>
        </div>

        <div className="h-4" />
      </div>

      {/* ── Create Ticket Modal ── */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.4)", backdropFilter: "blur(4px)" }}
          onClick={() => setShowCreate(false)}>
          <div className="rounded-2xl p-6 w-full max-w-lg" style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 800, color: "#111827" }}>Create New Ticket</h3>
                <p style={{ fontSize: "0.78rem", color: "#9CA3AF", marginTop: "2px" }}>Log a new customer issue or request</p>
              </div>
              <button onClick={() => setShowCreate(false)} style={{ color: "#9CA3AF" }}>
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Customer ID / Name</label>
                  <DirectorySelect kind="customers" value={customer} onChange={setCustomer} placeholder="Type customer name or phone..." />
                </div>
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Issue Type</label>
                  <select value={form.issue_type} onChange={e => setForm(v => ({...v, issue_type:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                    {["Payment","Security/Compliance","General Enquiry","Delivery","Product Fault","Collection/Agent"].map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Subject</label>
                <input value={form.subject} onChange={e => setForm(v => ({...v,subject:e.target.value}))} placeholder="Describe the issue briefly..." className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Description</label>
                <textarea value={form.description} onChange={e => setForm(v => ({...v,description:e.target.value}))} rows={4} placeholder="Provide full details of the customer issue..." className="w-full rounded-lg px-3 py-2.5 text-sm outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Priority</label>
                  <select value={form.priority} onChange={e => setForm(v => ({...v,priority:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                    {PRIORITIES.slice(1).map(p => <option key={p}>{p}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Assign To</label>
                  <DirectorySelect kind="agents" value={agent} onChange={setAgent} placeholder="Search all branch agents..." />
                </div>
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Channel</label>
                  <select value={form.channel} onChange={e => setForm(v => ({...v,channel:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                    {Object.keys(CHANNEL_CFG).map(c => <option key={c}>{c}</option>)}
                  </select>
                </div>
              </div>
              <div><label className="block mb-1.5 text-sm" style={{fontWeight:600,color:"#374151"}}>Resolution SLA (hours)</label><input type="number" min="1" max="720" value={form.sla_hours} onChange={e=>setForm(v=>({...v,sla_hours:e.target.value}))} className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm" /></div>
              {createError && <p className="text-sm text-red-600">{createError}</p>}
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowCreate(false)} className="flex-1 py-2.5 rounded-xl text-sm" style={{ border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}>Cancel</button>
                <button disabled={saving} onClick={() => void createTicket()} className="flex-1 py-2.5 rounded-xl text-sm flex items-center justify-center gap-2 disabled:opacity-60" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 700 }}>
                  <Zap size={14} /> {saving ? "Creating..." : "Create Ticket"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
