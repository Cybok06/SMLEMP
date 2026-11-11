import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { InteractionRecordFields, emptyInteraction, type InteractionRecord } from "./InteractionRecordFields";
import { useEffect, useState } from "react";
import {
  Phone, PhoneIncoming, PhoneOutgoing, PhoneMissed, PhoneCall,
  Search, Plus, Download, Filter, SlidersHorizontal, Clock,
  ChevronLeft, ChevronRight, Mic, Calendar,
  X, CheckCircle2, AlertCircle, TrendingUp,
  TrendingDown, User, RefreshCw, ChevronDown, AlertTriangle,
  CheckSquare, Circle, Users, BarChart2, Headphones,
} from "lucide-react";
import { CallDetails } from "./CallDetails";
import { useTheme } from "./ThemeContext";
import { DirectorySelect, type DirectoryOption } from "./DirectorySelect";

/* ─── Data ─────────────────────────────────────────────────────── */
export const ALL_CALLS = [
  { id: "CALL-8821", type: "Outbound", customer: "Mohd Izzat bin Ramlan",  customerId: "C-00001", phone: "+60 12-334 5678", officer: "Siti Rahimah",  officerInitials: "SR", department: "Customer Support", purpose: "Ticket TK-4821 follow-up — refrigerator complaint",  duration: "8m 23s",  durationSecs: 503, outcome: "Resolved",       followUp: false, followUpDate: "",             date: "21 Jun 2026", time: "11:30 AM", recorded: true,  linkedTicket: "TK-4821", linkedTask: "",        branch: "KL Central",    notes: "Customer acknowledged repair visit at 3pm. Confirmed technician name and arrival time. Customer satisfied with response." },
  { id: "CALL-8820", type: "Inbound",  customer: "Kavitha d/o Rajan",      customerId: "C-00007", phone: "+60 11-449 8834", officer: "Zainab Othman",  officerInitials: "ZO", department: "Customer Support", purpose: "Delivery rescheduled 3× — formal complaint lodged",    duration: "12m 04s", durationSecs: 724, outcome: "Escalated",      followUp: true,  followUpDate: "22 Jun 2026",  date: "21 Jun 2026", time: "10:52 AM", recorded: true,  linkedTicket: "TK-4817", linkedTask: "TSK-331", branch: "Subang Jaya",   notes: "Customer very upset. Delivery rescheduled 3 times. Escalated to branch manager. Follow-up call scheduled for tomorrow." },
  { id: "CALL-8819", type: "Missed",   customer: "Unknown Caller",          customerId: "—",       phone: "+60 17-221 0094", officer: "—",              officerInitials: "—",  department: "—",                purpose: "Inbound — no agent available during lunch",            duration: "—",       durationSecs: 0,   outcome: "Missed",          followUp: true,  followUpDate: "21 Jun 2026",  date: "21 Jun 2026", time: "10:14 AM", recorded: false, linkedTicket: "",        linkedTask: "",        branch: "KL Central",    notes: "Missed call during lunch period. Number not in system. Callback required." },
  { id: "CALL-8818", type: "Outbound", customer: "Ramli bin Hassan",        customerId: "C-00004", phone: "+60 19-002 8874", officer: "Rashid Halim",   officerInitials: "RH", department: "Collections",      purpose: "3rd overdue payment reminder — COL-1201 (Day 6)",     duration: "4m 11s",  durationSecs: 251, outcome: "Promise to Pay", followUp: true,  followUpDate: "23 Jun 2026",  date: "21 Jun 2026", time: "09:45 AM", recorded: true,  linkedTicket: "",        linkedTask: "TSK-333", branch: "Johor Bahru",   notes: "Customer promised payment by 23 Jun. Stated cash flow issue. Set reminder for follow-up verification." },
  { id: "CALL-8817", type: "Inbound",  customer: "Nor Azlina bt. Yusof",   customerId: "C-00005", phone: "+60 13-554 6620", officer: "Siti Rahimah",   officerInitials: "SR", department: "Customer Support", purpose: "Double billing complaint — instalment deducted twice", duration: "15m 30s", durationSecs: 930, outcome: "Pending",         followUp: true,  followUpDate: "22 Jun 2026",  date: "21 Jun 2026", time: "09:10 AM", recorded: true,  linkedTicket: "TK-4819", linkedTask: "",        branch: "KL Central",    notes: "Finance team investigating duplicate charge. Customer requested refund urgently. Ticket TK-4819 raised." },
  { id: "CALL-8816", type: "Outbound", customer: "Mohd Fadzli Noor",        customerId: "C-00008", phone: "+60 14-228 7741", officer: "Rashid Halim",   officerInitials: "RH", department: "Collections",      purpose: "Overdue payment COL-1202 — 2nd attempt",               duration: "2m 58s",  durationSecs: 178, outcome: "No Answer",       followUp: true,  followUpDate: "22 Jun 2026",  date: "20 Jun 2026", time: "04:30 PM", recorded: false, linkedTicket: "",        linkedTask: "",        branch: "KL Central",    notes: "No answer. Voicemail left. Third attempt scheduled for tomorrow afternoon." },
  { id: "CALL-8815", type: "Inbound",  customer: "Tan Bee Lian",            customerId: "C-00003", phone: "+60 16-774 2210", officer: "Ahmad Faizal",   officerInitials: "AF", department: "Delivery",         purpose: "Repair RPR-0097 status enquiry",                       duration: "6m 42s",  durationSecs: 402, outcome: "Resolved",       followUp: false, followUpDate: "",             date: "20 Jun 2026", time: "02:22 PM", recorded: true,  linkedTicket: "",        linkedTask: "",        branch: "Penang",        notes: "Informed customer repair completed and unit ready for collection. Customer satisfied." },
  { id: "CALL-8814", type: "Outbound", customer: "Priya Pillai",            customerId: "C-00011", phone: "+60 11-449 8834", officer: "Lee Chun Wei",   officerInitials: "LC", department: "Repairs",          purpose: "Parts arrival confirmation for RPR-0098",              duration: "3m 17s",  durationSecs: 197, outcome: "Confirmed",      followUp: false, followUpDate: "",             date: "20 Jun 2026", time: "11:05 AM", recorded: true,  linkedTicket: "",        linkedTask: "TSK-335", branch: "KL Central",    notes: "Customer confirmed availability for installation on 23 Jun. Loan unit accepted." },
  { id: "CALL-8813", type: "Inbound",  customer: "Lim Siew Lan",            customerId: "C-00009", phone: "+60 16-334 5501", officer: "Zainab Othman",  officerInitials: "ZO", department: "Customer Support", purpose: "Washing machine loud noise — repair request",          duration: "9m 51s",  durationSecs: 591, outcome: "Ticket Created", followUp: false, followUpDate: "",             date: "19 Jun 2026", time: "03:15 PM", recorded: true,  linkedTicket: "TK-4815", linkedTask: "",        branch: "Subang Jaya",   notes: "Repair ticket TK-4815 created. Technician scheduled." },
  { id: "CALL-8812", type: "Outbound", customer: "Chong Wei Keong",         customerId: "C-00006", phone: "+60 17-663 9910", officer: "Ahmad Faizal",   officerInitials: "AF", department: "Delivery",         purpose: "Reschedule failed delivery DLV-2091",                  duration: "5m 08s",  durationSecs: 308, outcome: "Rescheduled",    followUp: true,  followUpDate: "22 Jun 2026",  date: "19 Jun 2026", time: "11:30 AM", recorded: false, linkedTicket: "TK-4813", linkedTask: "TSK-331", branch: "Ipoh",          notes: "Customer confirmed availability 22 Jun morning. Delivery slot confirmed 9-11 AM." },
  { id: "CALL-8811", type: "Inbound",  customer: "Amirah bt. Kamarudin",    customerId: "C-00013", phone: "+60 12-556 8831", officer: "Siti Rahimah",   officerInitials: "SR", department: "Customer Support", purpose: "Formal complaint — delivery officer misconduct",       duration: "18m 22s", durationSecs: 1102,outcome: "Escalated",      followUp: true,  followUpDate: "22 Jun 2026",  date: "13 Jun 2026", time: "03:20 PM", recorded: true,  linkedTicket: "TK-4809", linkedTask: "",        branch: "KL Central",    notes: "Complaint against delivery officer. HR notified. Formal investigation opened." },
  { id: "CALL-8810", type: "Outbound", customer: "Hafizuddin Aziz",         customerId: "C-00010", phone: "+60 18-776 2298", officer: "Siti Rahimah",   officerInitials: "SR", department: "Customer Support", purpose: "Post-resolution satisfaction check TK-4814",           duration: "2m 44s",  durationSecs: 164, outcome: "Satisfied",      followUp: false, followUpDate: "",             date: "18 Jun 2026", time: "10:00 AM", recorded: false, linkedTicket: "TK-4814", linkedTask: "",        branch: "Petaling Jaya", notes: "Customer confirmed login issue resolved. CSAT 5★." },
  { id: "CALL-8809", type: "Missed",   customer: "David Ong Chee Keong",    customerId: "C-00014", phone: "+60 16-221 4450", officer: "—",              officerInitials: "—",  department: "—",                purpose: "Inbound — queue timeout",                              duration: "—",       durationSecs: 0,   outcome: "Missed",          followUp: true,  followUpDate: "19 Jun 2026",  date: "18 Jun 2026", time: "09:05 AM", recorded: false, linkedTicket: "",        linkedTask: "",        branch: "Penang",        notes: "Queue timeout. Customer in database. Callback needed." },
  { id: "CALL-8808", type: "Inbound",  customer: "Zulkifli Mahmud",         customerId: "C-00012", phone: "+60 13-887 4421", officer: "Lee Chun Wei",   officerInitials: "LC", department: "Repairs",          purpose: "Smart TV screen flicker after firmware — TK-4812",    duration: "7m 33s",  durationSecs: 453, outcome: "In Progress",    followUp: true,  followUpDate: "22 Jun 2026",  date: "17 Jun 2026", time: "05:10 PM", recorded: true,  linkedTicket: "TK-4812", linkedTask: "",        branch: "Johor Bahru",   notes: "Firmware rollback being tested remotely. Customer will call back if issue persists." },
  { id: "CALL-8807", type: "Outbound", customer: "Salmah binti Daud",       customerId: "C-00015", phone: "+60 19-334 0092", officer: "Rashid Halim",   officerInitials: "RH", department: "Collections",      purpose: "New account — instalment plan welcome call",           duration: "6m 55s",  durationSecs: 415, outcome: "Confirmed",      followUp: false, followUpDate: "",             date: "17 Jun 2026", time: "02:00 PM", recorded: true,  linkedTicket: "",        linkedTask: "",        branch: "Ipoh",          notes: "Welcome call completed. Payment schedule explained. Auto-debit consent obtained." },
];

