import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useState } from "react";
import { useTheme } from "./ThemeContext";
import { ActivityVisitSelect } from "./ActivityVisitSelect";
import {
  ChevronLeft, Phone, Mail, MapPin, Calendar, User, CreditCard, Package, Wrench,
  Ticket, MessageSquare, Star, Edit2, MoreHorizontal, CheckCircle2, Clock,
  PhoneCall, FileText, Plus, Building2, Shield, Tag, Copy, Bell, AlertTriangle,
  ChevronRight, ChevronDown, Circle, PhoneIncoming, PhoneOutgoing, PhoneMissed,
  Truck, CheckSquare, Paperclip, Download, Upload, TrendingUp, TrendingDown,
  Zap, CalendarClock, Send, ClipboardList, BarChart2, AlertCircle, Info,
  Repeat, Award, X,
} from "lucide-react";

/* ─── Types ─────────────────────────────────────────────────────── */
type Customer = {
  id: string; name: string; phone: string; email: string;
  branch: string; agent: string; agentInitials: string;
  products: string[]; productCount: number;
  balance: number; balanceFormatted: string; status: string; segment: string;
  joinDate: string; lastInteraction: string; lastInteractionRaw: string;
  tickets: number; csat: number; accountType: string; city: string;
  ic: string; dob: string; gender: string;
  imageUrl?: string; followUpCount?: number;
  occupation?: string;
};
const receiptTime = (value?: string) => {
  if (!value) return "--";
  const parsed = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + "Z");
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("en-GH", { timeZone: "Africa/Accra" });
};

const CUSTOMER_FALLBACK_IMG = "https://imagedelivery.net/h9fmMoa1o2c2P55TcWJGOg/f1b8f81c-1aac-4580-6b1c-869ffafcb400/public";

/* ─── Config ─────────────────────────────────────────────────────── */
const SEGMENT_CFG: Record<string, { text: string; bg: string; border: string; gradient: string }> = {
  VIP:      { text: "#7C3AED", bg: "#F5F3FF", border: "#DDD6FE", gradient: "linear-gradient(135deg,#7C3AED,#5B21B6)" },
  Premium:  { text: "#0B5FFF", bg: "#EFF6FF", border: "#BFDBFE", gradient: "linear-gradient(135deg,#0B5FFF,#0040CC)" },
  Standard: { text: "#6B7280", bg: "#F9FAFB", border: "#E5E7EB", gradient: "linear-gradient(135deg,#64748B,#475569)" },
};
const STATUS_CFG: Record<string, { text: string; bg: string; dot: string }> = {
  Active:    { text: "#16A34A", bg: "#DCFCE7", dot: "#16A34A" },
  Inactive:  { text: "#9CA3AF", bg: "#F3F4F6", dot: "#D1D5DB" },
  Suspended: { text: "#DC2626", bg: "#FEE2E2", dot: "#DC2626" },
  Overdue:   { text: "#D97706", bg: "#FEF3C7", dot: "#F59E0B" },
};

/* ─── Rich Activity Timeline ─────────────────────────────────────── */
const TIMELINE: {
  id: number; date: string; time: string; type: string;
  icon: React.ElementType; color: string; bg: string;
  title: string; detail: string; sub?: string; tag?: string; tagColor?: string; tagBg?: string;
}[] = [
  { id: 1,  date: "Today",          time: "11:45 AM", type: "ticket",   icon: AlertTriangle,  color: "#DC2626", bg: "#FEF2F2",  title: "Ticket Escalated to Level 2",       detail: "Ticket #TK-4821 escalated by Siti Rahimah — refrigerator malfunction (2nd report)", sub: "Priority: High · Assigned: Siti Rahimah",         tag: "Escalated",    tagColor: "#DC2626", tagBg: "#FEF2F2"  },
  { id: 2,  date: "Today",          time: "11:30 AM", type: "call",     icon: PhoneOutgoing,  color: "#0B5FFF", bg: "#EFF6FF",  title: "Outbound Call Made",                 detail: "Siti Rahimah called customer re: Ticket #TK-4821. Duration: 8m 23s",               sub: "Outcome: Issue acknowledged, repair scheduled",    tag: "Completed",    tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 3,  date: "Today",          time: "9:12 AM",  type: "ticket",   icon: Ticket,         color: "#0B5FFF", bg: "#EFF6FF",  title: "Ticket Created — #TK-4821",          detail: "Customer reported refrigerator not cooling. Received via Phone channel",             sub: "Category: Product Defect · SLA: 24 hours",        tag: "Open",         tagColor: "#0B5FFF", tagBg: "#EFF6FF"  },
  { id: 4,  date: "20 Jun 2026",    time: "3:00 PM",  type: "delivery", icon: Truck,          color: "#16A34A", bg: "#F0FDF4",  title: "Delivery Completed",                  detail: "Philips Air Purifier (DLV-2090) delivered successfully to customer's address",     sub: "Officer: Ahmad Faizal · Vehicle: WXY 4421",       tag: "Delivered",    tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 5,  date: "21 May 2026",    time: "8:04 AM",  type: "payment",  icon: CreditCard,     color: "#0B5FFF", bg: "#EFF6FF",  title: "Auto-debit Payment Collected",        detail: "Instalment 7/24 — RM 1,200 successfully debited from account",                    sub: "Payment ID: PAY-0188 · Method: Auto-debit",        tag: "Paid",         tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 6,  date: "18 May 2026",    time: "2:15 PM",  type: "call",     icon: PhoneIncoming,  color: "#16A34A", bg: "#F0FDF4",  title: "Inbound Call Received",               detail: "Customer called to enquire about delivery schedule for Philips Air Purifier",     sub: "Agent: Siti Rahimah · Duration: 5m 47s",          tag: "Resolved",     tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 7,  date: "21 Apr 2026",    time: "8:01 AM",  type: "payment",  icon: CreditCard,     color: "#0B5FFF", bg: "#EFF6FF",  title: "Auto-debit Payment Collected",        detail: "Instalment 6/24 — RM 1,200 successfully debited from account",                    sub: "Payment ID: PAY-0154 · Method: Auto-debit",        tag: "Paid",         tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 8,  date: "14 Apr 2026",    time: "10:30 AM", type: "ticket",   icon: CheckCircle2,   color: "#16A34A", bg: "#F0FDF4",  title: "Ticket Resolved — #TK-4310",         detail: "Delivery delay complaint resolved. Customer confirmed satisfaction",                sub: "Resolved by: Ahmad Faizal · Time to resolve: 2d",  tag: "Resolved",     tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 9,  date: "15 Apr 2026",    time: "11:00 AM", type: "csat",     icon: Star,           color: "#F59E0B", bg: "#FFFBEB",  title: "CSAT Survey Submitted",               detail: "Customer gave ★ 4.8 rating. Comment: 'Very helpful and professional team'",      sub: "Linked to: TK-4310 · Agent rated: Ahmad Faizal",   tag: "★ 4.8",        tagColor: "#D97706", tagBg: "#FEF3C7"  },
  { id: 10, date: "12 Apr 2026",    time: "9:00 AM",  type: "ticket",   icon: Ticket,         color: "#0B5FFF", bg: "#EFF6FF",  title: "Ticket Created — #TK-4310",          detail: "Delivery rescheduled 3 times without notification — customer complaint",           sub: "Category: Delivery Issue · Priority: Medium",      tag: "Open",         tagColor: "#0B5FFF", tagBg: "#EFF6FF"  },
  { id: 11, date: "21 Mar 2026",    time: "8:03 AM",  type: "payment",  icon: CreditCard,     color: "#0B5FFF", bg: "#EFF6FF",  title: "Auto-debit Payment Collected",        detail: "Instalment 5/24 — RM 1,200 successfully debited from account",                    sub: "Payment ID: PAY-0122 · Method: Auto-debit",        tag: "Paid",         tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 12, date: "02 Mar 2026",    time: "3:45 PM",  type: "note",     icon: MessageSquare,  color: "#8B5CF6", bg: "#F5F3FF",  title: "Internal Note Added",                 detail: "Customer prefers morning contact before 12pm. Very particular about billing.",    sub: "Added by: Siti Rahimah",                           tag: "Note",         tagColor: "#7C3AED", tagBg: "#F5F3FF"  },
  { id: 13, date: "20 Jun 2025",    time: "9:30 AM",  type: "repair",   icon: Wrench,         color: "#F97316", bg: "#FFF7ED",  title: "Repair Job Completed — RPR-0044",    detail: "Washing machine drum bearing replaced. Customer satisfaction confirmed.",          sub: "Technician: Lee Chun Wei · Duration: 3 days",      tag: "Completed",    tagColor: "#16A34A", tagBg: "#F0FDF4"  },
  { id: 14, date: "12 Mar 2021",    time: "10:00 AM", type: "account",  icon: Award,          color: "#0B5FFF", bg: "#EFF6FF",  title: "Customer Account Created",            detail: "New customer onboarded. Washing Machine purchased — Instalment plan 24 months.", sub: "Onboarded by: Ahmad Marzuki",                      tag: "New Customer", tagColor: "#0B5FFF", tagBg: "#EFF6FF"  },
];

