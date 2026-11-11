import { poll, snapshot } from "./polling";
import { useEffect, useState } from "react";
import {
  Search, Bell, ChevronDown, LogOut, User,
  X, Plus, Sun, Moon,
} from "lucide-react";
import { useSupportIdentity } from "./SupportIdentityContext";
import { useTheme } from "./ThemeContext";

/* ─── Top-nav colour palettes ────────────────────────── */
const NAV_LIGHT = {
  bg:           "rgba(255,255,255,0.72)",
  border:       "rgba(122,99,255,0.12)",
  title:        "#17142b",
  titleSub:     "#706a86",
  searchBg:     "rgba(255,255,255,0.68)",
  searchBgFocus:"rgba(255,255,255,0.96)",
  searchBorder: "rgba(122,99,255,0.14)",
  searchBorderFocus:"rgba(104,82,255,0.45)",
  searchRing:   "rgba(104,82,255,0.10)",
  searchText:   "#17142b",
  searchPlaceholder:"#9e98b3",
  btnBg:        "rgba(255,255,255,0.70)",
  btnBgHover:   "#ffffff",
  btnBorder:    "rgba(122,99,255,0.12)",
  btnText:      "#3f3a58",
  primaryBg:    "linear-gradient(135deg, #6b56ff, #8f7cff)",
  primaryText:  "#FFFFFF",
  primaryHover: "linear-gradient(135deg, #5b48e2, #8572ff)",
  iconColor:    "#706a86",
  iconHover:    "#17142b",
  kbd:          "#f1edff",
  kbdText:      "#8c85a8",
  badgeBorder:  "#ffffff",
  dropdownBg:   "#FFFFFF",
  dropdownBorder:"rgba(122,99,255,0.12)",
  dropdownText: "#17142b",
  dropdownMuted:"#706a86",
  dropdownHover:"#f7f3ff",
  dropdownAccent:"#6852ff",
};

const NAV_DARK = {
  bg:           "rgba(17,13,28,0.84)",
  border:       "rgba(255,255,255,0.07)",
  title:        "#f5f0ff",
  titleSub:     "#9d91c2",
  searchBg:     "rgba(255,255,255,0.05)",
  searchBgFocus:"rgba(255,255,255,0.08)",
  searchBorder: "rgba(255,255,255,0.09)",
  searchBorderFocus:"rgba(141,123,255,0.40)",
  searchRing:   "rgba(141,123,255,0.12)",
  searchText:   "#f5f0ff",
  searchPlaceholder:"rgba(245,240,255,0.35)",
  btnBg:        "rgba(255,255,255,0.05)",
  btnBgHover:   "rgba(255,255,255,0.10)",
  btnBorder:    "rgba(255,255,255,0.08)",
  btnText:      "#d4caee",
  primaryBg:    "linear-gradient(135deg, #6b56ff, #8f7cff)",
  primaryText:  "#FFFFFF",
  primaryHover: "linear-gradient(135deg, #7a67ff, #9d8cff)",
  iconColor:    "#9d91c2",
  iconHover:    "#FFFFFF",
  kbd:          "rgba(255,255,255,0.05)",
  kbdText:      "#8277a2",
  badgeBorder:  "#171128",
  dropdownBg:   "#151126",
  dropdownBorder:"rgba(255,255,255,0.08)",
  dropdownText: "#f5f0ff",
  dropdownMuted:"#9d91c2",
  dropdownHover:"rgba(255,255,255,0.05)",
  dropdownAccent:"#8d7bff",
};

/* ─── Data ───────────────────────────────────────────── */
const PAGE_LABELS: Record<string, string> = {
  dashboard: "Dashboard", customers: "Customers", tickets: "Tickets",
  calls: "Calls", tasks: "Tasks", targets: "My Targets", collections: "Collections",
  repeatpurchases: "Repeat Purchases", deliveries: "Deliveries", repairs: "Repairs", satisfaction: "Satisfaction",
  reports: "Reports", notifications: "Notifications", profile: "Profile", activitylogs: "Activity logs",
};

