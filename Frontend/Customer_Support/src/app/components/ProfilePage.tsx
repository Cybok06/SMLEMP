import { useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  UserCircle2,
  Mail,
  Phone,
  Building2,
  MapPin,
  BadgeCheck,
  KeyRound,
  Eye,
  EyeOff,
  CheckCircle2,
  Clock3,
  MonitorSmartphone,
  Globe,
  LockKeyhole,
  ChevronRight,
} from "lucide-react";
import { useSupportIdentity } from "./SupportIdentityContext";
import { useTheme } from "./ThemeContext";

type TabKey = "overview" | "password" | "logs" | "phone_logs";
type PhoneLog = { id: string; customer: string; phone: string; type: string; fromNumber: string; deviceName: string; date: string; time: string; duration: string; enrichmentStatus: string; customerMatch: string };

export function ProfilePage() {
  const { t, isDark } = useTheme();
  const { profile, loginStats, loginLogs, loading, error } = useSupportIdentity();
  const [tab, setTab] = useState<TabKey>("overview");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNext, setShowNext] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [phoneLogs, setPhoneLogs] = useState<PhoneLog[]>([]);
  const [phoneLogsLoading, setPhoneLogsLoading] = useState(false);
  const [phoneLogsError, setPhoneLogsError] = useState("");

  const tabs = useMemo(
    () => [
      { key: "overview" as const, label: "Profile Details" },
      { key: "password" as const, label: "Password Change" },
      { key: "logs" as const, label: "Login Logs" },
      { key: "phone_logs" as const, label: "Phone Call Logs" },
    ],
    [],
  );

  useEffect(() => {
    if (tab !== "phone_logs") return;
    setPhoneLogsLoading(true); setPhoneLogsError("");
    fetch("/api/customer-support/mobile/calls/logs?per_page=100", { credentials: "same-origin" })
      .then(async response => { const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.message || data.error || "Unable to load phone logs."); return data; })
      .then(data => setPhoneLogs(data.calls || []))
      .catch(error => setPhoneLogsError(error instanceof Error ? error.message : "Unable to load phone logs."))
      .finally(() => setPhoneLogsLoading(false));
  }, [tab]);

  const shellStyle: React.CSSProperties = {
    background: t.cardBg,
    border: `1px solid ${t.cardBorder}`,
    borderRadius: t.cardRadius,
    boxShadow: t.cardShadow,
  };

  const inputStyle: React.CSSProperties = {
    height: "46px",
    borderRadius: "16px",
    border: `1px solid ${t.inputBorder}`,
    background: t.inputBg,
    color: t.textPrimary,
    padding: "0 15px",
    outline: "none",
    width: "100%",
  };

  if (loading && !profile) {
    return (
      <div className="overflow-y-auto" style={{ background: t.pageBg, minHeight: "100%" }}>
        <div style={{ padding: "30px 28px 40px", color: t.textSecondary }}>Loading profile...</div>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="overflow-y-auto" style={{ background: t.pageBg, minHeight: "100%" }}>
        <div style={{ padding: "30px 28px 40px", color: t.dangerText }}>
          {error || "Unable to load profile details."}
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-y-auto" style={{ background: t.pageBg, minHeight: "100%" }}>
      <div style={{ padding: "30px 28px 40px", maxWidth: "1400px" }}>
        <div
          className="mb-6 flex flex-wrap items-center justify-between gap-4"
          style={{
            ...shellStyle,
            padding: "24px 26px",
            background: isDark
              ? "linear-gradient(135deg, rgba(104,82,255,0.22), rgba(21,17,38,0.96))"
              : "linear-gradient(135deg, rgba(104,82,255,0.12), rgba(255,255,255,0.90))",
          }}
        >
          <div className="flex items-center gap-4">
            <div
              className="flex items-center justify-center rounded-[22px]"
              style={{
                width: "68px",
                height: "68px",
                background: isDark ? "rgba(255,255,255,0.08)" : "#ebe6ff",
                border: `1px solid ${t.accentBorder}`,
              }}
            >
              <UserCircle2 size={34} style={{ color: t.accent }} />
            </div>
            <div>
              <h2 style={{ fontSize: "1.5rem", color: t.textPrimary }}>My Profile</h2>
              <p style={{ marginTop: "6px", fontSize: "0.92rem", color: t.textTertiary }}>
                View account details, update your password, and monitor recent sign-ins.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {[ 
              { label: profile.role, icon: BadgeCheck },
              { label: profile.branch, icon: Building2 },
              { label: profile.status, icon: ShieldCheck },
            ].map((item) => (
              <div
                key={item.label}
                className="flex items-center gap-2 rounded-full"
                style={{
                  padding: "10px 14px",
                  background: isDark ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.7)",
                  border: `1px solid ${t.border}`,
                  color: t.textSecondary,
                }}
              >
                <item.icon size={14} style={{ color: t.accent }} />
                <span style={{ fontSize: "0.82rem", fontWeight: 600 }}>{item.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="mb-6 flex flex-wrap gap-2">
          {tabs.map((item) => {
            const active = item.key === tab;
            return (
              <button
                key={item.key}
                onClick={() => setTab(item.key)}
                className="rounded-full transition-all"
                style={{
                  padding: "11px 16px",
                  background: active ? t.accent : t.surface,
                  color: active ? "#ffffff" : t.textSecondary,
                  border: `1px solid ${active ? t.accent : t.border}`,
                  boxShadow: active ? "0 14px 30px rgba(93, 71, 255, 0.20)" : "none",
                  fontSize: "0.84rem",
                  fontWeight: 700,
                }}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        {tab === "overview" && (
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.25fr,0.75fr]">
            <div style={{ ...shellStyle, padding: "24px" }}>
              <div className="mb-5 flex items-center justify-between">
                <div>
                  <h3 style={{ color: t.textPrimary }}>Profile Details</h3>
                  <p style={{ marginTop: "4px", fontSize: "0.82rem", color: t.textMuted }}>
                    Core account information for the currently logged-in support user.
                  </p>
                </div>
                <div
                  className="rounded-full"
                  style={{
                    padding: "9px 14px",
                    background: t.accentSubtle,
                    color: t.accent,
                    fontSize: "0.78rem",
                    fontWeight: 700,
                  }}
                >
                  Verified Account
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {[
                  { label: "Full Name", value: profile.name, icon: UserCircle2 },
                  { label: "Email Address", value: profile.email || "Not set", icon: Mail },
                  { label: "Phone Number", value: profile.phone || "Not set", icon: Phone },
                  { label: "Employee ID", value: profile.employee_id || "Not set", icon: BadgeCheck },
                  { label: "Branch", value: profile.branch || "Not set", icon: Building2 },
                  { label: "Location", value: profile.location || "Not set", icon: MapPin },
                  { label: "Username", value: profile.username || "Not set", icon: BadgeCheck },
                  { label: "Joined", value: profile.joined || "Not set", icon: Clock3 },
                ].map((field) => (
                  <div
                    key={field.label}
                    className="rounded-[22px]"
                    style={{
                      padding: "18px 18px 16px",
                      background: t.surfaceSubtle,
                      border: `1px solid ${t.borderSubtle}`,
                    }}
                  >
                    <div className="mb-3 flex items-center gap-2">
                      <field.icon size={14} style={{ color: t.accent }} />
                      <span style={{ fontSize: "0.75rem", fontWeight: 700, color: t.textMuted, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        {field.label}
                      </span>
                    </div>
                    <div style={{ fontSize: "0.96rem", fontWeight: 700, color: t.textPrimary }}>
                      {field.value}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-6">
              <div style={{ ...shellStyle, padding: "22px" }}>
                <h3 style={{ marginBottom: "16px", color: t.textPrimary }}>Security Status</h3>
                <div className="space-y-3">
                  {[
                    { label: "Two-factor protection", value: "Managed by admin", icon: ShieldCheck, good: true },
                    { label: "Last login", value: loginStats?.last_login || "No recent login", icon: KeyRound, good: true },
                    { label: "Active devices", value: `${loginStats?.unique_devices ?? 0} devices`, icon: MonitorSmartphone, good: true },
                  ].map((item) => (
                    <div
                      key={item.label}
                      className="flex items-center justify-between rounded-[18px]"
                      style={{ padding: "14px 15px", background: t.surfaceSubtle, border: `1px solid ${t.borderSubtle}` }}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className="flex items-center justify-center rounded-xl"
                          style={{ width: "38px", height: "38px", background: t.accentSubtle }}
                        >
                          <item.icon size={16} style={{ color: t.accent }} />
                        </div>
                        <div>
                          <div style={{ fontSize: "0.84rem", fontWeight: 700, color: t.textPrimary }}>{item.label}</div>
                          <div style={{ fontSize: "0.76rem", color: t.textMuted }}>{item.value}</div>
                        </div>
                      </div>
                      <CheckCircle2 size={18} style={{ color: item.good ? "#16a34a" : t.textMuted }} />
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ ...shellStyle, padding: "22px" }}>
                <h3 style={{ marginBottom: "16px", color: t.textPrimary }}>Quick Activity</h3>
                <div className="space-y-3">
                  {[
                    { title: "Last login", meta: loginStats?.last_login || "No recent login", icon: Clock3 },
                    { title: "Unique IPs in 30 days", meta: `${loginStats?.unique_ips ?? 0} known IP address(es)`, icon: Globe },
                    { title: "Logins in 30 days", meta: `${loginStats?.total_logins ?? 0} successful sign-in record(s)`, icon: LockKeyhole },
                  ].map((event) => (
                    <div key={event.title} className="flex items-start gap-3">
                      <div
                        className="mt-0.5 flex items-center justify-center rounded-xl"
                        style={{ width: "34px", height: "34px", background: t.accentSubtle }}
                      >
                        <event.icon size={14} style={{ color: t.accent }} />
                      </div>
                      <div>
                        <div style={{ fontSize: "0.83rem", fontWeight: 700, color: t.textPrimary }}>{event.title}</div>
                        <div style={{ fontSize: "0.75rem", color: t.textMuted, marginTop: "3px" }}>{event.meta}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === "password" && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr,0.85fr]">
            <div style={{ ...shellStyle, padding: "24px" }}>
              <div className="mb-5">
                <h3 style={{ color: t.textPrimary }}>Change Password</h3>
                <p style={{ marginTop: "4px", fontSize: "0.82rem", color: t.textMuted }}>
                  Use a strong password with a mix of upper case, lower case, numbers, and symbols.
                </p>
              </div>

              <div className="space-y-4">
                {[
                  { label: "Current Password", visible: showCurrent, toggle: () => setShowCurrent((v) => !v) },
                  { label: "New Password", visible: showNext, toggle: () => setShowNext((v) => !v) },
                  { label: "Confirm New Password", visible: showConfirm, toggle: () => setShowConfirm((v) => !v) },
                ].map((field, index) => (
                  <div key={field.label}>
                    <label style={{ display: "block", marginBottom: "8px", fontSize: "0.8rem", fontWeight: 700, color: t.textSecondary }}>
                      {field.label}
                    </label>
                    <div className="relative">
                      <input
                        type={field.visible ? "text" : "password"}
                        placeholder={index === 0 ? "Enter current password" : "Enter secure password"}
                        style={{ ...inputStyle, paddingRight: "48px" }}
                      />
                      <button
                        type="button"
                        onClick={field.toggle}
                        className="absolute right-3 top-1/2 -translate-y-1/2"
                        style={{ color: t.textMuted }}
                      >
                        {field.visible ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              <div
                className="mt-5 rounded-[22px]"
                style={{ padding: "18px", background: t.surfaceSubtle, border: `1px solid ${t.borderSubtle}` }}
              >
                <div style={{ fontSize: "0.8rem", fontWeight: 700, color: t.textPrimary, marginBottom: "10px" }}>
                  Password checklist
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {[
                    "Minimum 8 characters",
                    "At least one uppercase letter",
                    "At least one number",
                    "At least one special character",
                  ].map((rule) => (
                    <div key={rule} className="flex items-center gap-2">
                      <CheckCircle2 size={14} style={{ color: t.accent }} />
                      <span style={{ fontSize: "0.78rem", color: t.textSecondary }}>{rule}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-6 flex flex-wrap gap-3">
                <button
                  className="rounded-full"
                  style={{
                    padding: "12px 20px",
                    background: t.accent,
                    color: "#fff",
                    fontWeight: 700,
                    boxShadow: "0 16px 34px rgba(93, 71, 255, 0.22)",
                  }}
                >
                  Update Password
                </button>
                <button
                  className="rounded-full"
                  style={{ padding: "12px 20px", background: t.surface, color: t.textSecondary, border: `1px solid ${t.border}` }}
                >
                  Cancel
                </button>
              </div>
            </div>

            <div style={{ ...shellStyle, padding: "24px" }}>
              <h3 style={{ marginBottom: "16px", color: t.textPrimary }}>Password Protection Tips</h3>
              <div className="space-y-3">
                {[
                  "Do not reuse the same password across other apps or devices.",
                  "Update your password immediately if you notice suspicious sign-ins.",
                  "Combine password changes with MFA for stronger account protection.",
                ].map((tip) => (
                  <div
                    key={tip}
                    className="flex items-start gap-3 rounded-[18px]"
                    style={{ padding: "14px 15px", background: t.surfaceSubtle, border: `1px solid ${t.borderSubtle}` }}
                  >
                    <ChevronRight size={16} style={{ color: t.accent, marginTop: "2px" }} />
                    <span style={{ fontSize: "0.8rem", color: t.textSecondary, lineHeight: 1.5 }}>{tip}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "logs" && (
          <div style={{ ...shellStyle, padding: "24px" }}>
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 style={{ color: t.textPrimary }}>Recent Login Logs</h3>
                <p style={{ marginTop: "4px", fontSize: "0.82rem", color: t.textMuted }}>
                  Review account access across devices, locations, and sessions.
                </p>
              </div>
              <div
                className="rounded-full"
                style={{ padding: "10px 14px", background: t.accentSubtle, color: t.accent, fontSize: "0.78rem", fontWeight: 700 }}
              >
                Last activity: 11 Aug 2026, 10:42 AM
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full" style={{ borderCollapse: "separate", borderSpacing: 0 }}>
                <thead>
                  <tr style={{ background: t.tableHeaderBg }}>
                    {["Time", "Device", "IP Address", "Location", "Status"].map((heading) => (
                      <th
                        key={heading}
                        className="px-4 py-3 text-left"
                        style={{
                          fontSize: "0.72rem",
                          fontWeight: 700,
                          color: t.textMuted,
                          textTransform: "uppercase",
                          letterSpacing: "0.08em",
                          borderBottom: `1px solid ${t.tableDivider}`,
                        }}
                      >
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loginLogs.map((log, index) => {
                    const statusColor =
                      log.status === "Failed Attempt" ? "#dc2626" :
                      log.status === "Password Changed" ? t.accent :
                      "#16a34a";

                    return (
                      <tr
                        key={log.id}
                        style={{ borderBottom: index < loginLogs.length - 1 ? `1px solid ${t.tableDivider}` : "none" }}
                      >
                        <td className="px-4 py-4" style={{ fontSize: "0.84rem", color: t.textSecondary }}>{log.time}</td>
                        <td className="px-4 py-4" style={{ fontSize: "0.84rem", color: t.textPrimary, fontWeight: 700 }}>{log.device}</td>
                        <td className="px-4 py-4" style={{ fontSize: "0.82rem", color: t.textSecondary, fontFamily: "monospace" }}>{log.ip}</td>
                        <td className="px-4 py-4" style={{ fontSize: "0.82rem", color: t.textSecondary }}>{log.location}</td>
                        <td className="px-4 py-4">
                          <span
                            className="rounded-full"
                            style={{
                              display: "inline-block",
                              padding: "7px 11px",
                              background: `${statusColor}18`,
                              color: statusColor,
                              fontSize: "0.76rem",
                              fontWeight: 700,
                            }}
                          >
                            {log.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "phone_logs" && (
          <div style={{ ...shellStyle, padding: "24px" }}>
            <div className="mb-5"><h3 style={{ color: t.textPrimary }}>Synced Phone Call Logs</h3><p style={{ marginTop: "4px", fontSize: "0.82rem", color: t.textMuted }}>Android call records received from registered company devices.</p></div>
            {phoneLogsError && <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{phoneLogsError}</div>}
            {phoneLogsLoading ? <div className="py-16 text-center" style={{ color: t.textMuted }}>Loading phone call logs...</div> : (
              <div className="overflow-x-auto"><table className="w-full" style={{ borderCollapse: "separate", borderSpacing: 0 }}><thead><tr style={{ background: t.tableHeaderBg }}>
                {["Date & Time", "Device", "From", "Contact Number", "Customer", "Type", "Duration", "Status"].map(heading => <th key={heading} className="px-4 py-3 text-left" style={{ fontSize: "0.72rem", fontWeight: 700, color: t.textMuted, textTransform: "uppercase", borderBottom: `1px solid ${t.tableDivider}` }}>{heading}</th>)}
              </tr></thead><tbody>{phoneLogs.map(log => <tr key={log.id} style={{ borderBottom: `1px solid ${t.tableDivider}` }}>
                <td className="whitespace-nowrap px-4 py-4 text-sm" style={{ color: t.textSecondary }}>{log.date}<div className="text-xs" style={{ color: t.textMuted }}>{log.time}</div></td>
                <td className="px-4 py-4 text-sm font-bold" style={{ color: t.textPrimary }}>{log.deviceName || "Unknown device"}</td>
                <td className="whitespace-nowrap px-4 py-4 text-sm font-semibold" style={{ color: t.textSecondary }}>{log.fromNumber || "Not available"}</td>
                <td className="whitespace-nowrap px-4 py-4 text-sm" style={{ color: t.textSecondary }}>{log.phone}</td>
                <td className="px-4 py-4 text-sm" style={{ color: t.textPrimary }}>{log.customerMatch === "not_customer" ? <span className="rounded-full bg-red-50 px-2 py-1 text-xs font-bold text-red-700">Not a Customer</span> : log.customer}</td>
                <td className="px-4 py-4 text-sm font-bold" style={{ color: t.textSecondary }}>{log.type}</td>
                <td className="px-4 py-4 text-sm" style={{ color: t.textSecondary }}>{log.duration}</td>
                <td className="px-4 py-4"><span className={`rounded-full px-2 py-1 text-xs font-bold ${log.enrichmentStatus === "needs_update" ? "bg-amber-50 text-amber-700" : "bg-green-50 text-green-700"}`}>{log.enrichmentStatus === "needs_update" ? "Needs Update" : "Complete"}</span></td>
              </tr>)}{!phoneLogs.length && <tr><td colSpan={8} className="py-16 text-center" style={{ color: t.textMuted }}>No Android phone logs have been received.</td></tr>}</tbody></table></div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