/* ─── Table mock data ─────────────────────────────────────────────── */
const MOCK_DELIVERIES = [
  { id: "DLV-2090", product: "Philips Air Purifier", date: "20 Jun 2026", officer: "Ahmad Faizal", area: "Wangsa Maju, KL", status: "Delivered" },
  { id: "DLV-1822", product: "Panasonic Washing Machine", date: "12 Mar 2021", officer: "Mohd Shafiq", area: "Wangsa Maju, KL", status: "Delivered" },
];
const MOCK_TASKS = [
  { id: "TSK-334", title: "Follow up on SLA breach TK-4821", due: "Today, 2:00 PM", priority: "High", status: "In Progress", assignee: "Siti Rahimah" },
  { id: "TSK-290", title: "Send repair status update to customer", due: "22 Jun 2026", priority: "Medium", status: "Pending", assignee: "Lee Chun Wei" },
  { id: "TSK-212", title: "Confirm instalment restructuring request", due: "25 Jun 2026", priority: "Low", status: "Not Started", assignee: "Rashid Halim" },
];

/* ─── Helpers ─────────────────────────────────────────────────────── */
const TICKET_STATUS: Record<string, { t: string; bg: string }> = {
  "Open": { t: "#0B5FFF", bg: "#EFF6FF" }, "In Progress": { t: "#F59E0B", bg: "#FFFBEB" },
  "Pending": { t: "#8B5CF6", bg: "#F5F3FF" }, "Escalated": { t: "#DC2626", bg: "#FEF2F2" },
  "Resolved": { t: "#16A34A", bg: "#F0FDF4" },
};
const PAYMENT_STATUS: Record<string, { t: string; bg: string }> = {
  "Paid": { t: "#16A34A", bg: "#F0FDF4" }, "Pending": { t: "#F59E0B", bg: "#FFFBEB" }, "Overdue": { t: "#DC2626", bg: "#FEF2F2" },
};
const TASK_STATUS: Record<string, { t: string; bg: string }> = {
  "In Progress": { t: "#0B5FFF", bg: "#EFF6FF" }, "Pending": { t: "#F59E0B", bg: "#FFFBEB" },
  "Not Started": { t: "#9CA3AF", bg: "#F9FAFB" }, "Completed": { t: "#16A34A", bg: "#F0FDF4" },
};
const DOC_TYPE_CFG: Record<string, { t: string; bg: string }> = {
  "Contract": { t: "#0B5FFF", bg: "#EFF6FF" }, "Finance": { t: "#F59E0B", bg: "#FFFBEB" },
  "Warranty": { t: "#16A34A", bg: "#F0FDF4" }, "Identity": { t: "#8B5CF6", bg: "#F5F3FF" },
};
const PRIORITY_COLOR: Record<string, string> = { High: "#DC2626", Medium: "#F59E0B", Low: "#16A34A" };

function pill(label: string, t: string, bg: string) {
  return (
    <span className="px-2.5 py-1 rounded-full whitespace-nowrap" style={{ fontSize: "0.72rem", fontWeight: 700, color: t, background: bg }}>{label}</span>
  );
}

function SectionCard({ title, children, action, noPad }: { title?: string; children: React.ReactNode; action?: React.ReactNode; noPad?: boolean }) {
  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
      {title && (
        <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid #F3F4F6" }}>
          <p style={{ fontSize: "0.8125rem", fontWeight: 700, color: "#111827" }}>{title}</p>
          {action}
        </div>
      )}
      <div className={noPad ? "" : "px-5 py-4"}>{children}</div>
    </div>
  );
}

function InfoField({ label, value, icon: Icon, mono }: { label: string; value: string; icon?: React.ElementType; mono?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="py-2.5 group" style={{ borderBottom: "1px solid #F9FAFB" }}>
      <p style={{ fontSize: "0.65rem", fontWeight: 700, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: "3px" }}>{label}</p>
      <div className="flex items-center gap-2">
        {Icon && <Icon size={12} style={{ color: "#C4C4C4", flexShrink: 0 }} />}
        <p style={{ fontSize: "0.8125rem", color: "#1F2937", fontWeight: 500, fontFamily: mono ? "monospace" : "inherit" }}>{value}</p>
        {(mono || label === "Email" || label === "Phone") && (
          <button
            onClick={() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }}
            className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded"
            style={{ color: copied ? "#16A34A" : "#C4C4C4" }}
          >
            <Copy size={11} />
          </button>
        )}
      </div>
    </div>
  );
}