const CALL_TYPES   = ["All Types", "Inbound", "Outbound", "Missed"];
const OUTCOMES_ALL = ["All Outcomes", "Resolved", "Escalated", "Pending", "No Answer", "Missed", "Promise to Pay", "Confirmed", "Rescheduled", "Ticket Created", "In Progress", "Satisfied"];
const OFFICERS     = ["All Officers", "Siti Rahimah", "Zainab Othman", "Ahmad Faizal", "Lee Chun Wei", "Rashid Halim"];
const DEPARTMENTS  = ["All Departments", "Customer Support", "Collections", "Delivery", "Repairs"];
const FOLLOWUP     = ["All", "Follow-up Required", "No Follow-up"];

export const TYPE_CFG: Record<string, { text: string; bg: string; border: string; icon: React.ElementType; dot: string }> = {
  Inbound:  { text: "#15803D", bg: "#DCFCE7", border: "#86EFAC", icon: PhoneIncoming,  dot: "#16A34A" },
  Outbound: { text: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD", icon: PhoneOutgoing,  dot: "#2563EB" },
  Missed:   { text: "#991B1B", bg: "#FEE2E2", border: "#FCA5A5", icon: PhoneMissed,    dot: "#DC2626" },
};

export const OUTCOME_CFG: Record<string, { text: string; bg: string }> = {
  Resolved:       { text: "#15803D", bg: "#DCFCE7" },
  Escalated:      { text: "#991B1B", bg: "#FEE2E2" },
  Pending:        { text: "#B45309", bg: "#FEF3C7" },
  "No Answer":    { text: "#6B7280", bg: "#F3F4F6" },
  Missed:         { text: "#991B1B", bg: "#FEE2E2" },
  "Promise to Pay":{ text: "#D97706", bg: "#FEF3C7" },
  Confirmed:      { text: "#15803D", bg: "#DCFCE7" },
  Rescheduled:    { text: "#7C3AED", bg: "#EDE9FE" },
  "Ticket Created":{ text: "#1D4ED8", bg: "#DBEAFE" },
  "In Progress":  { text: "#C2410C", bg: "#FFEDD5" },
  Satisfied:      { text: "#15803D", bg: "#DCFCE7" },
};

/* ─── Dropdown Filter ──────────────────────────────────────────── */
function DropFilter({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const active = value !== options[0];
  return (
    <div className="relative">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all"
        style={{ background: active ? "#EFF6FF" : "#FFFFFF", border: `1.5px solid ${active ? "#0B5FFF" : "#E5E7EB"}`, color: active ? "#0B5FFF" : "#374151", fontWeight: active ? 600 : 400, whiteSpace: "nowrap" }}>
        <span style={{ maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis" }}>{active ? value : label}</span>
        {active ? <X size={12} onClick={e => { e.stopPropagation(); onChange(options[0]); }} /> : <ChevronDown size={13} style={{ color: "#9CA3AF" }} />}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute top-10 left-0 rounded-xl z-50 py-1 overflow-hidden" style={{ minWidth: "185px", background: "#FFFFFF", boxShadow: "0 12px 36px rgba(0,0,0,0.12)", border: "1px solid #E5E7EB" }}>
            {options.map(opt => (
              <button key={opt} onClick={() => { onChange(opt); setOpen(false); }}
                className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-left"
                style={{ background: value === opt ? "#EFF6FF" : "transparent", color: value === opt ? "#0B5FFF" : "#374151", fontWeight: value === opt ? 600 : 400 }}
                onMouseEnter={e => { if (value !== opt) (e.currentTarget as HTMLElement).style.background = "#F9FAFB"; }}
                onMouseLeave={e => { if (value !== opt) (e.currentTarget as HTMLElement).style.background = "transparent"; }}>
                <span>{opt}</span>
                {value === opt && <span style={{ color: "#0B5FFF", fontSize: "0.7rem" }}>✓</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ─── Log Call Modal ─────────────────────────────────────────────── */
type CallRecord = (typeof ALL_CALLS)[number] & SupportAttribution & { interaction?: InteractionRecord; _id?: string; callbackStatus?: string; source?: string; enrichmentStatus?: string; customerMatch?: string; deviceName?: string; fromNumber?: string };
type TicketOption = { id: string; subject: string; status: string };

function LogCallModal({ type, onClose, initialCall, onSaved }: { type: "Inbound" | "Outbound" | "Missed"; onClose: () => void; initialCall?: CallRecord | null; onSaved?: (call: CallRecord) => void }) {
  const [step, setStep] = useState(1);
  const [interaction, setInteraction] = useState<InteractionRecord>({...emptyInteraction, branch:initialCall?.branch||"", ...initialCall?.interaction});
  const [customer, setCustomer] = useState<DirectoryOption | null>(initialCall?.customerId ? { id: initialCall.customerId, name: initialCall.customer, phone: initialCall.phone } : null);
  const [purpose, setPurpose] = useState(initialCall?.purpose || "");
  const [outcome, setOutcome] = useState(initialCall?.outcome || (type === "Missed" ? "Missed" : "Resolved"));
  const [notes, setNotes] = useState(initialCall?.notes || "");
  const [department, setDepartment] = useState(initialCall?.department || "Customer Support");
  const [duration, setDuration] = useState(initialCall?.duration || "");
  const [linkedTicket, setLinkedTicket] = useState(initialCall?.linkedTicket || "");
  const [ticketOptions, setTicketOptions] = useState<TicketOption[]>([]);
  const [ticketsLoading, setTicketsLoading] = useState(false);
  const [followUp, setFollowUp] = useState(initialCall?.followUp || false);
  const [followUpDate, setFollowUpDate] = useState(initialCall?.followUpDate || "");
  const [followUpAgent, setFollowUpAgent] = useState<DirectoryOption | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const cfg = TYPE_CFG[type];
  const Icon = cfg.icon;

  useEffect(() => {
    const customerId = customer?.id;
    if (!customerId) { setTicketOptions([]); if (!initialCall) setLinkedTicket(""); return; }
    const controller = new AbortController();
    setTicketsLoading(true);
    fetch(`/api/customer-support/customers/${encodeURIComponent(customerId)}/tickets`, { credentials: "same-origin", signal: controller.signal })
      .then(response => response.json().then(data => ({ response, data })))
      .then(({ response, data }) => { if (response.ok && data.ok) setTicketOptions(data.tickets || []); else setTicketOptions([]); })
      .catch(error => { if (!(error instanceof DOMException && error.name === "AbortError")) setTicketOptions([]); })
      .finally(() => { if (!controller.signal.aborted) setTicketsLoading(false); });
    return () => controller.abort();
  }, [customer?.id]);

  const selectCustomer = (next: DirectoryOption | null) => {
    if (next?.id !== customer?.id) setLinkedTicket("");
    setCustomer(next);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.4)", backdropFilter: "blur(6px)" }}
      onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Call log" className="rounded-2xl max-h-[90vh] overflow-y-auto w-full max-w-lg" style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>

        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-5" style={{ background: type === "Inbound" ? "linear-gradient(135deg,#F0FDF4,#DCFCE7)" : "linear-gradient(135deg,#EFF6FF,#DBEAFE)", borderBottom: "1px solid #F3F4F6" }}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: cfg.bg, border: `1.5px solid ${cfg.border}` }}>
              <Icon size={18} style={{ color: cfg.text }} />
            </div>
            <div>
              <h3 style={{ fontSize: "1rem", fontWeight: 800, color: "#111827" }}>{initialCall ? `Update ${initialCall.id}` : `Log ${type} Call`}</h3>
              <p style={{ fontSize: "0.75rem", color: "#9CA3AF", marginTop: "1px" }}>Step {step} of 2 — {step === 1 ? "Call Details" : "Outcome & Follow-up"}</p>
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ color: "#9CA3AF" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
            <X size={18} />
          </button>
        </div>

        {/* Step indicators */}
        <div className="flex items-center gap-3 px-6 py-3" style={{ borderBottom: "1px solid #F5F7FB" }}>
          {[1, 2].map(s => (
            <div key={s} className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold" style={{ background: step >= s ? cfg.bg : "#F3F4F6", color: step >= s ? cfg.text : "#9CA3AF", border: `1.5px solid ${step >= s ? cfg.border : "#E5E7EB"}` }}>{s}</div>
              <span style={{ fontSize: "0.75rem", fontWeight: step === s ? 700 : 400, color: step === s ? "#111827" : "#9CA3AF" }}>{s === 1 ? "Call Info" : "Outcome"}</span>
              {s < 2 && <ChevronRight size={13} style={{ color: "#D1D5DB" }} />}
            </div>
          ))}
        </div>

        <div className="px-6 py-5">
          {step === 1 && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Customer ID / Name *</label>
                  <DirectorySelect kind="customers" value={customer} onChange={selectCustomer} placeholder="Type customer name or phone..." />
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Phone Number *</label>
                  <input readOnly value={customer?.phone || initialCall?.phone || ""} placeholder="Filled from customer" className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Call Type</label>
                  <div className="rounded-xl px-3.5 py-2.5 text-sm" style={{ border: "1.5px solid #E5E7EB", background: cfg.bg, color: cfg.text, fontWeight: 600 }}>
                    {type}
                  </div>
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Department</label>
                  <select value={department} onChange={e => setDepartment(e.target.value)} className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                    {DEPARTMENTS.slice(1).map(d => <option key={d}>{d}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Call Purpose / Subject *</label>
                <input value={purpose} onChange={e => setPurpose(e.target.value)} placeholder="Brief description of call reason..." className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Date</label>
                  <input type="text" readOnly value={initialCall?.date || new Date().toLocaleDateString("en-GB", {day:"2-digit",month:"short",year:"numeric"})} className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}
                    onFocus={e => e.target.style.borderColor = "#0B5FFF"} onBlur={e => e.target.style.borderColor = "#E5E7EB"} />
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Time</label>
                  <input type="text" readOnly value={initialCall?.time || new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})} className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}
                    onFocus={e => e.target.style.borderColor = "#0B5FFF"} onBlur={e => e.target.style.borderColor = "#E5E7EB"} />
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Duration</label>
                  <input value={duration} readOnly={!!initialCall} onChange={e => setDuration(e.target.value)} placeholder="e.g. 5m 30s" className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                    onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Linked Ticket (optional)</label>
                <select value={linkedTicket} onChange={e => setLinkedTicket(e.target.value)} disabled={!customer || ticketsLoading} className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-60" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}>
                  <option value="">{!customer ? "Select a customer first" : ticketsLoading ? "Loading customer tickets..." : ticketOptions.length ? "No linked ticket" : "No tickets found for this customer"}</option>
                  {ticketOptions.map(ticket => <option key={ticket.id} value={ticket.id}>{ticket.id} — {ticket.subject}</option>)}
                </select>
                {customer && !ticketsLoading && ticketOptions.length > 0 && <p className="mt-1.5 text-xs text-gray-400">Only tickets linked to {customer.name} are shown.</p>}
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div>
                <label className="block mb-2" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Call Outcome *</label>
                <div className="grid grid-cols-3 gap-2">
                  {["Resolved", "Escalated", "Pending", "No Answer", "Promise to Pay", "Rescheduled"].map(o => {
                    const oc = OUTCOME_CFG[o] ?? { text: "#6B7280", bg: "#F3F4F6" };
                    return (
                      <button type="button" onClick={() => setOutcome(o)} key={o} className="px-3 py-2.5 rounded-xl text-xs transition-all" style={{ background: o === outcome ? oc.bg : "#F9FAFB", color: o === outcome ? oc.text : "#6B7280", border: `1.5px solid ${o === outcome ? oc.text : "#E5E7EB"}`, fontWeight: o === outcome ? 700 : 500 }}>
                        {o}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Call Notes / Summary</label>
                <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={4} placeholder="Summarise key points discussed, decisions made, and next steps..." className="w-full rounded-xl px-3.5 py-3 text-sm outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151", lineHeight: 1.65 }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              </div>
              <div className="rounded-xl p-4" style={{ background: "#F8FAFC", border: "1px solid #E8ECEF" }}>
                <div className="flex items-center justify-between mb-3">
                  <label style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Follow-up Required?</label>
                  <div className="flex gap-2">
                    {["Yes", "No"].map(v => (
                      <button type="button" onClick={() => { const required=v === "Yes"; setFollowUp(required); if(!required){setFollowUpAgent(null);setFollowUpDate("");} }} key={v} className="px-4 py-1.5 rounded-lg text-sm" style={{ background: (v === "Yes") === followUp ? "#EFF6FF" : "#F9FAFB", color: (v === "Yes") === followUp ? "#0B5FFF" : "#6B7280", border: `1.5px solid ${(v === "Yes") === followUp ? "#0B5FFF" : "#E5E7EB"}`, fontWeight: (v === "Yes") === followUp ? 700 : 400 }}>
                        {v}
                      </button>
                    ))}
                  </div>
                </div>
                {followUp && <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block mb-1.5" style={{ fontSize: "0.72rem", fontWeight: 600, color: "#9CA3AF" }}>Follow-up Date</label>
                    <input type="date" min={new Date().toISOString().slice(0,10)} value={followUpDate} onChange={e => setFollowUpDate(e.target.value)} className="w-full rounded-xl px-3 py-2 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}
                      onFocus={e => e.target.style.borderColor = "#0B5FFF"} onBlur={e => e.target.style.borderColor = "#E5E7EB"} />
                  </div>
                  <div>
                    <label className="block mb-1.5" style={{ fontSize: "0.72rem", fontWeight: 600, color: "#9CA3AF" }}>Assigned To</label>
                    <DirectorySelect kind="agents" value={followUpAgent} onChange={setFollowUpAgent} placeholder="Search agent or branch..." />
                  </div>
                </div>}
              </div>
            </div>
          )}

          {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
          {step === 2 && <div className="px-6 pb-4 max-h-64 overflow-y-auto"><InteractionRecordFields value={interaction} onChange={setInteraction}/></div>}
          <div className="flex gap-3 mt-6">
            {step === 2 && (
              <button onClick={() => setStep(1)} className="px-5 py-2.5 rounded-xl text-sm" style={{ border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}>← Back</button>
            )}
            <button disabled={saving} onClick={async () => { if (step === 1) { if ((!initialCall && !customer) || !purpose.trim()) { setError(`${initialCall ? "Enter" : "Select a customer and enter"} the call purpose.`); return; } setError(""); setStep(2); return; } if(followUp&&(!followUpDate)){setError("Select a follow-up date.");return;} if(!initialCall&&followUp&&!followUpAgent){setError("Select a follow-up agent.");return;} if(!interaction.branch.trim()||!interaction.feedback.trim()||!interaction.action_taken.trim()){setError("Enter branch, customer feedback and action taken.");return;} setSaving(true); try { const endpoint=initialCall?`/api/customer-support/calls/${encodeURIComponent(initialCall._id || initialCall.id)}`:"/api/customer-support/calls"; const response = await fetch(endpoint, { method:initialCall?"PATCH":"POST", credentials:"same-origin", headers:{"Content-Type":"application/json"}, body:JSON.stringify({...interaction,interaction_record:true,channel:"Call",customer_id:customer?.id||"",type,purpose,outcome,notes,department,duration,linked_ticket:linkedTicket,follow_up:followUp,follow_up_date:followUpDate,follow_up_agent_id:followUpAgent?.id}) }); const data=await response.json(); if(!response.ok||!data.ok) throw new Error(data.message||"Unable to save call."); onSaved?.(data.call); onClose(); } catch(e) { setError(e instanceof Error ? e.message : "Unable to save call."); } finally { setSaving(false); } }}
              className="flex-1 py-2.5 rounded-xl text-sm flex items-center justify-center gap-2 transition-all"
              style={{ background: cfg.bg.includes("EFF") ? "#0B5FFF" : "#16A34A", color: "#FFF", fontWeight: 700 }}>
              {step === 1 ? <><span>Continue</span> <ChevronRight size={14} /></> : <><CheckCircle2 size={14} /> {saving ? "Saving..." : "Save Call Log"}</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Main Component ─────────────────────────────────────────────── */
export function CallsPage() {
  const { t } = useTheme();
  const [search, setSearch]             = useState("");
  useEffect(() => { const apply = (event?: Event) => { let payload: any = event ? (event as CustomEvent).detail : null; if (!payload) { try { payload = JSON.parse(window.sessionStorage.getItem("customer-support-global-search") || "null"); } catch { payload = null; } } if (payload?.page === "calls") { setSearch(payload.query || ""); setPage(1); window.sessionStorage.removeItem("customer-support-global-search"); } }; apply(); window.addEventListener("customer-support-global-search", apply); return () => window.removeEventListener("customer-support-global-search", apply); }, []);
  const [typeFilter, setType]           = useState(CALL_TYPES[0]);
  const [outcomeFilter, setOutcome]     = useState(OUTCOMES_ALL[0]);
  const [officerFilter, setOfficer]     = useState(OFFICERS[0]);
  const [deptFilter, setDept]           = useState(DEPARTMENTS[0]);
  const [followUpFilter, setFollowUp]   = useState(FOLLOWUP[0]);
  const [page, setPage]                 = useState(1);
  const [selectedCall, setSelectedCall] = useState<CallRecord | null>(null);
  const [editingCall, setEditingCall] = useState<CallRecord | null>(null);
  const [logModal, setLogModal]         = useState<"Inbound" | "Outbound" | null>(null);
  const [selectedRows, setSelectedRows] = useState<string[]>([]);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [summaryFilter, setSummaryFilter] = useState<"all" | "today" | "inbound" | "outbound" | "missed" | "callbacks">("all");
  const loadCalls = async () => { setLoading(true); try { const response = await fetch("/api/customer-support/calls", {credentials:"same-origin"}); const data=await response.json(); if(response.ok&&data.ok) setCalls(data.calls??[]); } finally { setLoading(false); } };
  useEffect(() => { void loadCalls(); }, [logModal]);
  const rowsPerPage = 10;

  if (selectedCall) return <CallDetails call={selectedCall} onBack={() => setSelectedCall(null)} />;

  const filtered = calls.filter(c => {
    const q = search.toLowerCase();
    const matchSearch   = !search || c.id.toLowerCase().includes(q) || c.customer.toLowerCase().includes(q) || c.phone.includes(q) || c.purpose.toLowerCase().includes(q);
    const matchType     = typeFilter === CALL_TYPES[0] || c.type === typeFilter;
    const matchOutcome  = outcomeFilter === OUTCOMES_ALL[0] || c.outcome === outcomeFilter;
    const matchOfficer  = officerFilter === OFFICERS[0] || c.officer === officerFilter;
    const matchDept     = deptFilter === DEPARTMENTS[0] || c.department === deptFilter;
    const matchFollowUp = followUpFilter === FOLLOWUP[0] || (followUpFilter === "Follow-up Required" ? c.followUp : !c.followUp);
    const todayLabel = new Date().toLocaleDateString("en-GB", {day:"2-digit",month:"short",year:"numeric"});
    const matchSummary = summaryFilter === "all" || (summaryFilter === "today" && c.date === todayLabel) ||
      (summaryFilter === "inbound" && c.date === todayLabel && c.type === "Inbound") ||
      (summaryFilter === "outbound" && c.date === todayLabel && c.type === "Outbound") ||
      (summaryFilter === "missed" && c.type === "Missed" && c.callbackStatus !== "Called Back") ||
      (summaryFilter === "callbacks" && c.followUp && c.followUpDate === todayLabel);
    return matchSearch && matchType && matchOutcome && matchOfficer && matchDept && matchFollowUp && matchSummary;
  });

  const totalPages    = Math.ceil(filtered.length / rowsPerPage);
  const paginated     = filtered.slice((page - 1) * rowsPerPage, page * rowsPerPage);
  const toggleRow     = (id: string) => setSelectedRows(prev => prev.includes(id) ? prev.filter(r => r !== id) : [...prev, id]);
  const toggleAll     = () => setSelectedRows(selectedRows.length === paginated.length ? [] : paginated.map(c => c.id));

  const activeFilters = [typeFilter, outcomeFilter, officerFilter, deptFilter, followUpFilter]
    .filter(f => f !== CALL_TYPES[0] && f !== OUTCOMES_ALL[0] && f !== OFFICERS[0] && f !== DEPARTMENTS[0] && f !== FOLLOWUP[0]).length;

  // KPI calculations
  const todayLabel = new Date().toLocaleDateString("en-GB", {day:"2-digit",month:"short",year:"numeric"});
  const today         = calls.filter(c => c.date === todayLabel);
  const callsToday    = today.length;
  const callbacksDue  = calls.filter(c => c.followUp && c.followUpDate === todayLabel).length;
  const totalSecs     = calls.filter(c => c.durationSecs > 0).reduce((s, c) => s + c.durationSecs, 0);
  const avgSecs       = Math.round(totalSecs / Math.max(calls.filter(c => c.durationSecs > 0).length, 1));
  const avgDuration   = `${Math.floor(avgSecs / 60)}m ${avgSecs % 60}s`;
  const missedToday   = today.filter(c => c.type === "Missed").length;
  const resolvedToday = today.filter(c => c.outcome === "Resolved").length;
  const followUpsTotal= calls.filter(c => c.followUp).length;
  const exportCalls = () => {
    const quote=(value:unknown)=>`"${String(value??"").replaceAll('"','""')}"`;
    const rows=[["Call ID","Type","Customer","Phone","Officer","Department","Purpose","Outcome","Follow-up","Follow-up Date","Date","Time"],...filtered.map(call=>[call.id,call.type,call.customer,call.phone,call.officer,call.department,call.purpose,call.outcome,call.followUp?"Yes":"No",call.followUpDate,call.date,call.time])];
    const blob=new Blob([rows.map(row=>row.map(quote).join(",")).join("\r\n")],{type:"text/csv;charset=utf-8"}); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=`calls-${new Date().toISOString().slice(0,10)}.csv`; link.click(); URL.revokeObjectURL(url);
  };
  const markCalledBack = async (call: (typeof calls)[number]) => {
    const response = await fetch(`/api/customer-support/calls/${encodeURIComponent(call._id || call.id)}/called-back`, { method: "PATCH", credentials: "same-origin" });
    const data = await response.json();
    if (!response.ok || !data.ok) return;
    setCalls(current => current.map(item => item.id === call.id ? data.call : item));
    window.dispatchEvent(new Event("customer-support-missed-calls-changed"));
  };

  return (
    <div className="flex flex-col" style={{ background: t.pageBg, minHeight: "100%" }}>
      <div className="p-6 space-y-5">

        {/* ── Page Header ── */}
        <div className="flex items-start justify-between">
          <div>
            <h2 style={{ fontSize: "1.3rem", fontWeight: 800, color: "#111827", lineHeight: 1 }}>Call Management</h2>
            <p style={{ fontSize: "0.78rem", color: "#9CA3AF", marginTop: "5px" }}>
              {filtered.length} of {calls.length} calls · {followUpsTotal} follow-ups pending
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <button onClick={exportCalls} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm" style={{ background: "#FFFFFF", border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F9FAFB"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#FFFFFF"}>
              <Download size={14} style={{ color: "#6B7280" }} /> Export
            </button>
            <button onClick={() => void loadCalls()} disabled={loading} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm disabled:opacity-50" style={{ background: "#FFFFFF", border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F9FAFB"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#FFFFFF"}>
              <RefreshCw size={14} style={{ color: "#6B7280" }} />
            </button>
            <button onClick={() => setLogModal("Inbound")}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#DCFCE7", color: "#15803D", border: "1.5px solid #86EFAC", fontWeight: 700 }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#BBF7D0"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#DCFCE7"}>
              <PhoneIncoming size={15} /> Log Incoming Call
            </button>
            <button onClick={() => setLogModal("Outbound")}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#0B5FFF", color: "#FFFFFF", fontWeight: 700, boxShadow: "0 2px 10px rgba(11,95,255,0.3)" }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#0040CC"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#0B5FFF"}>
              <PhoneOutgoing size={15} /> Log Outgoing Call
            </button>
          </div>
        </div>

        {/* ── KPI Cards ── */}
        <div className="grid grid-cols-6 gap-3">
          {[
            { key: "today", label: "Calls Today",     value: callsToday.toString(),  icon: PhoneCall,    color: "#0B5FFF", bg: "#EFF6FF",  border: "#BFDBFE", sub: `${today.filter(c => c.type === "Inbound").length} in · ${today.filter(c => c.type === "Outbound").length} out` },
            { key: "inbound", label: "Inbound Today", value: today.filter(c => c.type === "Inbound").length.toString(), icon: PhoneIncoming, color: "#15803D", bg: "#DCFCE7", border: "#86EFAC", sub: "received today" },
            { key: "outbound", label: "Outbound Today", value: today.filter(c => c.type === "Outbound").length.toString(),icon: PhoneOutgoing, color: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD", sub: "made today" },
            { key: "missed", label: "Missed Calls", value: calls.filter(c => c.type === "Missed" && c.callbackStatus !== "Called Back").length.toString(), icon: PhoneMissed, color: "#991B1B", bg: "#FEE2E2", border: "#FCA5A5", sub: "need callback" },
            { key: "callbacks", label: "Callbacks Due", value: callbacksDue.toString(),icon: PhoneCall, color: "#B45309", bg: "#FEF3C7", border: "#FCD34D", sub: "due today" },
            { key: "all", label: "Avg Duration", value: avgDuration, icon: Clock, color: "#7C3AED", bg: "#EDE9FE", border: "#C4B5FD", sub: "show all calls" },
          ].map(k => (
            <button key={k.label} onClick={() => { setSummaryFilter(k.key as typeof summaryFilter); setPage(1); }} className="rounded-2xl p-4 flex flex-col gap-2.5 cursor-pointer text-left transition-all"
              style={{ background: summaryFilter === k.key ? k.bg : "#FFFFFF", border: `1px solid ${summaryFilter === k.key ? k.border : "#E8ECEF"}`, boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 16px rgba(0,0,0,0.08)"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.boxShadow = "0 1px 4px rgba(0,0,0,0.04)"}>
              <div className="flex items-center justify-between">
                <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: k.bg, border: `1px solid ${k.border}` }}>
                  <k.icon size={16} style={{ color: k.color }} />
                </div>
              </div>
              <div>
                <p style={{ fontSize: "1.5rem", fontWeight: 800, color: "#111827", lineHeight: 1 }}>{k.value}</p>
                <p style={{ fontSize: "0.67rem", fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.05em", marginTop: "4px" }}>{k.label}</p>
                <p style={{ fontSize: "0.67rem", color: "#C4C4C4", marginTop: "2px" }}>{k.sub}</p>
              </div>
            </button>
          ))}
        </div>

        {/* ── Secondary Stats Row ── */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: "Resolved Today",    value: `${resolvedToday} / ${callsToday}`, pct: callsToday > 0 ? Math.round((resolvedToday/callsToday)*100) : 0, color: "#15803D", bg: "#DCFCE7", barColor: "#16A34A" },
            { label: "Follow-ups Pending",value: `${followUpsTotal} calls`,           pct: Math.round((followUpsTotal/Math.max(calls.length,1))*100), color: "#D97706", bg: "#FEF3C7", barColor: "#F59E0B" },
            { label: "Recorded Calls",    value: `${calls.filter(c => c.recorded).length} / ${calls.length}`, pct: Math.round((calls.filter(c=>c.recorded).length/Math.max(calls.length,1))*100), color: "#1D4ED8", bg: "#DBEAFE", barColor: "#2563EB" },
            { label: "Escalated Calls",   value: `${calls.filter(c => c.outcome === "Escalated").length} calls`,  pct: Math.round((calls.filter(c=>c.outcome==="Escalated").length/Math.max(calls.length,1))*100), color: "#991B1B", bg: "#FEE2E2", barColor: "#DC2626" },
          ].map(s => (
            <div key={s.label} className="rounded-2xl px-5 py-4" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
              <div className="flex items-center justify-between mb-2">
                <span style={{ fontSize: "0.72rem", fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>{s.label}</span>
                <span className="px-2 py-0.5 rounded-full text-xs" style={{ background: s.bg, color: s.color, fontWeight: 700 }}>{s.pct}%</span>
              </div>
              <p style={{ fontSize: "1.1rem", fontWeight: 800, color: "#111827", marginBottom: "8px" }}>{s.value}</p>
              <div className="h-1.5 rounded-full" style={{ background: "#F3F4F6" }}>
                <div className="h-full rounded-full transition-all" style={{ width: `${s.pct}%`, background: s.barColor }} />
              </div>
            </div>
          ))}
        </div>

        {/* ── Filter + Search ── */}
        <div className="rounded-2xl p-4 space-y-3" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2" style={{ color: "#9CA3AF" }} />
              <input type="text" placeholder="Search by call ID, customer name, phone number, or purpose..."
                value={search} onChange={e => { setSearch(e.target.value); setPage(1); }}
                className="w-full rounded-xl pl-10 pr-4 py-2.5 text-sm outline-none transition-all"
                style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#111827" }}
                onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFFFFF"; e.target.style.boxShadow = "0 0 0 3px rgba(11,95,255,0.08)"; }}
                onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; e.target.style.boxShadow = "none"; }} />
              {search && <button onClick={() => setSearch("")} className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "#9CA3AF" }}><X size={14} /></button>}
            </div>
            {activeFilters > 0 && (
              <button onClick={() => { setType(CALL_TYPES[0]); setOutcome(OUTCOMES_ALL[0]); setOfficer(OFFICERS[0]); setDept(DEPARTMENTS[0]); setFollowUp(FOLLOWUP[0]); setSummaryFilter("all"); setSearch(""); }}
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-sm" style={{ background: "#FEE2E2", color: "#DC2626", border: "1px solid #FECACA", fontWeight: 600, whiteSpace: "nowrap" }}>
                <X size={13} /> Clear {activeFilters} filter{activeFilters > 1 ? "s" : ""}
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 mr-1"><SlidersHorizontal size={14} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.78rem", color: "#9CA3AF", fontWeight: 500 }}>Filter:</span></div>
            <DropFilter label="Call Type"   value={typeFilter}     options={CALL_TYPES}   onChange={v => { setType(v); setPage(1); }} />
            <DropFilter label="Outcome"     value={outcomeFilter}  options={OUTCOMES_ALL} onChange={v => { setOutcome(v); setPage(1); }} />
            <DropFilter label="Officer"     value={officerFilter}  options={OFFICERS}     onChange={v => { setOfficer(v); setPage(1); }} />
            <DropFilter label="Department"  value={deptFilter}     options={DEPARTMENTS}  onChange={v => { setDept(v); setPage(1); }} />
            <DropFilter label="Follow-up"   value={followUpFilter} options={FOLLOWUP}     onChange={v => { setFollowUp(v); setPage(1); }} />
            <div className="flex-1" />
            <div className="flex items-center gap-2">
              <span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{filtered.length} result{filtered.length !== 1 ? "s" : ""}</span>
              <div className="w-px h-4" style={{ background: "#E5E7EB" }} />
              <select className="text-sm rounded-lg px-3 py-2 outline-none" style={{ border: "1px solid #E5E7EB", color: "#6B7280" }}>
                <option>Sort: Newest</option><option>Sort: Duration</option><option>Sort: Outcome</option>
              </select>
            </div>
          </div>
        </div>

        {/* ── Bulk Actions ── */}
        {selectedRows.length > 0 && (
          <div className="rounded-xl px-5 py-3 flex items-center gap-4" style={{ background: "#EFF6FF", border: "1.5px solid #BFDBFE" }}>
            <span style={{ fontSize: "0.875rem", color: "#0B5FFF", fontWeight: 700 }}>{selectedRows.length} call{selectedRows.length > 1 ? "s" : ""} selected</span>
            <div className="h-4 w-px" style={{ background: "#BFDBFE" }} />
            {[{ label: "Export", c: "#0B5FFF", bg: "#EFF6FF" }, { label: "Assign Follow-up", c: "#7C3AED", bg: "#EDE9FE" }].map(b => (
              <button key={b.label} className="px-3 py-1.5 rounded-lg text-xs" style={{ background: b.bg, color: b.c, fontWeight: 600 }}>{b.label}</button>
            ))}
            <button onClick={() => setSelectedRows([])} className="ml-auto text-xs" style={{ color: "#6B7280" }}>Clear</button>
          </div>
        )}

        {/* ── Main Table ── */}
        <div className="rounded-2xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
          <div className="overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "separate", borderSpacing: 0 }}>
              <thead>
                <tr style={{ background: "#F8FAFC" }}>
                  <th className="px-5 py-3.5 text-left" style={{ borderBottom: "1px solid #E8ECEF", width: "44px" }}>
                    <input type="checkbox" checked={selectedRows.length === paginated.length && paginated.length > 0} onChange={toggleAll} style={{ accentColor: "#0B5FFF", width: "15px", height: "15px" }} />
                  </th>
                  {[
                    { label: "Call ID",            w: "110px" },
                    { label: "Customer",           w: "185px" },
                    { label: "Call Type",          w: "115px" },
                    { label: "From",               w: "130px" },
                    { label: "Duration",           w: "105px" },
                    { label: "Officer",            w: "150px" },
                    { label: "Date & Time",        w: "145px" },
                    { label: "Outcome",            w: "140px" },
                    { label: "Follow-up Required", w: "145px" },
                    { label: "Source",              w: "90px" },
                    { label: "Status",              w: "110px" },
                    { label: "Completion",          w: "220px"  },
                  ].map(col => (
                    <th key={col.label} className="px-4 py-3.5 text-left" style={{ borderBottom: "1px solid #E8ECEF", minWidth: col.w }}>
                      {col.label && <span style={{ fontSize: "0.67rem", fontWeight: 700, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.07em", whiteSpace: "nowrap" }}>{col.label}</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 && (
                  <tr>
                    <td colSpan={13} className="py-16 text-center">
                      <div className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-3" style={{ background: "#F5F7FB" }}>
                        <PhoneCall size={22} style={{ color: "#D1D5DB" }} />
                      </div>
                      <p style={{ color: "#9CA3AF", fontWeight: 500 }}>No calls match your search or filters</p>
                    </td>
                  </tr>
                )}
                {paginated.map((call, i) => {
                  const tc = TYPE_CFG[call.type] ?? TYPE_CFG.Inbound;
                  const oc = OUTCOME_CFG[call.outcome] ?? { text: "#6B7280", bg: "#F3F4F6" };
                  const TypeIcon = tc.icon;
                  const isSelected = selectedRows.includes(call.id);
                  const isMissed   = call.type === "Missed";

                  return (
                    <tr key={call.id}
                      onClick={() => setSelectedCall(call)}
                      style={{
                        background: isSelected ? "#EFF6FF" : isMissed ? "#FFFBEB" : i % 2 === 0 ? "#FFFFFF" : "#FAFBFC",
                        borderBottom: "1px solid #F3F4F6",
                        cursor: "pointer",
                        transition: "background 0.1s",
                        borderLeft: call.followUp ? "3px solid #F59E0B" : "3px solid transparent",
                      }}
                      onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = "#F0F6FF"; }}
                      onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = isMissed ? "#FFFBEB" : i % 2 === 0 ? "#FFFFFF" : "#FAFBFC"; }}>

                      {/* Checkbox */}
                      <td className="px-5 py-3.5" onClick={e => { e.stopPropagation(); toggleRow(call.id); }}>
                        <input type="checkbox" checked={isSelected} onChange={() => toggleRow(call.id)} style={{ accentColor: "#0B5FFF", width: "15px", height: "15px" }} />
                      </td>

                      {/* Call ID */}
                      <td className="px-4 py-3.5">
                        <p style={{ fontSize: "0.8rem", fontWeight: 800, color: "#0B5FFF", fontFamily: "monospace" }}>{call.id}</p>
                        {call.recorded && (
                          <div className="flex items-center gap-1 mt-0.5">
                            <Mic size={10} style={{ color: "#8B5CF6" }} />
                            <span style={{ fontSize: "0.65rem", color: "#8B5CF6", fontWeight: 600 }}>Recorded</span>
                          </div>
                        )}
                      </td>

                      {/* Customer */}
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0"
                            style={{ background: isMissed ? "#94A3B8" : "#0B5FFF", fontSize: "0.55rem", fontWeight: 700 }}>
                            {call.customer === "Unknown Caller" ? "?" : call.customer.split(" ").map(n => n[0]).slice(0, 2).join("")}
                          </div>
                          <div className="min-w-0">
                            <p style={{ fontSize: "0.8rem", fontWeight: 600, color: "#111827", whiteSpace: "nowrap" }}>{call.customer.split(" ").slice(0, 2).join(" ")}</p><RecordAttribution record={call}/>
                            <p style={{ fontSize: "0.68rem", color: "#9CA3AF", fontFamily: call.customerId !== "—" ? "monospace" : "inherit" }}>{call.phone}</p>
                            {call.customerMatch === "not_customer" && <span className="mt-1 inline-flex rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-bold text-red-700">Not a Customer</span>}
                          </div>
                        </div>
                      </td>

                      {/* Call Type */}
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full w-fit" style={{ background: tc.bg, border: `1px solid ${tc.border}` }}>
                          <TypeIcon size={11} style={{ color: tc.text }} />
                          <span style={{ fontSize: "0.72rem", fontWeight: 700, color: tc.text }}>{call.type}</span>
                        </div>
                      </td>

                      <td className="px-4 py-3.5"><span className="text-xs font-semibold text-gray-700">{call.fromNumber || "Not available"}</span></td>

                      {/* Duration */}
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-1.5">
                          <Clock size={12} style={{ color: "#9CA3AF" }} />
                          <span style={{ fontSize: "0.8rem", fontWeight: call.duration !== "—" ? 600 : 400, color: call.duration !== "—" ? "#374151" : "#9CA3AF" }}>{call.duration}</span>
                        </div>
                        {call.durationSecs > 0 && (
                          <div className="mt-1 h-1 rounded-full" style={{ background: "#F3F4F6", width: "60px" }}>
                            <div className="h-full rounded-full" style={{ width: `${Math.min(100, (call.durationSecs / 1200) * 100)}%`, background: call.durationSecs > 600 ? "#DC2626" : call.durationSecs > 300 ? "#F59E0B" : "#16A34A" }} />
                          </div>
                        )}
                      </td>

                      {/* Officer */}
                      <td className="px-4 py-3.5">
                        {call.officer !== "—" ? (
                          <div className="flex items-center gap-2">
                            <div className="w-6 h-6 rounded-full flex items-center justify-center text-white flex-shrink-0" style={{ background: "#7C3AED", fontSize: "0.55rem", fontWeight: 700 }}>
                              {call.officerInitials}
                            </div>
                            <div>
                              <p style={{ fontSize: "0.8rem", color: "#374151", fontWeight: 500 }}>{call.officer.split(" ")[0]}</p>
                              <p style={{ fontSize: "0.65rem", color: "#9CA3AF" }}>{call.department}</p>
                            </div>
                          </div>
                        ) : (
                          <span style={{ fontSize: "0.8rem", color: "#9CA3AF" }}>Unassigned</span>
                        )}
                      </td>

                      {/* Date */}
                      <td className="px-4 py-3.5">
                        <p style={{ fontSize: "0.78rem", color: "#374151" }}>{call.date}</p>
                        <p style={{ fontSize: "0.68rem", color: "#9CA3AF", marginTop: "2px" }}>{call.time}</p>
                      </td>

                      {/* Outcome */}
                      <td className="px-4 py-3.5">
                        <span className="px-2.5 py-1 rounded-full whitespace-nowrap" style={{ fontSize: "0.72rem", fontWeight: 700, color: oc.text, background: oc.bg }}>{call.outcome}</span>
                        {call.linkedTicket && (
                          <p style={{ fontSize: "0.65rem", color: "#0B5FFF", marginTop: "3px", fontFamily: "monospace", fontWeight: 600 }}>→ {call.linkedTicket}</p>
                        )}
                      </td>

                      {/* Follow-up */}
                      <td className="px-4 py-3.5">
                        {call.followUp ? (
                          <div>
                            <div className="flex items-center gap-1.5">
                              <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#F59E0B" }} />
                              <span style={{ fontSize: "0.78rem", fontWeight: 700, color: "#D97706" }}>Required</span>
                            </div>
                            {call.followUpDate && (
                              <p style={{ fontSize: "0.68rem", color: "#9CA3AF", marginTop: "2px" }}>Due: {call.followUpDate}</p>
                            )}
                          </div>
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <CheckCircle2 size={13} style={{ color: "#16A34A" }} />
                            <span style={{ fontSize: "0.78rem", color: "#9CA3AF" }}>None</span>
                          </div>
                        )}
                      </td>

                      <td className="px-4 py-3.5"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${call.source === "android" ? "bg-blue-50 text-blue-700" : "bg-gray-100 text-gray-600"}`}>{call.source === "android" ? "Android" : "Manual"}</span></td>
                      <td className="px-4 py-3.5"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${call.enrichmentStatus === "needs_update" ? "bg-amber-50 text-amber-700" : "bg-green-50 text-green-700"}`}>{call.enrichmentStatus === "needs_update" ? "Needs Update" : "Complete"}</span></td>

                      {/* Completion */}
                      <td className="px-4 py-3.5" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-0.5">
                          {call.enrichmentStatus === "needs_update" && <button onClick={() => setEditingCall(call)} className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-bold text-white">Update</button>}
                          {isMissed && <a href={`tel:${call.phone.replace(/[^+\d]/g, "")}`} title="Call customer" className="flex items-center gap-1 rounded-lg bg-green-50 px-2 py-1.5 text-xs font-bold text-green-700"><PhoneCall size={13} /> Call</a>}
                          {isMissed && call.callbackStatus !== "Called Back" && <button onClick={() => void markCalledBack(call)} title="Mark as called back" className="flex items-center gap-1 rounded-lg bg-red-50 px-2 py-1.5 text-xs font-bold text-red-700"><CheckCircle2 size={13} /> Called back</button>}
                          {isMissed && call.callbackStatus === "Called Back" && <span className="whitespace-nowrap text-xs font-bold text-green-700">Called back</span>}
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
              Showing <strong style={{ color: "#374151" }}>{Math.max(1, (page - 1) * rowsPerPage + 1)}</strong>–<strong style={{ color: "#374151" }}>{Math.min(page * rowsPerPage, filtered.length)}</strong> of <strong style={{ color: "#374151" }}>{filtered.length}</strong> calls
            </span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(1)} disabled={page === 1} className="w-8 h-8 rounded-lg flex items-center justify-center text-xs" style={{ border: "1px solid #E5E7EB", color: page === 1 ? "#D1D5DB" : "#374151" }}>«</button>
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ border: "1px solid #E5E7EB", color: page === 1 ? "#D1D5DB" : "#374151" }}><ChevronLeft size={14} /></button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const p = Math.max(1, Math.min(page - 2, totalPages - 4)) + i;
                if (p < 1 || p > totalPages) return null;
                return (
                  <button key={p} onClick={() => setPage(p)} className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                    style={{ background: p === page ? "#0B5FFF" : "transparent", color: p === page ? "#FFF" : "#374151", fontWeight: p === page ? 700 : 400, border: `1px solid ${p === page ? "#0B5FFF" : "#E5E7EB"}` }}>
                    {p}
                  </button>
                );
              })}
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages || totalPages === 0} className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ border: "1px solid #E5E7EB", color: page === totalPages || totalPages === 0 ? "#D1D5DB" : "#374151" }}><ChevronRight size={14} /></button>
              <button onClick={() => setPage(totalPages)} disabled={page === totalPages || totalPages === 0} className="w-8 h-8 rounded-lg flex items-center justify-center text-xs" style={{ border: "1px solid #E5E7EB", color: page === totalPages || totalPages === 0 ? "#D1D5DB" : "#374151" }}>»</button>
            </div>
          </div>
        </div>
        <div className="h-4" />
      </div>

      {/* Log Call Modals */}
      {logModal && <LogCallModal type={logModal} onClose={() => setLogModal(null)} onSaved={saved => setCalls(current => [saved, ...current])} />}
      {editingCall && <LogCallModal type={editingCall.type as "Inbound" | "Outbound" | "Missed"} initialCall={editingCall} onClose={() => setEditingCall(null)} onSaved={saved => setCalls(current => current.map(call => call.id === saved.id ? saved : call))} />}
    </div>
  );
}
