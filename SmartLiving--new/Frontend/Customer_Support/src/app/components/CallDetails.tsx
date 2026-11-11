import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useState } from "react";
import { useTheme } from "./ThemeContext";
import {
  ChevronLeft, ChevronRight, Phone, PhoneIncoming, PhoneOutgoing, PhoneMissed,
  Clock, Calendar, User, Building2, Mic, Play, Pause, Volume2, Download,
  CheckCircle2, AlertTriangle, MessageSquare, FileText, Tag, Plus,
  MoreHorizontal, Edit2, Send, X, CheckSquare, ArrowUpRight,
  Ticket, MapPin, Star, RefreshCw, Mail, Copy, Flag, Activity,
  Hash, Bookmark, VolumeX, SkipBack, SkipForward,
} from "lucide-react";
import { TYPE_CFG, OUTCOME_CFG } from "./CallsPage";

type Call = SupportAttribution & {
  id: string; type: string; customer: string; customerId: string;
  phone: string; officer: string; officerInitials: string; department: string;
  purpose: string; duration: string; durationSecs: number; outcome: string;
  followUp: boolean; followUpDate: string; date: string; time: string;
  recorded: boolean; linkedTicket: string; linkedTask: string;
  branch: string; notes: string;
};

/* ─── Helpers ─────────────────────────────────────────────────────── */
const AUDIT_EVENTS = [
  { icon: PhoneIncoming, color: "#15803D", bg: "#DCFCE7",  label: "Call logged",           detail: "Inbound call from customer received and logged automatically", time: "9:10 AM" },
  { icon: User,          color: "#1D4ED8", bg: "#DBEAFE",  label: "Call answered",          detail: "Call answered by Siti Rahimah (KL Central)", time: "9:10 AM" },
  { icon: Mic,           color: "#7C3AED", bg: "#EDE9FE",  label: "Recording started",      detail: "Call recording initiated automatically (consented)", time: "9:10 AM" },
  { icon: MessageSquare, color: "#0891B2", bg: "#CFFAFE",  label: "Notes added",            detail: "Call notes added by Siti Rahimah after call ended", time: "9:26 AM" },
  { icon: Ticket,        color: "#0B5FFF", bg: "#DBEAFE",  label: "Ticket linked",          detail: "Linked to existing ticket TK-4819 (double billing complaint)", time: "9:27 AM" },
  { icon: CheckSquare,   color: "#D97706", bg: "#FEF3C7",  label: "Follow-up scheduled",   detail: "Follow-up call set for 22 Jun 2026 at 10:00 AM", time: "9:28 AM" },
  { icon: Flag,          color: "#B45309", bg: "#FEF3C7",  label: "Outcome set",            detail: "Outcome marked as Pending — awaiting finance team response", time: "9:28 AM" },
];

const FOLLOW_UP_TASKS = [
  { id: "TSK-341", title: "Verify double billing refund with finance team", due: "22 Jun 2026, 10:00 AM", priority: "High",   status: "Pending",    assignee: "Rashid Halim" },
  { id: "TSK-342", title: "Callback to customer with refund confirmation",  due: "22 Jun 2026, 11:00 AM", priority: "High",   status: "Not Started", assignee: "Siti Rahimah" },
];

const RELATED_CALLS = [
  { id: "CALL-8810", type: "Outbound", purpose: "Post-resolution check",       duration: "2m 44s", outcome: "Satisfied",  date: "18 Jun 2026", officer: "Siti R." },
  { id: "CALL-8800", type: "Inbound",  purpose: "Initial billing complaint",   duration: "11m 02s", outcome: "Escalated", date: "01 Jun 2026", officer: "Zainab O." },
];

function PRIORITY_BADGE(p: string) {
  const c: Record<string, { t: string; bg: string }> = { High: { t: "#C2410C", bg: "#FFEDD5" }, Medium: { t: "#B45309", bg: "#FEF3C7" }, Low: { t: "#15803D", bg: "#DCFCE7" } };
  const cfg = c[p] ?? c.Medium;
  return <span className="px-2 py-0.5 rounded-full text-xs" style={{ background: cfg.bg, color: cfg.t, fontWeight: 700 }}>{p}</span>;
}

function TASK_STATUS_BADGE(s: string) {
  const c: Record<string, { t: string; bg: string }> = { "Pending": { t: "#B45309", bg: "#FEF3C7" }, "Not Started": { t: "#6B7280", bg: "#F3F4F6" }, "In Progress": { t: "#1D4ED8", bg: "#DBEAFE" }, "Completed": { t: "#15803D", bg: "#DCFCE7" } };
  const cfg = c[s] ?? c["Not Started"];
  return <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: cfg.bg, color: cfg.t, fontWeight: 700 }}>{s}</span>;
}