function CollapsibleSection({ title, defaultOpen = true, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3.5 transition-colors"
        style={{ background: open ? "#FAFBFC" : "#FFFFFF" }}
        onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"}
        onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = open ? "#FAFBFC" : "#FFFFFF"}
      >
        <p style={{ fontSize: "0.8rem", fontWeight: 700, color: "#374151" }}>{title}</p>
        <ChevronDown size={14} style={{ color: "#9CA3AF", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  );
}

function TableWrapper({ headers, children, emptyIcon: EmptyIcon, emptyMsg }: { headers: string[]; children: React.ReactNode; emptyIcon?: React.ElementType; emptyMsg?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr style={{ background: "#F8FAFC" }}>
            {headers.map(h => (
              <th key={h} className="px-5 py-3 text-left" style={{ fontSize: "0.66rem", fontWeight: 700, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.07em", whiteSpace: "nowrap", borderBottom: "1px solid #F3F4F6" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Tr({ children, i }: { children: React.ReactNode; i: number }) {
  return (
    <tr style={{ borderBottom: "1px solid #F9FAFB", background: i % 2 === 0 ? "#FFFFFF" : "#FAFBFC", cursor: "pointer" }}
      onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F0F6FF"}
      onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = i % 2 === 0 ? "#FFFFFF" : "#FAFBFC"}>
      {children}
    </tr>
  );
}

/* ─── Main Component ─────────────────────────────────────────────── */
export function CustomerProfile({ customer, onBack }: { customer: Customer; onBack: () => void }) {
  const { t } = useTheme();
  const [activeTab, setActiveTab] = useState("overview");
  const [showFollowupModal, setShowFollowupModal] = useState(false);
  const [showReminderModal, setShowReminderModal] = useState(false);
  const [showActivityModal, setShowActivityModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [relatedTickets, setRelatedTickets] = useState<any[]>([]);
  const [relatedCalls, setRelatedCalls] = useState<any[]>([]);
  const [payments, setPayments] = useState<any[]>([]);
  const [paymentTotal, setPaymentTotal] = useState(0);
  const [documents, setDocuments] = useState<any[]>([]);
  const [activities, setActivities] = useState<any[]>([]);
  const [pendingFollowups, setPendingFollowups] = useState(0);
  const [activityLoading, setActivityLoading] = useState(true);
  const [documentName, setDocumentName] = useState("");
  const [documentType, setDocumentType] = useState("Image");
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [followupForm, setFollowupForm] = useState({ date: "", time: "10:00", purpose: "Ticket follow-up", notes: "" });
  const [reminderChannel, setReminderChannel] = useState<"sms" | "whatsapp">("sms");
  const [reminderMessage, setReminderMessage] = useState(`Dear ${customer.name.split(" ")[0]}, this is a reminder from Smart Living. Please contact us if you need assistance.`);
  const [actionError, setActionError] = useState("");
  const [sendingReminder, setSendingReminder] = useState(false);
  const [activityForm, setActivityForm] = useState({ category: "Note", channel: "Field visit", branch: "", salesperson: "", delivery_status: "Unknown", feedback: "", issues: "", requested_products: "", action_taken: "", next_followup: "", next_followup_at: "", title: "", description: "", occurredAt: new Date().toISOString().slice(0, 16) });
  const [savingActivity, setSavingActivity] = useState(false);
  const [activityVisitId, setActivityVisitId] = useState("");
  const [editCount, setEditCount] = useState(0);
  const [profileDetails, setProfileDetails] = useState({ phone: customer.phone || "", email: customer.email || "", location: customer.city || "", occupation: customer.occupation || "" });
  const [editForm, setEditForm] = useState({ phone: customer.phone || "", email: customer.email || "", location: customer.city || "", occupation: customer.occupation || "" });
  const [savingDetails, setSavingDetails] = useState(false);
  const [reminderSuccess, setReminderSuccess] = useState<{ message: string; redirect?: string } | null>(null);

  useEffect(() => {
    let active = true;
    setActivityLoading(true);
    fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/activity`, { credentials: "same-origin" })
      .then(async response => {
        if (!response.ok) throw new Error("Unable to load customer activity");
        return response.json();
      })
      .then(data => {
        if (!active) return;
        setRelatedTickets((data.tickets ?? []).map((ticket: any) => ({
          ...ticket,
          assignee: ticket.owner || "Unassigned",
          updated: [ticket.created, ticket.createdTime].filter(Boolean).join(", "),
        })));
        setRelatedCalls((data.calls ?? []).map((call: any) => ({
          ...call,
          agent: call.officer || "Unassigned",
          date: [call.date, call.time].filter(Boolean).join(", "),
        })));
        setPayments(data.payments ?? []);
        setPaymentTotal(data.paymentSummary?.totalPaid ?? 0);
        setDocuments(data.documents ?? []);
        setActivities(data.activities ?? []);
        setPendingFollowups(data.pendingFollowups ?? 0);
        setEditCount(data.editCount ?? 0);
      })
      .catch(() => {
        if (active) {
          setRelatedTickets([]);
          setRelatedCalls([]);
          setPayments([]);
          setDocuments([]);
          setActivities([]);
        }
      })
      .finally(() => active && setActivityLoading(false));
    return () => { active = false; };
  }, [customer.id]);

  const uploadDocument = async () => {
    if (!documentFile || !documentName.trim()) {
      setDocumentError("Enter a document name and choose an image.");
      return;
    }
    setUploadingDocument(true);
    setDocumentError("");
    try {
      const form = new FormData();
      form.append("image", documentFile);
      const uploadResponse = await fetch("/products/upload_image", { method: "POST", credentials: "same-origin", body: form });
      const uploaded = await uploadResponse.json();
      if (!uploadResponse.ok || !uploaded.success) throw new Error(uploaded.error || "Image upload failed.");
      const response = await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/documents`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: documentName.trim(), type: documentType, url: uploaded.image_url, image_id: uploaded.image_id, size: `${(documentFile.size / 1024 / 1024).toFixed(2)} MB` }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to save document.");
      setDocuments(current => [data.document, ...current]);
      setDocumentName("");
      setDocumentType("Image");
      setDocumentFile(null);
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "Unable to upload document.");
    } finally {
      setUploadingDocument(false);
    }
  };
  const refreshActivity = async () => { const response = await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/activity`, { credentials: "same-origin" }); const data = await response.json(); if (response.ok && data.ok) { setActivities(data.activities || []); setPendingFollowups(data.pendingFollowups || 0); setEditCount(data.editCount ?? 0); } };
  const scheduleFollowup = async () => { setActionError(""); if (!followupForm.date || !followupForm.time) { setActionError("Select a date and time."); return; } const response = await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/followups`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scheduled_at: `${followupForm.date}T${followupForm.time}:00`, purpose: followupForm.purpose, notes: followupForm.notes }) }); const data = await response.json(); if (!response.ok || !data.ok) { setActionError(data.message || "Unable to schedule follow-up."); return; } setShowFollowupModal(false); await refreshActivity(); window.dispatchEvent(new Event("customer-support-followups-changed")); };
  const completeFollowup = async (id: string) => { const response = await fetch(`/api/customer-support/followups/${id}`, { method: "PATCH", credentials: "same-origin" }); if (response.ok) { await refreshActivity(); window.dispatchEvent(new Event("customer-support-followups-changed")); } };
  const sendReminder = async () => { setActionError(""); setSendingReminder(true); try { const response = await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/reminders`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channel: reminderChannel, message: reminderMessage }) }); const data = await response.json(); if (!response.ok || !data.ok) { setActionError(data.message || "Unable to send reminder."); return; } setShowReminderModal(false); setReminderSuccess({ message: data.message || "Reminder sent successfully.", redirect: data.redirect }); await refreshActivity(); } finally { setSendingReminder(false); } };
  const logManualActivity = async () => {
    if (!activityForm.title.trim() || !activityForm.description.trim()) { setActionError("Title and description are required."); return; }
    if (activityForm.category === "Interaction" && activityForm.channel === "Field visit" && !activityVisitId) { setActionError("Select your field visit for this interaction."); return; }
    setSavingActivity(true); setActionError("");
    try {
      const response = await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/activities`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...activityForm, interaction_record: activityForm.category === "Interaction", category: activityForm.category, title: activityForm.title, description: activityForm.description, occurred_at: activityForm.occurredAt, field_visit_id: activityForm.category === "Interaction" && activityForm.channel === "Field visit" ? activityVisitId : undefined }) });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to log activity.");
      setShowActivityModal(false); setActivityVisitId("");
      setActivityForm({ category: "Note", channel: "Field visit", branch: "", salesperson: "", delivery_status: "Unknown", feedback: "", issues: "", requested_products: "", action_taken: "", next_followup: "", next_followup_at: "", title: "", description: "", occurredAt: new Date().toISOString().slice(0, 16) });
      window.dispatchEvent(new Event("customer-support-progress-changed"));
      await refreshActivity();
    } catch (error) { setActionError(error instanceof Error ? error.message : "Unable to log activity."); }
    finally { setSavingActivity(false); }
  };
  const saveCustomerDetails = async () => { setSavingDetails(true);setActionError("");try { const response=await fetch(`/api/customer-support/customers/${encodeURIComponent(customer.id)}/details`,{method:"PATCH",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({phone_number:editForm.phone,email:editForm.email,location:editForm.location,occupation:editForm.occupation})});const data=await response.json();if(!response.ok||!data.ok){setActionError(data.message||"Unable to update customer details.");return;}const next={phone:data.customer.phone,email:data.customer.email,location:data.customer.location,occupation:data.customer.occupation};setProfileDetails(next);setEditForm(next);setEditCount(data.editCount);setShowEditModal(false);await refreshActivity();window.dispatchEvent(new Event("customer-support-customers-changed"));}finally{setSavingDetails(false);}};

  const sc = STATUS_CFG[customer.status] ?? STATUS_CFG.Active;
  const seg = SEGMENT_CFG[customer.segment] ?? SEGMENT_CFG.Standard;
  const avatarColor = customer.segment === "VIP" ? "linear-gradient(135deg,#7C3AED,#5B21B6)" : customer.segment === "Premium" ? "linear-gradient(135deg,#0B5FFF,#0040CC)" : "linear-gradient(135deg,#64748B,#475569)";
  const initials = customer.name.split(" ").map(n => n[0]).slice(0, 2).join("");

  const TABS = [
    { key: "overview",   label: "Overview",   count: null },
    { key: "activities", label: "Activities", count: activities.length },
    { key: "tickets",    label: "Tickets",    count: relatedTickets.length },
    { key: "calls",      label: "Calls",      count: relatedCalls.length },
    { key: "collections",label: "Collections",count: payments.length },
    { key: "deliveries", label: "Deliveries", count: MOCK_DELIVERIES.length },
    { key: "tasks",      label: "Tasks",      count: MOCK_TASKS.length },
    { key: "documents",  label: "Documents",  count: documents.length },
  ];

  /* Timeline grouped by date */
  const grouped = TIMELINE.reduce<Record<string, typeof TIMELINE>>((acc, item) => {
    (acc[item.date] ??= []).push(item);
    return acc;
  }, {});

  return (
    <div className="flex flex-col" style={{ height: "100%", background: t.pageBg, fontFamily: "var(--font-family-body)" }}>

      {/* ════════════════════════════════════════════════════════════
          HEADER HERO
      ════════════════════════════════════════════════════════════ */}
      <div style={{ background: "#FFFFFF", borderBottom: "1px solid #E8ECEF", flexShrink: 0 }}>

        {/* Breadcrumb row */}
        <div className="flex items-center gap-2 px-6 pt-4 pb-2">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "#6B7280", fontWeight: 500 }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#0B5FFF"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "#6B7280"}
          >
            <ChevronLeft size={15} /> Customers
          </button>
          <ChevronRight size={13} style={{ color: "#D1D5DB" }} />
          <span style={{ fontSize: "0.8125rem", color: "#9CA3AF" }}>Customer Profile</span>
          <ChevronRight size={13} style={{ color: "#D1D5DB" }} />
          <span style={{ fontSize: "0.8125rem", fontWeight: 600, color: "#374151" }}>{customer.name}</span>
          <div className="flex-1" />
          <button className="p-2 rounded-lg transition-colors" style={{ color: "#9CA3AF" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
            <MoreHorizontal size={17} />
          </button>
        </div>

        {/* Profile strip */}
        <div className="flex items-center gap-6 px-6 pb-5 pt-2">
          {/* Avatar */}
          <div className="relative flex-shrink-0">
            <img src={customer.imageUrl?.trim() || CUSTOMER_FALLBACK_IMG} alt={customer.name} className="h-20 w-20 rounded-2xl object-cover" style={{ boxShadow: "0 4px 20px rgba(0,0,0,0.18)" }} onError={event => { if (event.currentTarget.src !== CUSTOMER_FALLBACK_IMG) event.currentTarget.src = CUSTOMER_FALLBACK_IMG; }} />
            <div
              className="absolute -bottom-1.5 -right-1.5 w-5 h-5 rounded-full border-2 border-white flex items-center justify-center"
              style={{ background: sc.dot }}
            />
          </div>

          {/* Name block */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap mb-1">
              <h1 style={{ fontSize: "1.35rem", fontWeight: 800, color: "#111827", lineHeight: 1 }}>{customer.name}</h1>
              <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: sc.bg, color: sc.text, fontWeight: 700 }}>{customer.status}</span>
              <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: seg.bg, color: seg.text, border: `1px solid ${seg.border}`, fontWeight: 700 }}>{customer.segment}</span>
            </div>
            <div className="flex items-center flex-wrap gap-x-5 gap-y-1 mt-2">
              <div className="flex items-center gap-1.5" style={{ color: "#6B7280" }}>
                <Tag size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", fontFamily: "monospace", color: "#6B7280" }}>{customer.id}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Phone size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>{profileDetails.phone || "No phone"}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Mail size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>{profileDetails.email || "No email"}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Building2 size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>{customer.branch} Branch</span>
              </div>
              <div className="flex items-center gap-1.5">
                <User size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>Agent: <strong style={{ color: "#374151" }}>{customer.agent}</strong></span>
              </div>
              <div className="flex items-center gap-1.5">
                <MapPin size={12} style={{ color: "#C4C4C4" }} />
                <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>{profileDetails.location || "No location"}</span>
              </div>
            </div>
          </div>

          {/* Quick Action Buttons */}
          <div className="flex items-center gap-2.5 flex-shrink-0">
            <button disabled={editCount >= 2} onClick={() => { setActionError("");setEditForm(profileDetails);setShowEditModal(true); }} title={editCount >= 2 ? "Maximum of two edits reached" : `${2-editCount} edit${2-editCount===1?"":"s"} remaining`} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all disabled:cursor-not-allowed disabled:opacity-50" style={{background:"#FFFFFF",color:"#374151",fontWeight:600,border:"1.5px solid #E5E7EB"}}><Edit2 size={14}/> Edit Customer Details <span className="rounded-full bg-gray-100 px-1.5 text-[10px]">{editCount}/2</span></button>
            <a href={`tel:${profileDetails.phone.replace(/[^+\d]/g, "")}`} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#16A34A", color: "#FFFFFF", fontWeight: 600, boxShadow: "0 2px 8px rgba(22,163,74,0.3)" }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#15803D"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#16A34A"}>
              <Phone size={14} /> Call Customer
            </a>
            <button className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#0B5FFF", color: "#FFFFFF", fontWeight: 600, boxShadow: "0 2px 8px rgba(11,95,255,0.3)" }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#0040CC"}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#0B5FFF"}>
              <Ticket size={14} /> Create Ticket
            </button>
            <button onClick={() => setShowFollowupModal(true)} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#FFFFFF", color: "#374151", fontWeight: 600, border: "1.5px solid #E5E7EB" }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#F5F7FB"; (e.currentTarget as HTMLElement).style.borderColor = "#0B5FFF"; (e.currentTarget as HTMLElement).style.color = "#0B5FFF"; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "#FFFFFF"; (e.currentTarget as HTMLElement).style.borderColor = "#E5E7EB"; (e.currentTarget as HTMLElement).style.color = "#374151"; }}>
              <CalendarClock size={14} /> Schedule Follow-up
              {pendingFollowups > 0 && <span className="rounded-full bg-amber-100 px-1.5 text-xs text-amber-800">{pendingFollowups}</span>}
            </button>
            <button onClick={() => setShowReminderModal(true)} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
              style={{ background: "#FFFFFF", color: "#374151", fontWeight: 600, border: "1.5px solid #E5E7EB" }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#F5F7FB"; (e.currentTarget as HTMLElement).style.borderColor = "#F59E0B"; (e.currentTarget as HTMLElement).style.color = "#D97706"; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "#FFFFFF"; (e.currentTarget as HTMLElement).style.borderColor = "#E5E7EB"; (e.currentTarget as HTMLElement).style.color = "#374151"; }}>
              <Bell size={14} /> Send Reminder
            </button>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex items-center px-6 gap-0" style={{ borderTop: "1px solid #F3F4F6" }}>
          {TABS.map(tab => (
            <button key={tab.key} onClick={() => setActiveTab(tab.key)}
              className="flex items-center gap-2 px-4 py-3 text-sm transition-all"
              style={{
                borderBottom: activeTab === tab.key ? "2.5px solid #0B5FFF" : "2.5px solid transparent",
                color: activeTab === tab.key ? "#0B5FFF" : "#9CA3AF",
                fontWeight: activeTab === tab.key ? 700 : 400,
                marginBottom: "-1px",
                whiteSpace: "nowrap",
              }}>
              {tab.label}
              {tab.count !== null && (
                <span className="px-1.5 py-0.5 rounded-full" style={{ background: activeTab === tab.key ? "#EFF6FF" : "#F5F7FB", color: activeTab === tab.key ? "#0B5FFF" : "#9CA3AF", fontSize: "0.62rem", fontWeight: 700 }}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ════════════════════════════════════════════════════════════
          BODY
      ════════════════════════════════════════════════════════════ */}
      <div className="flex-1 overflow-y-auto">

        {/* ─── OVERVIEW ─── */}
        {activeTab === "overview" && (
          <div className="flex min-h-full">

            {/* Left sidebar */}
            <div className="flex-shrink-0 overflow-y-auto p-4 space-y-3" style={{ width: "288px", borderRight: "1px solid #E8ECEF", background: "#FFFFFF" }}>

              {/* Alert if problematic */}
              {(customer.status === "Overdue" || customer.status === "Suspended") && (
                <div className="rounded-xl p-3.5" style={{ background: "#FEF2F2", border: "1.5px solid #FECACA" }}>
                  <div className="flex items-start gap-2.5">
                    <AlertTriangle size={14} style={{ color: "#DC2626", flexShrink: 0, marginTop: "1px" }} />
                    <div>
                      <p style={{ fontSize: "0.78rem", fontWeight: 700, color: "#DC2626" }}>{customer.status === "Overdue" ? "Overdue Balance" : "Account Suspended"}</p>
                      <p style={{ fontSize: "0.7rem", color: "#B91C1C", marginTop: "2px", lineHeight: 1.4 }}>
                        {customer.status === "Overdue" ? `${customer.balanceFormatted} outstanding — immediate follow-up needed.` : "Account access restricted."}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <CollapsibleSection title="Customer Information" defaultOpen>
                <InfoField label="Customer ID"   value={customer.id}          icon={Tag}      mono />
                <InfoField label="IC / NRIC"     value={customer.ic}          icon={Shield}   mono />
                <InfoField label="Date of Birth" value={customer.dob}         icon={Calendar} />
                <InfoField label="Gender"        value={customer.gender}      icon={User} />
                <InfoField label="Phone"         value={customer.phone}       icon={Phone} />
                <InfoField label="Email"         value={customer.email}       icon={Mail} />
                <InfoField label="Address"       value={customer.city}        icon={MapPin} />
                <InfoField label="Branch"        value={customer.branch}      icon={Building2} />
                <InfoField label="Assigned Agent"value={customer.agent}       icon={User} />
                <InfoField label="Account Type"  value={customer.accountType} icon={CreditCard} />
                <InfoField label="Customer Since" value={customer.joinDate}   icon={Calendar} />
              </CollapsibleSection>

              <CollapsibleSection title="Products Purchased" defaultOpen>
                <div className="space-y-2 pt-1">
                  {customer.products.map((p, i) => (
                    <div key={i} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5" style={{ background: "#F0FDF4", border: "1px solid #BBF7D0" }}>
                      <Package size={13} style={{ color: "#16A34A", flexShrink: 0 }} />
                      <span style={{ fontSize: "0.8rem", color: "#15803D", fontWeight: 500 }}>{p}</span>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              <CollapsibleSection title="Outstanding Balance" defaultOpen>
                <div className="pt-1">
                  <div className="rounded-xl p-4 text-center mb-3" style={{ background: customer.balance > 2000 ? "#FEF2F2" : customer.balance > 0 ? "#FFFBEB" : "#F0FDF4", border: `1px solid ${customer.balance > 2000 ? "#FECACA" : customer.balance > 0 ? "#FDE68A" : "#BBF7D0"}` }}>
                    <p style={{ fontSize: "1.8rem", fontWeight: 800, color: customer.balance > 2000 ? "#DC2626" : customer.balance > 0 ? "#D97706" : "#16A34A", lineHeight: 1 }}>
                      {customer.balanceFormatted}
                    </p>
                    <p style={{ fontSize: "0.72rem", color: "#9CA3AF", marginTop: "4px" }}>
                      {customer.balance > 0 ? `Instalment ${customer.accountType}` : "No outstanding balance"}
                    </p>
                  </div>
                  <div className="space-y-2">
                    {[
                      { label: "Monthly Instalment", value: "RM 1,200" },
                      { label: "Instalment Progress", value: "7 / 24 months" },
                      { label: "Next Payment Due", value: "21 Jul 2026" },
                      { label: "Payment Method", value: "Auto-debit" },
                    ].map(row => (
                      <div key={row.label} className="flex items-center justify-between">
                        <span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{row.label}</span>
                        <span style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>{row.value}</span>
                      </div>
                    ))}
                  </div>
                  {/* Progress bar */}
                  <div className="mt-3">
                    <div className="h-2 rounded-full" style={{ background: "#E5E7EB" }}>
                      <div className="h-full rounded-full" style={{ width: "29%", background: "#16A34A" }} />
                    </div>
                    <p style={{ fontSize: "0.65rem", color: "#9CA3AF", marginTop: "4px", textAlign: "center" }}>29% of instalment plan completed</p>
                  </div>
                </div>
              </CollapsibleSection>

              <CollapsibleSection title="Payment History Summary">
                <div className="space-y-2 pt-1">
                  {[
                    { label: "Total Paid", value: `GHS ${paymentTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: "#16A34A" },
                    { label: "Payment Records", value: payments.length.toString(), color: "#374151" },
                    { label: "Latest Payment", value: payments[0]?.date || "No payments", color: "#374151" },
                  ].map(row => (
                    <div key={row.label} className="flex items-center justify-between py-1.5" style={{ borderBottom: "1px solid #F9FAFB" }}>
                      <span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{row.label}</span>
                      <span style={{ fontSize: "0.8125rem", fontWeight: 700, color: row.color }}>{row.value}</span>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              <CollapsibleSection title="Customer Satisfaction">
                <div className="pt-1">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <p style={{ fontSize: "2rem", fontWeight: 800, color: "#111827", lineHeight: 1 }}>{customer.csat}</p>
                      <p style={{ fontSize: "0.68rem", color: "#9CA3AF", marginTop: "2px" }}>out of 5.0</p>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <div className="flex gap-0.5">
                        {[1,2,3,4,5].map(s => (
                          <Star key={s} size={16} style={{ color: s <= Math.round(customer.csat) ? "#F59E0B" : "#E5E7EB", fill: s <= Math.round(customer.csat) ? "#F59E0B" : "#E5E7EB" }} />
                        ))}
                      </div>
                      <p style={{ fontSize: "0.7rem", color: "#9CA3AF" }}>3 surveys</p>
                    </div>
                  </div>
                  {[
                    { label: "Support",    score: 4.9 },
                    { label: "Delivery",   score: 4.5 },
                    { label: "Repairs",    score: 4.7 },
                  ].map(row => (
                    <div key={row.label} className="mb-2.5">
                      <div className="flex items-center justify-between mb-1">
                        <span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{row.label}</span>
                        <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "#374151" }}>★ {row.score}</span>
                      </div>
                      <div className="h-1.5 rounded-full" style={{ background: "#F3F4F6" }}>
                        <div className="h-full rounded-full" style={{ width: `${(row.score/5)*100}%`, background: "#F59E0B" }} />
                      </div>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              {/* Tags */}
              <CollapsibleSection title="Tags">
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {[customer.segment, customer.accountType, "Loyal Customer", customer.tickets > 3 ? "High Engagement" : "Low Ticket"].map(t => (
                    <span key={t} className="px-2.5 py-1 rounded-full text-xs" style={{ background: "#F5F7FB", color: "#6B7280", border: "1px solid #E5E7EB" }}>{t}</span>
                  ))}
                  <button className="px-2 py-1 rounded-full text-xs" style={{ background: "#EFF6FF", color: "#0B5FFF", border: "1px dashed #BFDBFE" }}>+ Add</button>
                </div>
              </CollapsibleSection>
            </div>

            {/* Right: Activity Timeline + Log Note */}
            <div className="flex-1 p-5 space-y-4 overflow-y-auto">

              {/* Log note bar */}
              <div className="rounded-xl p-4" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
                <div className="flex items-center gap-3 mb-3">
                  {["Note", "Email", "Call", "Task"].map(type => (
                    <button key={type} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all"
                      style={{ background: type === "Note" ? "#EFF6FF" : "#F5F7FB", color: type === "Note" ? "#0B5FFF" : "#6B7280", border: `1px solid ${type === "Note" ? "#BFDBFE" : "#E5E7EB"}`, fontWeight: type === "Note" ? 700 : 400 }}>
                      {type === "Note" && <MessageSquare size={11} />}
                      {type === "Email" && <Mail size={11} />}
                      {type === "Call" && <Phone size={11} />}
                      {type === "Task" && <CheckSquare size={11} />}
                      {type}
                    </button>
                  ))}
                </div>
                <textarea
                  value={noteText}
                  onChange={e => setNoteText(e.target.value)}
                  placeholder="Add a note about this customer interaction..."
                  className="w-full rounded-lg p-3 text-sm outline-none resize-none"
                  rows={3}
                  style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFFFFF"; }}
                  onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }}
                />
                {noteText && (
                  <div className="flex justify-end mt-2">
                    <button className="px-4 py-1.5 rounded-lg text-sm" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 600 }} onClick={() => setNoteText("")}>
                      Save Note
                    </button>
                  </div>
                )}
              </div>

              {/* Activity Timeline header */}
              <div className="flex items-center justify-between">
                <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Activity Timeline</h3>
                <div className="flex items-center gap-2">
                  <select className="text-xs rounded-lg px-3 py-1.5 outline-none" style={{ border: "1px solid #E5E7EB", color: "#6B7280" }}>
                    <option>All Activities</option>
                    <option>Tickets</option>
                    <option>Payments</option>
                    <option>Calls</option>
                    <option>Deliveries</option>
                  </select>
                </div>
              </div>

              {/* Timeline items grouped by date */}
              <div className="space-y-6">
                {Object.entries(grouped).map(([date, items]) => (
                  <div key={date}>
                    {/* Date label */}
                    <div className="flex items-center gap-3 mb-4">
                      <div className="h-px flex-1" style={{ background: "#E8ECEF" }} />
                      <span className="px-3 py-1 rounded-full text-xs" style={{ background: "#F0F4FF", color: "#0B5FFF", fontWeight: 700, border: "1px solid #BFDBFE", whiteSpace: "nowrap" }}>
                        {date}
                      </span>
                      <div className="h-px flex-1" style={{ background: "#E8ECEF" }} />
                    </div>

                    {/* Items */}
                    <div className="relative space-y-3">
                      {/* Vertical line */}
                      <div className="absolute left-5 top-5 bottom-5 w-px" style={{ background: "#E8ECEF" }} />

                      {items.map((item) => (
                        <div key={item.id} className="relative flex items-start gap-4 rounded-xl p-4 transition-all cursor-pointer"
                          style={{ background: "#FFFFFF", border: "1px solid #F3F4F6", boxShadow: "0 1px 3px rgba(0,0,0,0.03)", marginLeft: "0" }}
                          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = "#BFDBFE"; (e.currentTarget as HTMLElement).style.boxShadow = "0 2px 12px rgba(11,95,255,0.06)"; }}
                          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = "#F3F4F6"; (e.currentTarget as HTMLElement).style.boxShadow = "0 1px 3px rgba(0,0,0,0.03)"; }}>

                          {/* Icon */}
                          <div className="relative z-10 flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: item.bg, border: `1.5px solid ${item.color}22` }}>
                            <item.icon size={16} style={{ color: item.color }} />
                          </div>

                          {/* Content */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-3">
                              <div className="flex-1">
                                <div className="flex items-center gap-2 flex-wrap mb-1">
                                  <p style={{ fontSize: "0.85rem", fontWeight: 700, color: "#111827" }}>{item.title}</p>
                                  {item.tag && (
                                    <span className="px-2 py-0.5 rounded-full" style={{ fontSize: "0.65rem", fontWeight: 700, color: item.tagColor, background: item.tagBg }}>
                                      {item.tag}
                                    </span>
                                  )}
                                </div>
                                <p style={{ fontSize: "0.8rem", color: "#6B7280", lineHeight: 1.5 }}>{item.detail}</p>
                                {item.sub && (
                                  <p style={{ fontSize: "0.72rem", color: "#9CA3AF", marginTop: "3px" }}>{item.sub}</p>
                                )}
                              </div>
                              <div className="flex-shrink-0 text-right">
                                <p style={{ fontSize: "0.72rem", color: "#9CA3AF", whiteSpace: "nowrap" }}>{item.time}</p>
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}

                {/* Load more */}
                <div className="text-center pb-4">
                  <button className="px-5 py-2.5 rounded-xl text-sm transition-all" style={{ background: "#F5F7FB", color: "#6B7280", border: "1px solid #E5E7EB", fontWeight: 500 }}
                    onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#EFF6FF"}
                    onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"}>
                    Load older activity
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ─── TICKETS TAB ─── */}
        {activeTab === "activities" && <div className="p-5"><SectionCard title="Customer Activity" action={<button onClick={() => { setActionError(""); setShowActivityModal(true); }} className="flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white"><Plus size={13}/> Log Activity</button>}><div className="relative ml-3 border-l-2 border-blue-100 pl-7">{activities.map(activity => <div key={activity.id} className="relative pb-7 last:pb-0"><div className={`absolute -left-[37px] top-0 h-5 w-5 rounded-full border-4 border-white ${activity.type === "followup" ? "bg-amber-500" : activity.type === "payment" ? "bg-green-600" : activity.type === "ticket" ? "bg-blue-600" : activity.type === "call" ? "bg-indigo-600" : activity.type === "manual" ? "bg-purple-600" : "bg-gray-500"}`} /><div className="rounded-lg border border-gray-200 p-4"><div className="flex justify-between gap-3"><div><h3 className="text-sm font-bold text-gray-900">{activity.title}</h3><RecordAttribution record={activity}/><p className="mt-1 whitespace-pre-wrap text-sm text-gray-600">{activity.detail}</p><p className="mt-2 text-xs text-gray-400">{new Date(activity.occurredAt).toLocaleString()}</p></div><div className="flex items-start gap-2"><span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-bold text-gray-600">{activity.status}</span>{activity.canComplete && <button onClick={() => void completeFollowup(activity.recordId)} className="rounded-md bg-green-600 px-3 py-1.5 text-xs font-bold text-white">Mark Done</button>}</div></div></div></div>)}{!activities.length && <p className="pb-6 text-sm text-gray-400">No customer activity recorded.</p>}</div></SectionCard></div>}

        {activeTab === "tickets" && (
          <div className="p-5">
            <SectionCard title="All Tickets" noPad
              action={
                <button className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 600 }}>
                  <Plus size={12} /> New Ticket
                </button>
              }>
              <TableWrapper headers={["Ticket ID","Subject","Priority","Status","Assignee","Channel","Created","Last Updated"]}>
                {!activityLoading && relatedTickets.length === 0 && <tr><td colSpan={8} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>No tickets linked to this customer.</td></tr>}
                {activityLoading && <tr><td colSpan={8} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>Loading tickets...</td></tr>}
                {relatedTickets.map((t, i) => {
                  const tc = TICKET_STATUS[t.status] ?? { t: "#6B7280", bg: "#F9FAFB" };
                  return (
                    <Tr key={t.id} i={i}>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0B5FFF" }}>{t.id}</span></td>
                      <td className="px-5 py-4" style={{ maxWidth: "220px" }}><span style={{ fontSize: "0.8rem", color: "#374151" }}>{t.subject}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.78rem", fontWeight: 700, color: PRIORITY_COLOR[t.priority] }}>{t.priority}</span></td>
                      <td className="px-5 py-4">{pill(t.status, tc.t, tc.bg)}</td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{t.assignee}</span></td>
                      <td className="px-5 py-4"><span className="px-2 py-0.5 rounded text-xs" style={{ background: "#F5F7FB", color: "#6B7280", border: "1px solid #E5E7EB" }}>{t.channel}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{t.created}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.75rem", color: "#374151", fontWeight: 500 }}>{t.updated}</span></td>
                    </Tr>
                  );
                })}
              </TableWrapper>
            </SectionCard>
          </div>
        )}

        {/* ─── CALLS TAB ─── */}
        {activeTab === "calls" && (
          <div className="p-5">
            <SectionCard title="Call History" noPad
              action={
                <button className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs" style={{ background: "#16A34A", color: "#FFF", fontWeight: 600 }}>
                  <Phone size={12} /> Log Call
                </button>
              }>
              <TableWrapper headers={["Call ID","Type","Purpose","Duration","Outcome","Agent","Date / Time"]}>
                {!activityLoading && relatedCalls.length === 0 && <tr><td colSpan={7} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>No calls linked to this customer.</td></tr>}
                {activityLoading && <tr><td colSpan={7} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>Loading calls...</td></tr>}
                {relatedCalls.map((c, i) => {
                  const typeCfg = c.type === "Inbound" ? { t: "#16A34A", bg: "#F0FDF4" } : c.type === "Outbound" ? { t: "#0B5FFF", bg: "#EFF6FF" } : { t: "#DC2626", bg: "#FEF2F2" };
                  const outCfg = c.outcome === "Resolved" || c.outcome === "Acknowledged" ? { t: "#16A34A", bg: "#F0FDF4" } : c.outcome === "Promise" ? { t: "#F59E0B", bg: "#FFFBEB" } : { t: "#6B7280", bg: "#F9FAFB" };
                  const TIcon = c.type === "Inbound" ? PhoneIncoming : c.type === "Outbound" ? PhoneOutgoing : PhoneMissed;
                  return (
                    <Tr key={c.id} i={i}>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0B5FFF" }}>{c.id}</span></td>
                      <td className="px-5 py-4">
                        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full w-fit" style={{ background: typeCfg.bg }}>
                          <TIcon size={11} style={{ color: typeCfg.t }} />
                          <span style={{ fontSize: "0.72rem", fontWeight: 700, color: typeCfg.t }}>{c.type}</span>
                        </div>
                      </td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{c.purpose}</span></td>
                      <td className="px-5 py-4">
                        <div className="flex items-center gap-1"><Clock size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.8rem", color: "#374151" }}>{c.duration}</span></div>
                      </td>
                      <td className="px-5 py-4">{pill(c.outcome, outCfg.t, outCfg.bg)}</td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{c.agent}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.75rem", color: "#9CA3AF" }}>{c.date}</span></td>
                    </Tr>
                  );
                })}
              </TableWrapper>
            </SectionCard>
          </div>
        )}

        {/* ─── COLLECTIONS TAB ─── */}
        {activeTab === "collections" && (
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Total Paid", value: `GHS ${paymentTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, icon: CheckCircle2, color: "#16A34A", bg: "#F0FDF4" },
                { label: "Outstanding", value: customer.balanceFormatted, icon: CreditCard, color: customer.balance > 0 ? "#DC2626" : "#16A34A", bg: customer.balance > 0 ? "#FEF2F2" : "#F0FDF4" },
                { label: "Payment Records", value: payments.length.toString(), icon: Calendar, color: "#0B5FFF", bg: "#EFF6FF" },
              ].map(k => (
                <div key={k.label} className="rounded-xl p-4 flex items-center gap-4" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF" }}>
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: k.bg }}>
                    <k.icon size={18} style={{ color: k.color }} />
                  </div>
                  <div>
                    <p style={{ fontSize: "1.25rem", fontWeight: 800, color: "#111827" }}>{k.value}</p>
                    <p style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>{k.label}</p>
                  </div>
                </div>
              ))}
            </div>
            <SectionCard title="Payment History" noPad>
              <TableWrapper headers={["Payment ID","Amount","Payment For","Method","Date / Time","Status","Receipt"]}>
                {activityLoading && <tr><td colSpan={7} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>Loading payments...</td></tr>}
                {!activityLoading && payments.length === 0 && <tr><td colSpan={7} className="px-5 py-10 text-center text-sm" style={{ color: "#9CA3AF" }}>No payments recorded for this customer.</td></tr>}
                {payments.map((p, i) => {
                  const pc = PAYMENT_STATUS[p.status] ?? { t: "#6B7280", bg: "#F9FAFB" };
                  return (
                    <Tr key={p.id} i={i}>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0B5FFF" }}>{p.id}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.9rem", fontWeight: 800, color: "#111827" }}>GHS {Number(p.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#6B7280" }}>{p.product || p.type}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{p.method}</span></td>
                      <td className="px-5 py-4"><span style={{ fontSize: "0.78rem", color: "#9CA3AF" }}>{p.date}{p.time ? `, ${p.time}` : ""}</span></td>
                      <td className="px-5 py-4">{pill(p.status, pc.t, pc.bg)}</td>
                      <td className="px-5 py-4" style={{ fontSize: "0.75rem", minWidth: 245 }}>
                        <strong style={{ color: p.printed ? "#16A34A" : "#6B7280" }}>Printed: {p.printed ? "Yes" : "No"}</strong>
                        <div>Recorded: {receiptTime(p.payment_recorded_at)}</div>
                        <div>Printed at: {receiptTime(p.printed_at)}</div>
                        <div>Payment ID: {p.payment_id || "--"}</div>
                        <div>Interval: {p.print_delay_seconds == null ? "--" : `${Number(p.print_delay_seconds).toFixed(1)} seconds`}</div>
                        <details className="mt-1"><summary>Short / long receipts</summary>
                          {(["short", "full"] as const).map(format => {
                            const receipt = p.receipt_prints?.[format];
                            return <div key={format} className="mt-2">
                              <strong>{format === "short" ? "Short" : "Long"}: {receipt?.printed ? "Printed" : "Not printed"}</strong>
                              <div>Printed at: {receiptTime(receipt?.printed_at)}</div>
                              <div>Payment ID: {p.payment_id || "--"}</div>
                              <div>Interval: {receipt?.print_delay_seconds == null ? "--" : `${Number(receipt.print_delay_seconds).toFixed(1)} seconds`}</div>
                            </div>;
                          })}
                        </details>
                      </td>
                    </Tr>
                  );
                })}
              </TableWrapper>
            </SectionCard>
          </div>
        )}

        {/* ─── DELIVERIES TAB ─── */}
        {activeTab === "deliveries" && (
          <div className="p-5">
            <SectionCard title="Delivery History" noPad>
              <TableWrapper headers={["Delivery ID","Product","Delivery Date","Officer","Area","Status"]}>
                {MOCK_DELIVERIES.map((d, i) => (
                  <Tr key={d.id} i={i}>
                    <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0B5FFF" }}>{d.id}</span></td>
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-2">
                        <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: "#F0FDF4" }}>
                          <Package size={13} style={{ color: "#16A34A" }} />
                        </div>
                        <span style={{ fontSize: "0.8rem", color: "#374151", fontWeight: 500 }}>{d.product}</span>
                      </div>
                    </td>
                    <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{d.date}</span></td>
                    <td className="px-5 py-4"><span style={{ fontSize: "0.8rem", color: "#374151" }}>{d.officer}</span></td>
                    <td className="px-5 py-4"><div className="flex items-center gap-1.5"><MapPin size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.8rem", color: "#6B7280" }}>{d.area}</span></div></td>
                    <td className="px-5 py-4">{pill(d.status, "#16A34A", "#F0FDF4")}</td>
                  </Tr>
                ))}
              </TableWrapper>
            </SectionCard>
          </div>
        )}

        {/* ─── TASKS TAB ─── */}
        {activeTab === "tasks" && (
          <div className="p-5 space-y-3">
            <div className="flex items-center justify-between mb-1">
              <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Customer Tasks</h3>
              <button className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 600 }}>
                <Plus size={12} /> New Task
              </button>
            </div>
            {MOCK_TASKS.map(task => {
              const tc = TASK_STATUS[task.status] ?? { t: "#6B7280", bg: "#F9FAFB" };
              return (
                <div key={task.id} className="rounded-xl p-4 flex items-center gap-4 cursor-pointer transition-all"
                  style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = "#BFDBFE"; (e.currentTarget as HTMLElement).style.boxShadow = "0 2px 10px rgba(11,95,255,0.06)"; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = "#E8ECEF"; (e.currentTarget as HTMLElement).style.boxShadow = "0 1px 3px rgba(0,0,0,0.04)"; }}>
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: tc.bg }}>
                    <CheckSquare size={14} style={{ color: tc.t }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span style={{ fontSize: "0.7rem", color: "#9CA3AF", fontFamily: "monospace" }}>{task.id}</span>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: PRIORITY_COLOR[task.priority] }} />
                      <span style={{ fontSize: "0.72rem", fontWeight: 600, color: PRIORITY_COLOR[task.priority] }}>{task.priority}</span>
                    </div>
                    <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "#111827" }}>{task.title}</p>
                    <div className="flex items-center gap-4 mt-1">
                      <div className="flex items-center gap-1.5"><Calendar size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>Due: {task.due}</span></div>
                      <div className="flex items-center gap-1.5"><User size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{task.assignee}</span></div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    {pill(task.status, tc.t, tc.bg)}
                    <button className="p-1.5 rounded-lg" style={{ color: "#9CA3AF" }} onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"} onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
                      <MoreHorizontal size={15} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* ─── DOCUMENTS TAB ─── */}
        {activeTab === "documents" && (
          <div className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Customer Documents</h3>
            </div>
            <div className="rounded-lg border border-gray-200 bg-white p-5">
              <div className="grid gap-3 md:grid-cols-[1fr_180px_1fr_auto] md:items-end">
                <label className="text-xs font-semibold text-gray-600">Document name<input value={documentName} onChange={e => setDocumentName(e.target.value)} placeholder="e.g. National ID front" className="mt-1 block w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm font-normal outline-none focus:border-blue-600" /></label>
                <label className="text-xs font-semibold text-gray-600">Type<select value={documentType} onChange={e => setDocumentType(e.target.value)} className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-normal"><option>Image</option><option>Identity</option><option>Contract</option><option>Finance</option><option>Warranty</option></select></label>
                <label className="text-xs font-semibold text-gray-600">Image file<input type="file" accept="image/png,image/jpeg,image/gif" onChange={e => setDocumentFile(e.target.files?.[0] ?? null)} className="mt-1 block w-full text-sm text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-blue-50 file:px-3 file:py-2 file:font-semibold file:text-blue-700" /></label>
                <button onClick={() => void uploadDocument()} disabled={uploadingDocument || !documentFile || !documentName.trim()} className="flex h-[42px] items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-bold text-white disabled:opacity-50"><Upload size={15} />{uploadingDocument ? "Uploading..." : "Upload"}</button>
              </div>
              <p className="mt-2 text-xs text-gray-400">JPG, PNG, or GIF. The image is stored through Cloudflare Images.</p>
              {documentError && <p className="mt-2 text-sm font-medium text-red-600">{documentError}</p>}
            </div>
            {/* Documents list */}
            <div className="space-y-2">
              {activityLoading && <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">Loading documents...</div>}
              {!activityLoading && documents.length === 0 && <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">No documents uploaded for this customer.</div>}
              {documents.map((doc) => {
                const dc = DOC_TYPE_CFG[doc.type] ?? { t: "#6B7280", bg: "#F9FAFB" };
                return (
                  <div key={doc.id} className="rounded-xl px-5 py-4 flex items-center gap-4 cursor-pointer transition-all"
                    style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}
                    onMouseEnter={e => (e.currentTarget as HTMLElement).style.borderColor = "#BFDBFE"}
                    onMouseLeave={e => (e.currentTarget as HTMLElement).style.borderColor = "#E8ECEF"}>
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: dc.bg }}>
                      <FileText size={18} style={{ color: dc.t }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "#111827" }}>{doc.name}</p><RecordAttribution record={doc}/>
                      <div className="flex items-center gap-3 mt-1">
                        {pill(doc.type, dc.t, dc.bg)}
                        <span style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>{doc.size}</span>
                        <span style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>by {doc.uploadedBy}</span>
                        <span style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>{doc.date}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button title="Open document" onClick={() => window.open(doc.url, "_blank", "noopener,noreferrer")} className="p-2 rounded-lg transition-all" style={{ color: "#9CA3AF" }}
                        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#EFF6FF"; (e.currentTarget as HTMLElement).style.color = "#0B5FFF"; }}
                        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "#9CA3AF"; }}>
                        <Download size={15} />
                      </button>
                      <button className="p-2 rounded-lg transition-all" style={{ color: "#9CA3AF" }}
                        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = "#F5F7FB"; (e.currentTarget as HTMLElement).style.color = "#374151"; }}
                        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "#9CA3AF"; }}>
                        <MoreHorizontal size={15} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ════════════════════════════════════════════════════════════
          MODALS
      ════════════════════════════════════════════════════════════ */}
      {showEditModal && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={()=>setShowEditModal(false)}><div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white shadow-2xl" onClick={event=>event.stopPropagation()}><div className="flex items-center justify-between border-b border-gray-100 px-6 py-5"><div><h3 className="font-bold text-gray-900">Edit Customer Details</h3><p className="mt-1 text-xs text-gray-500">Edit {editCount+1} of 2. Changes are permanently logged.</p></div><button onClick={()=>setShowEditModal(false)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><X size={17}/></button></div><div className="grid gap-4 p-6 sm:grid-cols-2"><label className="text-xs font-semibold text-gray-600">Phone number<input value={editForm.phone} onChange={e=>setEditForm({...editForm,phone:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label><label className="text-xs font-semibold text-gray-600">Email address<input type="email" value={editForm.email} onChange={e=>setEditForm({...editForm,email:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label><label className="text-xs font-semibold text-gray-600">Location<input value={editForm.location} onChange={e=>setEditForm({...editForm,location:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label><label className="text-xs font-semibold text-gray-600">Occupation<input value={editForm.occupation} onChange={e=>setEditForm({...editForm,occupation:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label>{actionError&&<p className="sm:col-span-2 text-sm font-medium text-red-600">{actionError}</p>}<div className="flex justify-end gap-3 pt-2 sm:col-span-2"><button onClick={()=>setShowEditModal(false)} className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-600">Cancel</button><button disabled={savingDetails} onClick={()=>void saveCustomerDetails()} className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50">{savingDetails?"Saving…":"Save Changes"}</button></div></div></div></div>}

      {showActivityModal && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setShowActivityModal(false)}><div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white shadow-2xl" onClick={event => event.stopPropagation()}><div className="flex items-center justify-between border-b border-gray-100 px-6 py-5"><div><h3 className="font-bold text-gray-900">Log Customer Activity</h3><p className="mt-1 text-xs text-gray-500">Add an interaction or note to {customer.name}'s timeline.</p></div><button onClick={() => setShowActivityModal(false)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><X size={17}/></button></div><div className="space-y-4 p-6"><label className="block text-xs font-semibold text-gray-600">Category<select value={activityForm.category} onChange={e => setActivityForm({...activityForm,category:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option>Note</option><option>Interaction</option><option>Complaint</option><option>Visit</option><option>Email</option><option>Other</option></select></label>{activityForm.category === "Interaction" && activityForm.channel === "Field visit" && <ActivityVisitSelect value={activityVisitId} onChange={(id, startedAt) => { setActivityVisitId(id); if (startedAt) setActivityForm(current => ({ ...current, occurredAt: startedAt })); }} />}{activityForm.category === "Interaction" && <div className="rounded-xl border border-blue-100 bg-blue-50/40 p-4 space-y-3"><p className="text-xs text-blue-700">Customer: {customer.name}. The signed-in staff member is recorded automatically as the performer.</p><label className="block text-xs font-semibold text-gray-600">Channel<select aria-label="Channel" value={activityForm.channel} onChange={e=>setActivityForm({...activityForm,channel:e.target.value})} className="mt-1 w-full rounded-lg border p-2 bg-white">{["Call","Field visit","Branch visit","Email","WhatsApp","SMS","Other"].map(channel=><option key={channel}>{channel}</option>)}</select></label>{([['branch','Branch *'],['salesperson','Branch salesperson'],['feedback','Customer feedback *'],['issues','Issues / outstanding concerns'],['requested_products','Requested products'],['action_taken','Action taken *'],['next_followup','Next follow-up / no follow-up reason']] as const).map(([key,label])=><label key={key} className="block text-xs font-semibold text-gray-600">{label}<textarea rows={key==='branch'||key==='salesperson'?1:2} maxLength={2000} value={activityForm[key]} onChange={e=>setActivityForm({...activityForm,[key]:e.target.value})} className="mt-1 w-full rounded-lg border p-2 text-sm bg-white"/></label>)}<label className="block text-xs font-semibold text-gray-600">Delivery status *<select aria-label="Delivery status" value={activityForm.delivery_status} onChange={e=>setActivityForm({...activityForm,delivery_status:e.target.value})} className="mt-1 w-full rounded-lg border p-2 bg-white">{["Not ordered","Pending","Partly delivered","Delivered","Issue","Unknown"].map(status=><option key={status}>{status}</option>)}</select></label><label className="block text-xs font-semibold text-gray-600">Next follow-up date and time (Ghana)<input type="datetime-local" min={new Date().toISOString().slice(0,16)} value={activityForm.next_followup_at} onChange={e=>setActivityForm({...activityForm,next_followup_at:e.target.value})} className="mt-1 w-full rounded-lg border p-2 bg-white"/></label></div>}<label className="block text-xs font-semibold text-gray-600">Title *<input maxLength={120} value={activityForm.title} onChange={e => setActivityForm({...activityForm,title:e.target.value})} placeholder="e.g. Customer visited the branch" className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label><label className="block text-xs font-semibold text-gray-600">Date and time (Ghana)<input type="datetime-local" max={new Date().toISOString().slice(0,16)} value={activityForm.occurredAt} onChange={e => setActivityForm({...activityForm,occurredAt:e.target.value})} className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm"/></label><label className="block text-xs font-semibold text-gray-600">Description *<textarea maxLength={2000} rows={5} value={activityForm.description} onChange={e => setActivityForm({...activityForm,description:e.target.value})} placeholder="Describe what happened and any action taken..." className="mt-1.5 w-full resize-none rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-600"/></label>{actionError&&<p className="text-sm font-medium text-red-600">{actionError}</p>}<div className="flex justify-end gap-3 pt-2"><button onClick={() => setShowActivityModal(false)} className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-600">Cancel</button><button disabled={savingActivity} onClick={() => void logManualActivity()} className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50">{savingActivity?"Saving…":"Log Activity"}</button></div></div></div></div>}

      {showFollowupModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.35)", backdropFilter: "blur(4px)" }}
          onClick={() => setShowFollowupModal(false)}>
          <div className="rounded-2xl p-6 w-full max-w-md" style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "#111827" }}>Schedule Follow-up</h3>
              <button onClick={() => setShowFollowupModal(false)} style={{ color: "#9CA3AF" }}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Customer</label>
                <div className="rounded-lg px-4 py-3 text-sm" style={{ background: "#F5F7FB", border: "1px solid #E5E7EB", color: "#374151" }}>{customer.name}</div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Date</label>
                  <input type="date" min={new Date().toISOString().slice(0,10)} value={followupForm.date} onChange={e => setFollowupForm(v => ({...v,date:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }} />
                </div>
                <div>
                  <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Time</label>
                  <input type="time" value={followupForm.time} onChange={e => setFollowupForm(v => ({...v,time:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }} />
                </div>
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Purpose</label>
                <select value={followupForm.purpose} onChange={e => setFollowupForm(v => ({...v,purpose:e.target.value}))} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                  <option>Ticket follow-up</option>
                  <option>Payment reminder</option>
                  <option>Delivery update</option>
                  <option>Customer satisfaction check</option>
                </select>
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Notes</label>
                <textarea rows={3} value={followupForm.notes} onChange={e => setFollowupForm(v => ({...v,notes:e.target.value}))} placeholder="Add context for this follow-up..." className="w-full rounded-lg px-3 py-2.5 text-sm outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }} />
              </div>
              <div className="flex gap-3 pt-1">
                <button onClick={() => setShowFollowupModal(false)} className="flex-1 py-2.5 rounded-xl text-sm" style={{ border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}>Cancel</button>
                <button onClick={() => void scheduleFollowup()} className="flex-1 py-2.5 rounded-xl text-sm" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 700 }}>Schedule Follow-up</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showReminderModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.35)", backdropFilter: "blur(4px)" }}
          onClick={() => setShowReminderModal(false)}>
          <div className="rounded-2xl p-6 w-full max-w-md" style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "#111827" }}>Send Reminder</h3>
              <button onClick={() => setShowReminderModal(false)} style={{ color: "#9CA3AF" }}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block mb-2 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Send via</label>
                <div className="flex gap-2">
                  {["SMS", "WhatsApp"].map((ch) => (
                    <button key={ch} onClick={() => setReminderChannel(ch.toLowerCase() as "sms" | "whatsapp")} className="flex-1 py-2.5 rounded-xl text-sm transition-all" style={{ background: reminderChannel === ch.toLowerCase() ? "#EFF6FF" : "#F5F7FB", color: reminderChannel === ch.toLowerCase() ? "#0B5FFF" : "#6B7280", border: `1.5px solid ${reminderChannel === ch.toLowerCase() ? "#0B5FFF" : "#E5E7EB"}`, fontWeight: reminderChannel === ch.toLowerCase() ? 700 : 400 }}>
                      {ch}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Reminder Type</label>
                <select className="w-full rounded-lg px-3 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                  <option>Payment Reminder</option>
                  <option>Appointment Reminder</option>
                  <option>Delivery Notification</option>
                  <option>Ticket Status Update</option>
                </select>
              </div>
              <div>
                <label className="block mb-1.5 text-sm" style={{ fontWeight: 600, color: "#374151" }}>Message</label>
                <textarea rows={4} value={reminderMessage} onChange={e => setReminderMessage(e.target.value)} className="w-full rounded-lg px-3 py-2.5 text-sm outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }} />
              </div>
              {actionError && <p className="rounded-lg bg-red-50 p-3 text-sm font-medium text-red-700">{actionError}</p>}
              <div className="flex gap-3 pt-1">
                <button onClick={() => setShowReminderModal(false)} className="flex-1 py-2.5 rounded-xl text-sm" style={{ border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}>Cancel</button>
                <button disabled={sendingReminder || !reminderMessage.trim()} onClick={() => void sendReminder()} className="flex-1 py-2.5 rounded-xl text-sm flex items-center justify-center gap-2 disabled:opacity-60" style={{ background: "#F59E0B", color: "#FFF", fontWeight: 700 }}>
                  {sendingReminder ? <RefreshCw size={14} className="animate-spin" /> : <Send size={14} />} {sendingReminder ? "Sending..." : "Send Reminder"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {reminderSuccess && <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"><div className="w-full max-w-sm rounded-xl bg-white p-6 text-center shadow-2xl"><div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-50"><CheckCircle2 size={24} className="text-green-600" /></div><h3 className="mt-4 text-lg font-bold text-gray-900">Reminder sent</h3><p className="mt-2 text-sm text-gray-600">{reminderSuccess.message}</p><button onClick={() => { const redirect = reminderSuccess.redirect; setReminderSuccess(null); if (redirect) window.location.href = redirect; }} className="mt-5 w-full rounded-lg bg-blue-600 py-2.5 text-sm font-bold text-white">{reminderSuccess.redirect ? "Continue to WhatsApp" : "Done"}</button></div></div>}
    </div>
  );
}