/* ─── Dropdown ───────────────────────────────────────── */
function Dropdown({ trigger, children, align = "right", width = 250, n }: {
  trigger: React.ReactNode; children: React.ReactNode;
  align?: "left" | "right"; width?: number; n: typeof NAV_LIGHT;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <div onClick={() => setOpen(o => !o)}>{trigger}</div>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute z-50 overflow-hidden"
            style={{ top: "calc(100% + 10px)", [align === "right" ? "right" : "left"]: 0, width, background: n.dropdownBg, borderRadius: "16px", border: `1px solid ${n.dropdownBorder}`, boxShadow: "0px 8px 32px rgba(0,0,0,0.12), 0px 2px 8px rgba(0,0,0,0.06)" }}>
            {children}
          </div>
        </>
      )}
    </div>
  );
}

/* ─── TopNav ─────────────────────────────────────────── */
interface TopNavProps { currentPage: string; onLogout: () => void; onNavigate: (page: string) => void; }
type SearchResult = { type: string; title: string; subtitle: string; page: string; id: string; query: string };

export function TopNav({ currentPage, onLogout, onNavigate }: TopNavProps) {
  const { isDark, toggle }    = useTheme();
  const { profile }           = useSupportIdentity();
  const n                     = isDark ? NAV_DARK : NAV_LIGHT;
  const [search, setSearch]   = useState("");
  const [focused, setFocused] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [activeResult, setActiveResult] = useState(0);
  const [notifications, setNotifications] = useState<Array<{ id: string; text: string; time: string; unread: boolean; dot: string; actionPage: string }>>([]);
  const [unread, setUnread] = useState(0);
  const loadNotifications = () => snapshot("/api/customer-support/notifications?per_page=5").then(data => {
    if (!data.ok) return;
    setUnread(data.unreadCount || 0);
    setNotifications((data.notifications || []).map((item: any) => ({ id: item.id, text: item.title, time: new Date(item.createdAt).toLocaleString(), unread: !item.read, actionPage: item.actionPage, dot: item.priority === "Critical" ? "#EF4444" : item.type === "delivery" ? "#16A34A" : "#6852ff" })));
  });
  useEffect(() => { const refresh = (event: Event) => { if (event instanceof CustomEvent && typeof event.detail === 'number') setUnread(event.detail); }; window.addEventListener("customer-support-notification-count", refresh); const timer = poll(loadNotifications); return () => { window.removeEventListener("customer-support-notification-count", refresh); timer.stop(); }; }, []);
  const markAllRead = async () => { await fetch("/api/customer-support/notifications", { method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) }); void loadNotifications().catch(() => undefined); };
  const openNotification = async (id: string, actionPage: string) => { await fetch("/api/customer-support/notifications", { method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) }); onNavigate(actionPage || "notifications"); void loadNotifications().catch(() => undefined); };
  const userName = profile?.name || "Loading User";
  const userEmail = profile?.email || "";
  const userRole = profile?.role || "Customer Support";
  const userInitials = profile?.avatar_initials || "U";

  useEffect(() => {
    const term = search.trim();
    if (term.length < 2) { setSearchResults([]); setSearching(false); return; }
    const controller = new AbortController(); setSearching(true);
    const timer = window.setTimeout(() => fetch(`/api/customer-support/search?q=${encodeURIComponent(term)}`, { credentials: "same-origin", signal: controller.signal }).then(response => response.json()).then(data => { if (data.ok) { setSearchResults(data.results || []); setActiveResult(0); } }).catch(() => undefined).finally(() => setSearching(false)), 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [search]);
  const chooseSearchResult = (result: SearchResult) => {
    if (result.query) window.sessionStorage.setItem("customer-support-global-search", JSON.stringify({ page: result.page, query: result.query, id: result.id, type: result.type }));
    else window.sessionStorage.removeItem("customer-support-global-search");
    onNavigate(result.page); setSearch(""); setSearchResults([]); setFocused(false);
    window.setTimeout(() => window.dispatchEvent(new CustomEvent("customer-support-global-search", { detail: result })), 0);
  };

  return (
    <header
      className="sticky top-0 z-30 flex items-center gap-4 flex-shrink-0"
      style={{ height: "72px", padding: "0 28px", background: n.bg, borderBottom: `1px solid ${n.border}`, backdropFilter: "blur(18px)", boxShadow: "0 18px 42px rgba(41,24,88,0.08)" }}
    >
      {/* Page title */}
      <div className="flex-shrink-0">
        <h1 style={{ fontSize: "1.125rem", fontWeight: 700, color: n.title, letterSpacing: "-0.03em", lineHeight: 1, fontFamily: "var(--font-family-heading)" }}>
          {PAGE_LABELS[currentPage] ?? "Dashboard"}
        </h1>
      </div>

      {/* ── Search ── */}
      <div className="relative flex-1 flex items-center" style={{ maxWidth: "520px", margin: "0 auto" }}>
        <div className="w-full flex items-center gap-2.5 rounded-xl transition-all"
          style={{ height: "44px", padding: "0 14px", background: focused ? n.searchBgFocus : n.searchBg, border: `1.5px solid ${focused ? n.searchBorderFocus : n.searchBorder}`, boxShadow: focused ? `0 0 0 3px ${n.searchRing}` : "none" }}>
          <Search size={15} style={{ color: n.iconColor, flexShrink: 0 }} />
          <input
            type="text"
            placeholder="Search customers, tickets, orders..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => window.setTimeout(() => setFocused(false), 150)}
            onKeyDown={e => { if (e.key === "ArrowDown") { e.preventDefault(); setActiveResult(value => Math.min(value + 1, searchResults.length - 1)); } else if (e.key === "ArrowUp") { e.preventDefault(); setActiveResult(value => Math.max(value - 1, 0)); } else if (e.key === "Enter" && searchResults[activeResult]) { e.preventDefault(); chooseSearchResult(searchResults[activeResult]); } else if (e.key === "Escape") { setFocused(false); } }}
            className="flex-1 outline-none bg-transparent"
            style={{ fontSize: "0.875rem", color: n.searchText }}
          />
          {/* Override placeholder colour via inline filter trick */}
          {search
            ? <button onClick={() => setSearch("")} style={{ color: n.iconColor }}><X size={14} /></button>
            : <kbd style={{ fontSize: "0.625rem", color: n.kbdText, background: n.kbd, padding: "2px 6px", borderRadius: "6px", border: `1px solid ${n.searchBorder}` }}>⌘K</kbd>}
        </div>
        {focused && search.trim().length >= 2 && <div className="absolute left-0 right-0 top-[50px] z-50 max-h-[440px] overflow-y-auto rounded-lg border border-gray-200 bg-white py-2 shadow-2xl">
          <div className="flex items-center justify-between px-4 pb-2 text-[11px] font-bold uppercase text-gray-400"><span>Global search</span><span>{searching ? "Searching..." : `${searchResults.length} results`}</span></div>
          {!searching && searchResults.length === 0 && <div className="px-4 py-8 text-center text-sm text-gray-500">No customers, tickets, tasks, calls, deliveries, or pages found.</div>}
          {searchResults.map((result, index) => <button key={`${result.type}-${result.id}`} onMouseDown={e => e.preventDefault()} onClick={() => chooseSearchResult(result)} onMouseEnter={() => setActiveResult(index)} className={`flex w-full items-start gap-3 px-4 py-3 text-left ${activeResult === index ? "bg-blue-50" : "bg-white"}`}><span className="mt-0.5 rounded bg-gray-100 px-2 py-1 text-[10px] font-bold uppercase text-gray-500">{result.type}</span><span className="min-w-0 flex-1"><strong className="block truncate text-sm text-gray-900">{result.title}</strong><span className="mt-0.5 block truncate text-xs text-gray-500">{result.subtitle}</span></span></button>)}
          {searchResults.length > 0 && <div className="border-t px-4 pt-2 text-[11px] text-gray-400">Use ↑ ↓ to select and Enter to open</div>}
        </div>}
      </div>

      {/* ── Right ── */}
      <div className="flex items-center gap-2 flex-shrink-0">

        {/* New Ticket */}
        <button onClick={() => { window.sessionStorage.setItem("customer-support-open-new-ticket", "1"); onNavigate("tickets"); window.setTimeout(() => window.dispatchEvent(new Event("customer-support-open-new-ticket")), 0); }} className="flex items-center gap-2 rounded-xl transition-all"
          style={{ height: "44px", padding: "0 18px", background: n.primaryBg, color: n.primaryText, fontSize: "0.875rem", fontWeight: 600, gap: "7px", boxShadow: "0 2px 12px rgba(0,0,0,0.12)", border: "none" }}
          onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = n.primaryHover}
          onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = n.primaryBg}>
          <Plus size={15} /> New Ticket
        </button>

        <div style={{ width: "1px", height: "22px", background: n.border, margin: "0 2px" }} />

        {/* Dark / Light toggle */}
        <button
          onClick={toggle}
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
          className="rounded-2xl transition-all"
          style={{
            height: "44px",
            padding: "4px",
            minWidth: "124px",
            background: n.btnBg,
            border: `1px solid ${n.btnBorder}`,
            boxShadow: "0 10px 28px rgba(41,24,88,0.08)",
          }}
          onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = n.btnBgHover}
          onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = n.btnBg}
        >
          <div className="flex items-center gap-1" style={{ width: "100%" }}>
            <div
              className="flex items-center justify-center gap-1.5 rounded-xl transition-all"
              style={{
                width: "50%",
                height: "34px",
                background: isDark ? "linear-gradient(135deg, #6b56ff, #8f7cff)" : "transparent",
                color: isDark ? "#ffffff" : n.iconColor,
                boxShadow: isDark ? "0 10px 22px rgba(93, 71, 255, 0.22)" : "none",
              }}
            >
              <Moon size={14} />
              <span style={{ fontSize: "0.76rem", fontWeight: 700 }}>Dark</span>
            </div>
            <div
              className="flex items-center justify-center gap-1.5 rounded-xl transition-all"
              style={{
                width: "50%",
                height: "34px",
                background: !isDark ? "rgba(255,255,255,0.92)" : "transparent",
                color: !isDark ? n.title : n.iconColor,
                boxShadow: !isDark ? "0 10px 22px rgba(41, 24, 88, 0.10)" : "none",
              }}
            >
              <Sun size={14} />
              <span style={{ fontSize: "0.76rem", fontWeight: 700 }}>Light</span>
            </div>
          </div>
        </button>

        {/* Notifications */}
        <Dropdown align="right" width={380} n={n}
          trigger={
            <button className="relative rounded-xl flex items-center justify-center transition-all"
              style={{ width: "44px", height: "44px", background: n.btnBg, border: `1px solid ${n.btnBorder}`, color: n.iconColor }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = n.btnBgHover; (e.currentTarget as HTMLElement).style.color = n.iconHover; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = n.btnBg; (e.currentTarget as HTMLElement).style.color = n.iconColor; }}>
              <Bell size={16} />
              {unread > 0 && (
                <div className="absolute flex items-center justify-center text-white"
                  style={{ top: "8px", right: "8px", width: "8px", height: "8px", background: "#EF4444", borderRadius: "99px", border: `2px solid ${n.bg}` }} />
              )}
            </button>
          }>
          <div>
            <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: `1px solid ${n.dropdownBorder}` }}>
              <span style={{ fontSize: "0.9375rem", fontWeight: 600, color: n.dropdownText }}>Notifications</span>
              {unread > 0 && <button onClick={markAllRead} style={{ fontSize: "0.8125rem", color: n.dropdownAccent, fontWeight: 500 }}>Mark all read</button>}
            </div>
            <div style={{ maxHeight: "380px", overflowY: "auto" }}>
              {notifications.map(notif => (
                <div key={notif.id} onClick={() => void openNotification(notif.id, notif.actionPage)}
                  className="flex items-start gap-3 cursor-pointer transition-colors"
                  style={{ padding: "12px 20px", background: notif.unread ? (isDark ? "rgba(141,123,255,0.10)" : "#f5f1ff") : "transparent", borderBottom: `1px solid ${n.dropdownBorder}` }}
                  onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = n.dropdownHover}
                  onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = notif.unread ? (isDark ? "rgba(141,123,255,0.10)" : "#f5f1ff") : "transparent"}>
                  <div style={{ width: "8px", height: "8px", borderRadius: "99px", background: notif.unread ? notif.dot : n.dropdownBorder, flexShrink: 0, marginTop: "5px" }} />
                  <div className="flex-1 min-w-0">
                    <p style={{ fontSize: "0.875rem", color: n.dropdownText, lineHeight: 1.5 }}>{notif.text}</p>
                    <p style={{ fontSize: "0.75rem", color: n.dropdownMuted, marginTop: "3px" }}>{notif.time}</p>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${n.dropdownBorder}`, padding: "12px 20px" }}>
              <button onClick={() => onNavigate("notifications")} style={{ fontSize: "0.875rem", color: n.dropdownAccent, fontWeight: 500 }}>View all notifications</button>
            </div>
          </div>
        </Dropdown>

        {/* Profile */}
        <Dropdown align="right" width={240} n={n}
          trigger={
            <button className="flex items-center gap-2.5 rounded-xl transition-all"
              style={{ height: "44px", padding: "0 12px 0 8px", background: n.btnBg, border: `1px solid ${n.btnBorder}` }}
              onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = n.btnBgHover}
              onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = n.btnBg}>
              <div className="rounded-full flex items-center justify-center text-white"
                style={{ width: "26px", height: "26px", background: "rgba(255,255,255,0.25)", fontSize: "0.5625rem", fontWeight: 700, border: "1px solid rgba(255,255,255,0.4)" }}>
                {userInitials}
              </div>
              <span style={{ fontSize: "0.875rem", fontWeight: 400, color: n.btnText }}>{userName}</span>
              <ChevronDown size={13} style={{ color: n.iconColor }} />
            </button>
          }>
          <div>
            <div style={{ padding: "16px 20px", borderBottom: `1px solid ${n.dropdownBorder}` }}>
              <div style={{ fontSize: "0.9375rem", fontWeight: 600, color: n.dropdownText }}>{userName}</div>
              <div style={{ fontSize: "0.8125rem", color: n.dropdownMuted, marginTop: "2px" }}>{userEmail}</div>
              <div style={{ marginTop: "8px", display: "inline-block", padding: "3px 10px", borderRadius: "8px", fontSize: "0.75rem", fontWeight: 600, background: isDark ? "rgba(141,123,255,0.14)" : "#ebe6ff", color: n.dropdownAccent }}>
                {userRole}
              </div>
            </div>
            <div style={{ padding: "8px 0" }}>
              {[{ icon: User, label: "My Profile", action: () => onNavigate("profile") }].map(item => (
                <button key={item.label} className="w-full flex items-center gap-3 transition-colors"
                  onClick={item.action}
                  style={{ padding: "10px 20px", fontSize: "0.875rem", color: n.dropdownText, background: "transparent" }}
                  onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = n.dropdownHover}
                  onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
                  <item.icon size={15} style={{ color: n.dropdownMuted }} />{item.label}
                </button>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${n.dropdownBorder}`, padding: "8px 0 4px" }}>
              <button onClick={onLogout} className="w-full flex items-center gap-3 transition-colors"
                style={{ padding: "10px 20px", fontSize: "0.875rem", color: "#EF4444", background: "transparent" }}
                onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = isDark ? "rgba(239,68,68,0.08)" : "#FEF2F2"}
                onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}>
                <LogOut size={15} style={{ color: "#EF4444" }} />Sign out
              </button>
            </div>
          </div>
        </Dropdown>
      </div>
    </header>
  );
}