/* ─── Audio Player ───────────────────────────────────────────────── */
function AudioPlayer({ duration }: { duration: string }) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(80);

  const bars = Array.from({ length: 80 }, (_, i) => ({
    h: 8 + Math.sin(i * 0.4) * 6 + Math.sin(i * 1.2) * 4 + (Math.random() < 0.3 ? 12 : 0),
  }));

  const handlePlayToggle = () => {
    const next = !playing;
    setPlaying(next);
    if (next) {
      const interval = setInterval(() => {
        setProgress(p => {
          if (p >= 100) { clearInterval(interval); setPlaying(false); return 0; }
          return p + 0.3;
        });
      }, 50);
    }
  };

  return (
    <div className="rounded-2xl p-5" style={{ background: "linear-gradient(135deg,#0B1929,#0B3060)", border: "1px solid rgba(255,255,255,0.08)" }}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <p style={{ fontSize: "0.8rem", fontWeight: 700, color: "#E2E8F0" }}>Call Recording</p>
          <p style={{ fontSize: "0.7rem", color: "#64748B", marginTop: "2px" }}>CALL-8817 · {duration}</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ background: "rgba(255,255,255,0.08)", color: "#94A3B8", border: "1px solid rgba(255,255,255,0.1)" }}>
            <Download size={12} /> Download
          </button>
          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ background: "rgba(255,255,255,0.08)", color: "#94A3B8", border: "1px solid rgba(255,255,255,0.1)" }}>
            <Copy size={12} /> Share
          </button>
        </div>
      </div>

      {/* Waveform */}
      <div className="flex items-center gap-px mb-4 cursor-pointer" style={{ height: "52px" }}
        onClick={e => { const rect = (e.currentTarget as HTMLElement).getBoundingClientRect(); setProgress(((e.clientX - rect.left) / rect.width) * 100); }}>
        {bars.map((bar, i) => (
          <div key={i} className="rounded-full transition-all" style={{ width: "2.5px", height: `${bar.h}px`, background: i < (bars.length * progress / 100) ? "#60A5FA" : "rgba(255,255,255,0.15)", minHeight: "4px" }} />
        ))}
      </div>

      {/* Progress */}
      <div className="mb-4">
        <div className="h-1 rounded-full cursor-pointer" style={{ background: "rgba(255,255,255,0.1)" }}
          onClick={e => { const rect = (e.currentTarget as HTMLElement).getBoundingClientRect(); setProgress(((e.clientX - rect.left) / rect.width) * 100); }}>
          <div className="h-full rounded-full transition-all" style={{ width: `${progress}%`, background: "linear-gradient(90deg,#3B82F6,#60A5FA)" }} />
        </div>
        <div className="flex justify-between mt-1">
          <span style={{ fontSize: "0.65rem", color: "#64748B" }}>
            {Math.floor((progress / 100) * 930 / 60)}:{String(Math.round((progress / 100) * 930 % 60)).padStart(2, "0")}
          </span>
          <span style={{ fontSize: "0.65rem", color: "#64748B" }}>{duration}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button className="w-8 h-8 rounded-full flex items-center justify-center" style={{ color: "#64748B" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#E2E8F0"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "#64748B"}>
            <SkipBack size={16} />
          </button>
          <button onClick={handlePlayToggle}
            className="w-11 h-11 rounded-full flex items-center justify-center transition-all"
            style={{ background: "#3B82F6", boxShadow: "0 4px 16px rgba(59,130,246,0.4)" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#2563EB"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#3B82F6"}>
            {playing ? <Pause size={18} style={{ color: "#FFF" }} /> : <Play size={18} style={{ color: "#FFF", marginLeft: "2px" }} />}
          </button>
          <button className="w-8 h-8 rounded-full flex items-center justify-center" style={{ color: "#64748B" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#E2E8F0"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "#64748B"}>
            <SkipForward size={16} />
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button onClick={() => setMuted(!muted)} className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ color: "#64748B" }}>
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          <div className="relative w-20 h-1.5 rounded-full cursor-pointer" style={{ background: "rgba(255,255,255,0.1)" }}
            onClick={e => { const rect = (e.currentTarget as HTMLElement).getBoundingClientRect(); setVolume(Math.round(((e.clientX - rect.left) / rect.width) * 100)); }}>
            <div className="h-full rounded-full" style={{ width: `${muted ? 0 : volume}%`, background: "#3B82F6" }} />
          </div>
          <span style={{ fontSize: "0.65rem", color: "#64748B", minWidth: "28px" }}>{muted ? 0 : volume}%</span>
        </div>

        <div className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg" style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)" }}>
          <span style={{ fontSize: "0.68rem", color: "#64748B" }}>Speed:</span>
          {["0.75×", "1×", "1.25×", "1.5×"].map(s => (
            <button key={s} className="px-1.5 py-0.5 rounded text-xs transition-all" style={{ background: s === "1×" ? "rgba(59,130,246,0.3)" : "transparent", color: s === "1×" ? "#93C5FD" : "#64748B", fontWeight: s === "1×" ? 700 : 400 }}>
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── Main Component ─────────────────────────────────────────────── */
export function CallDetails({ call, onBack }: { call: Call; onBack: () => void }) {
  const { t } = useTheme();
  const [noteText, setNoteText] = useState(call.notes);
  const [editingNotes, setEditingNotes] = useState(false);
  const [activeTab, setActiveTab] = useState<"details" | "tasks" | "related" | "audit">("details");
  const [showFollowUpModal, setShowFollowUpModal] = useState(false);

  const tc  = TYPE_CFG[call.type] ?? TYPE_CFG.Inbound;
  const oc  = OUTCOME_CFG[call.outcome] ?? { text: "#6B7280", bg: "#F3F4F6" };
  const TIcon = tc.icon;
  const initials = (n: string) => n === "—" ? "?" : n.split(" ").map(w => w[0]).slice(0, 2).join("");
  const isMissed = call.type === "Missed";

  return (
    <div className="flex flex-col" style={{ height: "100%", background: t.pageBg, fontFamily: "var(--font-family-body)" }}>

      {/* ════════════ HEADER ════════════ */}
      <div style={{ background: "#FFFFFF", borderBottom: "1px solid #E8ECEF", flexShrink: 0 }}>

        {/* Breadcrumb */}
        <div className="flex items-center gap-2 px-6 py-2.5" style={{ borderBottom: "1px solid #F5F7FB" }}>
          <button onClick={onBack} className="flex items-center gap-1.5 text-sm transition-colors" style={{ color: "#6B7280", fontWeight: 500 }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#0B5FFF"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "#6B7280"}>
            <ChevronLeft size={15} /> Call Log
          </button>
          <ChevronRight size={13} style={{ color: "#D1D5DB" }} />
          <span style={{ fontSize: "0.8rem", color: "#9CA3AF" }}>Call Detail</span>
          <ChevronRight size={13} style={{ color: "#D1D5DB" }} />
          <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#374151", fontFamily: "monospace" }}>{call.id}</span>
          <div className="flex-1" />
          <div className="flex items-center gap-1.5">
            {[ChevronLeft, ChevronRight].map((Icon, i) => (
              <button key={i} className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ border: "1px solid #E5E7EB", color: "#6B7280" }}
                onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F5F7FB"}
                onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
                <Icon size={13} />
              </button>
            ))}
            <div className="w-px h-4 mx-1" style={{ background: "#E5E7EB" }} />
            <button className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ border: "1px solid #E5E7EB", color: "#6B7280" }}>
              <MoreHorizontal size={15} />
            </button>
          </div>
        </div>

        {/* Hero strip */}
        <div className="px-6 py-5">
          <div className="flex items-start justify-between gap-6">
            <div className="flex items-start gap-5 flex-1 min-w-0">
              {/* Type icon */}
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center flex-shrink-0" style={{ background: tc.bg, border: `2px solid ${tc.border}`, boxShadow: "0 4px 16px rgba(0,0,0,0.08)" }}>
                <TIcon size={24} style={{ color: tc.text }} />
              </div>

              <div className="flex-1 min-w-0">
                {/* Badges */}
                <div className="flex items-center gap-2.5 mb-2 flex-wrap">
                  <span style={{ fontSize: "0.78rem", fontFamily: "monospace", fontWeight: 800, color: "#0B5FFF", background: "#EFF6FF", padding: "2px 8px", borderRadius: "6px", border: "1px solid #BFDBFE" }}>{call.id}</span>
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full" style={{ background: tc.bg, border: `1.5px solid ${tc.border}` }}>
                    <TIcon size={11} style={{ color: tc.text }} />
                    <span style={{ fontSize: "0.72rem", fontWeight: 800, color: tc.text }}>{call.type} Call</span>
                  </div>
                  <span className="px-2.5 py-1 rounded-full" style={{ fontSize: "0.72rem", fontWeight: 700, color: oc.text, background: oc.bg }}>{call.outcome}</span>
                  {call.recorded && (
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full" style={{ background: "#EDE9FE", border: "1px solid #C4B5FD" }}>
                      <Mic size={10} style={{ color: "#7C3AED" }} />
                      <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "#7C3AED" }}>Recorded</span>
                    </div>
                  )}
                  {call.followUp && (
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full" style={{ background: "#FEF3C7", border: "1px solid #FCD34D" }}>
                      <AlertTriangle size={10} style={{ color: "#D97706" }} />
                      <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "#B45309" }}>Follow-up Due {call.followUpDate}</span>
                    </div>
                  )}
                </div>

                {/* Purpose */}
                <h1 style={{ fontSize: "1.15rem", fontWeight: 800, color: "#111827", lineHeight: 1.35, marginBottom: "10px" }}>{call.purpose}</h1><RecordAttribution record={call}/>

                {/* Meta row */}
                <div className="flex items-center gap-2 flex-wrap">
                  {[
                    { icon: User, value: call.customer !== "Unknown Caller" ? call.customer : "Unknown Caller" },
                    { icon: Phone, value: call.phone },
                    { icon: User, value: call.officer !== "—" ? `Agent: ${call.officer}` : "Unassigned" },
                    { icon: Building2, value: `${call.branch} Branch` },
                    { icon: Calendar, value: `${call.date} · ${call.time}` },
                    { icon: Clock, value: call.duration !== "—" ? call.duration : "—" },
                  ].map((item, i) => (
                    <div key={i} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl" style={{ background: "#F8FAFC", border: "1px solid #E8ECEF" }}>
                      <item.icon size={12} style={{ color: "#9CA3AF" }} />
                      <span style={{ fontSize: "0.78rem", color: "#6B7280" }}>{item.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Quick actions */}
            <div className="flex items-center gap-2 flex-shrink-0 pt-1">
              <button className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
                style={{ background: "#DCFCE7", color: "#15803D", border: "1.5px solid #86EFAC", fontWeight: 700 }}
                onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#BBF7D0"}
                onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#DCFCE7"}>
                <Phone size={14} /> Call Back
              </button>
              <button className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
                style={{ background: "#DBEAFE", color: "#1D4ED8", border: "1.5px solid #93C5FD", fontWeight: 700 }}
                onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#BFDBFE"}
                onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "#DBEAFE"}>
                <Ticket size={14} /> Create Ticket
              </button>
              <button onClick={() => setShowFollowUpModal(true)} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm transition-all"
                style={{ background: "#FFFFFF", color: "#374151", border: "1.5px solid #E5E7EB", fontWeight: 600 }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = "#0B5FFF"; (e.currentTarget as HTMLElement).style.color = "#0B5FFF"; }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = "#E5E7EB"; (e.currentTarget as HTMLElement).style.color = "#374151"; }}>
                <CheckSquare size={14} /> Schedule Follow-up
              </button>
              <button className="p-2.5 rounded-xl" style={{ border: "1.5px solid #E5E7EB", color: "#9CA3AF" }}>
                <MoreHorizontal size={16} />
              </button>
            </div>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex items-center px-6 gap-0" style={{ borderTop: "1px solid #F3F4F6" }}>
          {(["details", "tasks", "related", "audit"] as const).map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className="px-5 py-3 text-sm capitalize transition-all"
              style={{ borderBottom: activeTab === tab ? "2.5px solid #0B5FFF" : "2.5px solid transparent", color: activeTab === tab ? "#0B5FFF" : "#9CA3AF", fontWeight: activeTab === tab ? 700 : 400, marginBottom: "-1px", whiteSpace: "nowrap" }}>
              {tab === "audit" ? "Audit Trail" : tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* ════════════ BODY ════════════ */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* ── Main Content ── */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4 min-w-0">

          {/* ── Details Tab ── */}
          {activeTab === "details" && (
            <>
              {/* Recording Player */}
              {call.recorded && <AudioPlayer duration={call.duration} />}

              {/* No recording notice */}
              {!call.recorded && (
                <div className="rounded-2xl px-5 py-4 flex items-center gap-3" style={{ background: "#F8FAFC", border: "1px solid #E8ECEF" }}>
                  <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: "#F3F4F6" }}>
                    <Mic size={16} style={{ color: "#9CA3AF" }} />
                  </div>
                  <div>
                    <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "#374151" }}>No Recording Available</p>
                    <p style={{ fontSize: "0.75rem", color: "#9CA3AF", marginTop: "2px" }}>
                      {isMissed ? "Call was not answered — no recording captured." : "This call was not recorded. Recording may be disabled for this channel."}
                    </p>
                  </div>
                </div>
              )}

              {/* Call Details Card */}
              <div className="rounded-2xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 6px rgba(0,0,0,0.05)", borderTop: "3px solid #0B5FFF" }}>
                <div className="flex items-center gap-3 px-5 py-4" style={{ borderBottom: "1px solid #F3F4F6" }}>
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "#EFF6FF" }}>
                    <PhoneCall size={15} style={{ color: "#0B5FFF" }} />
                  </div>
                  <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Call Information</h3>
                </div>
                <div className="px-5 py-4">
                  <div className="grid grid-cols-2 gap-x-10">
                    <div>
                      {[
                        { label: "Call ID",     value: call.id,         mono: true },
                        { label: "Call Type",   value: call.type },
                        { label: "Direction",   value: call.type === "Outbound" ? "Outbound → Customer" : call.type === "Inbound" ? "Customer → Agent" : "Incoming (Missed)" },
                        { label: "Date",        value: call.date },
                        { label: "Time",        value: call.time },
                        { label: "Duration",    value: call.duration !== "—" ? call.duration : "N/A (missed)" },
                      ].map(row => (
                        <div key={row.label} className="flex items-start gap-3 py-2.5" style={{ borderBottom: "1px solid #F9FAFB" }}>
                          <span style={{ fontSize: "0.75rem", color: "#9CA3AF", flexShrink: 0, width: "100px", paddingTop: "2px" }}>{row.label}</span>
                          <span style={{ fontSize: "0.8125rem", color: "#1F2937", fontWeight: 500, fontFamily: row.mono ? "monospace" : "inherit" }}>{row.value}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      {[
                        { label: "Department",    value: call.department || "—" },
                        { label: "Branch",        value: call.branch },
                        { label: "Outcome",       value: <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: oc.bg, color: oc.text, fontWeight: 700 }}>{call.outcome}</span> },
                        { label: "Recorded",      value: call.recorded ? <span className="flex items-center gap-1.5"><Mic size={12} style={{ color: "#7C3AED" }} /><span style={{ color: "#7C3AED", fontWeight: 600 }}>Yes — available</span></span> : <span style={{ color: "#9CA3AF" }}>Not recorded</span> },
                        { label: "Linked Ticket", value: call.linkedTicket ? <span style={{ fontSize: "0.8125rem", fontWeight: 700, color: "#0B5FFF", fontFamily: "monospace" }}>{call.linkedTicket}</span> : <span style={{ color: "#9CA3AF" }}>None</span> },
                        { label: "Follow-up",     value: call.followUp ? <span style={{ color: "#D97706", fontWeight: 700 }}>Required · {call.followUpDate}</span> : <span style={{ color: "#9CA3AF" }}>Not required</span> },
                      ].map(row => (
                        <div key={row.label} className="flex items-start gap-3 py-2.5" style={{ borderBottom: "1px solid #F9FAFB" }}>
                          <span style={{ fontSize: "0.75rem", color: "#9CA3AF", flexShrink: 0, width: "110px", paddingTop: "2px" }}>{row.label}</span>
                          {typeof row.value === "string" ? <span style={{ fontSize: "0.8125rem", color: "#1F2937", fontWeight: 500 }}>{row.value}</span> : row.value}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Officer Info */}
              {call.officer !== "—" && (
                <div className="rounded-2xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 6px rgba(0,0,0,0.05)", borderTop: "3px solid #7C3AED" }}>
                  <div className="flex items-center gap-3 px-5 py-4" style={{ borderBottom: "1px solid #F3F4F6" }}>
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "#EDE9FE" }}>
                      <Mic size={15} style={{ color: "#7C3AED" }} />
                    </div>
                    <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Handling Officer</h3>
                  </div>
                  <div className="px-5 py-4">
                    <div className="flex items-center gap-4">
                      <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-white" style={{ background: "#7C3AED", fontSize: "0.75rem", fontWeight: 700, boxShadow: "0 4px 12px rgba(124,58,237,0.3)" }}>
                        {call.officerInitials}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p style={{ fontSize: "1rem", fontWeight: 700, color: "#111827" }}>{call.officer}</p>
                          <div className="flex items-center gap-1">
                            <div className="w-2 h-2 rounded-full bg-green-500" />
                            <span style={{ fontSize: "0.7rem", color: "#16A34A", fontWeight: 600 }}>Online</span>
                          </div>
                        </div>
                        <p style={{ fontSize: "0.78rem", color: "#9CA3AF", marginTop: "2px" }}>{call.department} · {call.branch} Branch</p>
                      </div>
                      <button className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm" style={{ background: "#EDE9FE", color: "#7C3AED", fontWeight: 600, border: "1px solid #C4B5FD" }}>
                        <Mail size={13} /> Message
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Call Notes */}
              <div className="rounded-2xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 6px rgba(0,0,0,0.05)", borderTop: "3px solid #0891B2" }}>
                <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid #F3F4F6" }}>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "#CFFAFE" }}>
                      <MessageSquare size={15} style={{ color: "#0891B2" }} />
                    </div>
                    <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Call Notes & Summary</h3>
                  </div>
                  <button onClick={() => setEditingNotes(e => !e)}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-all"
                    style={{ background: editingNotes ? "#CFFAFE" : "#F5F7FB", color: editingNotes ? "#0891B2" : "#6B7280", border: `1px solid ${editingNotes ? "#A5F3FC" : "#E5E7EB"}`, fontWeight: 600 }}>
                    {editingNotes ? <><CheckCircle2 size={11} /> Save</> : <><Edit2 size={11} /> Edit</>}
                  </button>
                </div>
                <div className="px-5 py-4">
                  {editingNotes ? (
                    <textarea value={noteText} onChange={e => setNoteText(e.target.value)} rows={5}
                      className="w-full rounded-xl px-4 py-3 text-sm outline-none resize-none"
                      style={{ border: "1.5px solid #0891B2", background: "#F0FEFF", color: "#374151", lineHeight: 1.7 }} />
                  ) : (
                    <div className="rounded-xl px-4 py-4" style={{ background: "#F8FAFC", border: "1px solid #E8ECEF" }}>
                      <p style={{ fontSize: "0.875rem", color: "#374151", lineHeight: 1.75 }}>
                        {noteText || <span style={{ color: "#9CA3AF", fontStyle: "italic" }}>No notes recorded for this call.</span>}
                      </p>
                    </div>
                  )}
                  <div className="flex items-center justify-between mt-3 px-1">
                    <span style={{ fontSize: "0.7rem", color: "#9CA3AF" }}>Last edited by {call.officer !== "—" ? call.officer : "System"}</span>
                    <span style={{ fontSize: "0.7rem", color: "#9CA3AF" }}>{call.date} · {call.time}</span>
                  </div>
                </div>
              </div>
            </>
          )}

          {/* ── Tasks Tab ── */}
          {activeTab === "tasks" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Follow-up Tasks</h3>
                <button className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 700 }}>
                  <Plus size={13} /> Add Task
                </button>
              </div>
              {FOLLOW_UP_TASKS.map(task => (
                <div key={task.id} className="rounded-2xl p-5 flex items-center gap-4 cursor-pointer transition-all"
                  style={{ background: "#FFFFFF", border: "1px solid #E8ECEF", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = "#BFDBFE"; (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 16px rgba(11,95,255,0.07)"; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = "#E8ECEF"; (e.currentTarget as HTMLElement).style.boxShadow = "0 1px 4px rgba(0,0,0,0.04)"; }}>
                  <div className="w-4 h-4 rounded border-2 flex-shrink-0" style={{ borderColor: "#D97706" }} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span style={{ fontSize: "0.68rem", fontFamily: "monospace", color: "#9CA3AF" }}>{task.id}</span>
                      {PRIORITY_BADGE(task.priority)}
                    </div>
                    <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "#374151" }}>{task.title}</p>
                    <div className="flex items-center gap-4 mt-2">
                      <div className="flex items-center gap-1"><Calendar size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>Due: {task.due}</span></div>
                      <div className="flex items-center gap-1"><User size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{task.assignee}</span></div>
                    </div>
                  </div>
                  {TASK_STATUS_BADGE(task.status)}
                </div>
              ))}
              {call.followUp && (
                <div className="rounded-2xl px-5 py-4" style={{ background: "#FFFBEB", border: "1.5px solid #FDE68A" }}>
                  <div className="flex items-center gap-3">
                    <AlertTriangle size={16} style={{ color: "#D97706", flexShrink: 0 }} />
                    <div>
                      <p style={{ fontSize: "0.875rem", fontWeight: 700, color: "#92400E" }}>Follow-up Required</p>
                      <p style={{ fontSize: "0.78rem", color: "#B45309", marginTop: "2px" }}>Due date: {call.followUpDate} · Make sure to call back and confirm resolution.</p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── Related Calls Tab ── */}
          {activeTab === "related" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Related Calls — {call.customer}</h3>
              </div>
              {RELATED_CALLS.map(rc => {
                const rtc = TYPE_CFG[rc.type] ?? TYPE_CFG.Inbound;
                const roc = OUTCOME_CFG[rc.outcome] ?? { text: "#6B7280", bg: "#F3F4F6" };
                const RIcon = rtc.icon;
                return (
                  <div key={rc.id} className="flex items-center gap-5 rounded-2xl p-5 cursor-pointer transition-all"
                    style={{ background: "#FFFFFF", border: "1px solid #E8ECEF" }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = "#BFDBFE"; (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 12px rgba(11,95,255,0.07)"; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = "#E8ECEF"; (e.currentTarget as HTMLElement).style.boxShadow = "none"; }}>
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: rtc.bg, border: `1.5px solid ${rtc.border}` }}>
                      <RIcon size={18} style={{ color: rtc.text }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span style={{ fontSize: "0.8rem", fontWeight: 800, color: "#0B5FFF", fontFamily: "monospace" }}>{rc.id}</span>
                        <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: rtc.bg, color: rtc.text, fontWeight: 700 }}>{rc.type}</span>
                      </div>
                      <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "#374151" }}>{rc.purpose}</p>
                      <div className="flex items-center gap-4 mt-1.5">
                        <div className="flex items-center gap-1"><Clock size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{rc.duration}</span></div>
                        <div className="flex items-center gap-1"><Calendar size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{rc.date}</span></div>
                        <div className="flex items-center gap-1"><User size={11} style={{ color: "#9CA3AF" }} /><span style={{ fontSize: "0.72rem", color: "#6B7280" }}>{rc.officer}</span></div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <span className="px-2.5 py-1 rounded-full text-xs" style={{ background: roc.bg, color: roc.text, fontWeight: 700 }}>{rc.outcome}</span>
                      <ArrowUpRight size={16} style={{ color: "#9CA3AF" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* ── Audit Trail Tab ── */}
          {activeTab === "audit" && (
            <div className="rounded-2xl overflow-hidden" style={{ background: "#FFFFFF", border: "1px solid #E8ECEF" }}>
              <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid #F3F4F6" }}>
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "#EDE9FE" }}>
                    <Activity size={15} style={{ color: "#7C3AED" }} />
                  </div>
                  <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#111827" }}>Audit Trail</h3>
                </div>
                <span style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>{AUDIT_EVENTS.length} events</span>
              </div>
              <div className="px-5 py-4">
                <div className="relative">
                  <div className="absolute left-4 top-3 bottom-3 w-px" style={{ background: "#F3F4F6" }} />
                  {AUDIT_EVENTS.map((ev, i) => (
                    <div key={i} className="relative flex items-start gap-4 py-3.5 rounded-xl px-2 cursor-pointer transition-all"
                      style={{ borderBottom: i < AUDIT_EVENTS.length - 1 ? "1px solid #F9FAFB" : "none" }}
                      onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "#F8FAFC"}
                      onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
                      <div className="relative z-10 w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: ev.bg, border: `1.5px solid ${ev.color}30` }}>
                        <ev.icon size={14} style={{ color: ev.color }} />
                      </div>
                      <div className="flex-1 min-w-0 pt-0.5">
                        <div className="flex items-start justify-between gap-4">
                          <div>
                            <p style={{ fontSize: "0.83rem", fontWeight: 700, color: "#111827" }}>{ev.label}</p>
                            <p style={{ fontSize: "0.78rem", color: "#6B7280", marginTop: "3px", lineHeight: 1.5 }}>{ev.detail}</p>
                          </div>
                          <span style={{ fontSize: "0.7rem", color: "#C4C4C4", flexShrink: 0, whiteSpace: "nowrap" }}>{call.date} · {ev.time}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="h-6" />
        </div>

        {/* ── RIGHT SIDEBAR ── */}
        <div className="flex-shrink-0 overflow-y-auto p-4 space-y-4" style={{ width: "280px", borderLeft: "1px solid #E8ECEF", background: "#FFFFFF" }}>

          {/* Call Stats */}
          <div className="rounded-2xl p-4" style={{ background: "linear-gradient(135deg,#EFF6FF,#F5F3FF)", border: "1px solid #BFDBFE" }}>
            <p style={{ fontSize: "0.72rem", fontWeight: 700, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: "12px" }}>Call Summary</p>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: "Duration",   value: call.duration !== "—" ? call.duration : "—",  color: "#0B5FFF" },
                { label: "Type",       value: call.type,   color: tc.text },
                { label: "Outcome",    value: call.outcome, color: oc.text },
                { label: "Recorded",   value: call.recorded ? "Yes" : "No", color: call.recorded ? "#7C3AED" : "#9CA3AF" },
              ].map(s => (
                <div key={s.label} className="rounded-xl p-3 text-center" style={{ background: "rgba(255,255,255,0.7)" }}>
                  <p style={{ fontSize: "0.875rem", fontWeight: 800, color: s.color }}>{s.value}</p>
                  <p style={{ fontSize: "0.62rem", color: "#9CA3AF", marginTop: "3px", textTransform: "uppercase", letterSpacing: "0.04em" }}>{s.label}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Customer Card */}
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid #E8ECEF" }}>
            <div className="flex items-center justify-between px-4 py-3.5" style={{ borderBottom: "1px solid #F3F4F6", background: "#FAFBFC" }}>
              <p style={{ fontSize: "0.78rem", fontWeight: 700, color: "#374151" }}>Customer</p>
              <button style={{ fontSize: "0.72rem", color: "#0B5FFF", fontWeight: 600 }}>View Profile →</button>
            </div>
            <div className="p-4">
              {call.customer !== "Unknown Caller" ? (
                <>
                  <div className="flex items-center gap-3 mb-3 pb-3" style={{ borderBottom: "1px solid #F3F4F6" }}>
                    <div className="w-10 h-10 rounded-full flex items-center justify-center text-white" style={{ background: "#1D4ED8", fontSize: "0.65rem", fontWeight: 700 }}>
                      {initials(call.customer)}
                    </div>
                    <div>
                      <p style={{ fontSize: "0.875rem", fontWeight: 700, color: "#111827" }}>{call.customer}</p>
                      <p style={{ fontSize: "0.7rem", color: "#9CA3AF", fontFamily: "monospace" }}>{call.customerId}</p>
                    </div>
                  </div>
                  {[
                    { icon: Phone,    value: call.phone },
                    { icon: Building2,value: `${call.branch} Branch` },
                    { icon: MapPin,   value: "KL Central" },
                  ].map((row, i) => (
                    <div key={i} className="flex items-center gap-2 mb-2">
                      <row.icon size={12} style={{ color: "#C4C4C4" }} />
                      <span style={{ fontSize: "0.78rem", color: "#374151" }}>{row.value}</span>
                    </div>
                  ))}
                  <div className="grid grid-cols-2 gap-2 mt-3">
                    <button className="py-2 rounded-xl text-xs flex items-center justify-center gap-1.5" style={{ background: "#F0FDF4", color: "#16A34A", border: "1px solid #BBF7D0", fontWeight: 600 }}>
                      <Phone size={11} /> Call
                    </button>
                    <button className="py-2 rounded-xl text-xs flex items-center justify-center gap-1.5" style={{ background: "#EFF6FF", color: "#0B5FFF", border: "1px solid #BFDBFE", fontWeight: 600 }}>
                      <Ticket size={11} /> Tickets
                    </button>
                  </div>
                </>
              ) : (
                <div className="text-center py-3">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-2" style={{ background: "#F3F4F6" }}>
                    <User size={18} style={{ color: "#9CA3AF" }} />
                  </div>
                  <p style={{ fontSize: "0.8rem", fontWeight: 500, color: "#374151" }}>Unknown Caller</p>
                  <p style={{ fontSize: "0.72rem", color: "#9CA3AF", marginTop: "2px" }}>{call.phone}</p>
                  <button className="mt-3 px-4 py-2 rounded-xl text-xs w-full" style={{ background: "#EFF6FF", color: "#0B5FFF", border: "1px solid #BFDBFE", fontWeight: 600 }}>
                    Search in CRM
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Linked Items */}
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid #E8ECEF" }}>
            <div className="flex items-center justify-between px-4 py-3.5" style={{ borderBottom: "1px solid #F3F4F6", background: "#FAFBFC" }}>
              <p style={{ fontSize: "0.78rem", fontWeight: 700, color: "#374151" }}>Linked Items</p>
              <button style={{ fontSize: "0.72rem", color: "#0B5FFF", fontWeight: 600 }}>+ Link</button>
            </div>
            <div className="p-3 space-y-2">
              {[
                call.linkedTicket && { label: "Ticket",  value: call.linkedTicket, color: "#0B5FFF", bg: "#EFF6FF" },
                call.linkedTask   && { label: "Task",    value: call.linkedTask,   color: "#D97706", bg: "#FEF3C7" },
                call.customerId !== "—" && { label: "Customer", value: call.customerId, color: "#7C3AED", bg: "#EDE9FE" },
              ].filter(Boolean).map((item: any) => (
                <div key={item.value} className="flex items-center justify-between rounded-xl px-3 py-2.5 cursor-pointer transition-all"
                  style={{ background: item.bg, border: `1px solid ${item.color}22` }}
                  onMouseEnter={e => (e.currentTarget as HTMLElement).style.opacity = "0.8"}
                  onMouseLeave={e => (e.currentTarget as HTMLElement).style.opacity = "1"}>
                  <div>
                    <p style={{ fontSize: "0.62rem", color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>{item.label}</p>
                    <p style={{ fontSize: "0.82rem", fontWeight: 800, color: item.color, fontFamily: "monospace" }}>{item.value}</p>
                  </div>
                  <ArrowUpRight size={14} style={{ color: item.color }} />
                </div>
              ))}
              {!call.linkedTicket && !call.linkedTask && call.customerId === "—" && (
                <p style={{ fontSize: "0.78rem", color: "#9CA3AF", textAlign: "center", padding: "8px" }}>No linked items</p>
              )}
            </div>
          </div>

          {/* Quick Note */}
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid #E8ECEF" }}>
            <div className="px-4 py-3.5" style={{ borderBottom: "1px solid #F3F4F6", background: "#FAFBFC" }}>
              <p style={{ fontSize: "0.78rem", fontWeight: 700, color: "#374151" }}>Quick Note</p>
            </div>
            <div className="p-4">
              <textarea rows={3} placeholder="Add a quick note..." className="w-full rounded-xl px-3 py-2.5 text-xs outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151", lineHeight: 1.6 }}
                onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }}
                onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              <button className="w-full mt-2 py-2 rounded-xl text-xs flex items-center justify-center gap-1.5" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 700 }}>
                <Send size={11} /> Save Note
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Follow-up Modal ── */}
      {showFollowUpModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.4)", backdropFilter: "blur(6px)" }}
          onClick={() => setShowFollowUpModal(false)}>
          <div className="rounded-2xl p-6 w-full max-w-md" style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 800, color: "#111827" }}>Schedule Follow-up</h3>
                <p style={{ fontSize: "0.75rem", color: "#9CA3AF", marginTop: "2px" }}>Linked to {call.id}</p>
              </div>
              <button onClick={() => setShowFollowUpModal(false)} style={{ color: "#9CA3AF" }}><X size={20} /></button>
            </div>
            <div className="space-y-4">
              <div className="rounded-xl px-4 py-3 flex items-center gap-3" style={{ background: "#F8FAFC", border: "1px solid #E8ECEF" }}>
                <div className="w-8 h-8 rounded-full flex items-center justify-center text-white" style={{ background: "#1D4ED8", fontSize: "0.6rem", fontWeight: 700 }}>{initials(call.customer)}</div>
                <div>
                  <p style={{ fontSize: "0.875rem", fontWeight: 700, color: "#111827" }}>{call.customer}</p>
                  <p style={{ fontSize: "0.72rem", color: "#9CA3AF" }}>{call.phone}</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Follow-up Date</label>
                  <input type="date" defaultValue="2026-06-22" className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}
                    onFocus={e => e.target.style.borderColor = "#0B5FFF"} onBlur={e => e.target.style.borderColor = "#E5E7EB"} />
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Time</label>
                  <input type="time" defaultValue="10:00" className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}
                    onFocus={e => e.target.style.borderColor = "#0B5FFF"} onBlur={e => e.target.style.borderColor = "#E5E7EB"} />
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Assign To</label>
                <select className="w-full rounded-xl px-3.5 py-2.5 text-sm outline-none" style={{ border: "1.5px solid #E5E7EB", color: "#374151" }}>
                  {["Siti Rahimah","Zainab Othman","Ahmad Faizal","Lee Chun Wei","Rashid Halim"].map(o => <option key={o}>{o}</option>)}
                </select>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>Notes</label>
                <textarea rows={3} placeholder="Context for the follow-up call..." className="w-full rounded-xl px-3.5 py-3 text-sm outline-none resize-none" style={{ border: "1.5px solid #E5E7EB", background: "#F9FAFB", color: "#374151" }}
                  onFocus={e => { e.target.style.borderColor = "#0B5FFF"; e.target.style.background = "#FFF"; }} onBlur={e => { e.target.style.borderColor = "#E5E7EB"; e.target.style.background = "#F9FAFB"; }} />
              </div>
              <div className="flex gap-3">
                <button onClick={() => setShowFollowUpModal(false)} className="flex-1 py-2.5 rounded-xl text-sm" style={{ border: "1px solid #E5E7EB", color: "#374151", fontWeight: 500 }}>Cancel</button>
                <button onClick={() => setShowFollowUpModal(false)} className="flex-1 py-2.5 rounded-xl text-sm flex items-center justify-center gap-2" style={{ background: "#0B5FFF", color: "#FFF", fontWeight: 700 }}>
                  <CheckCircle2 size={14} /> Schedule
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
