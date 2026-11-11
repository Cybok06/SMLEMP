import { lazy, Suspense, useEffect, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopNav } from "./TopNav";
import { Dashboard } from "./Dashboard";
import { useTheme } from "./ThemeContext";

const CustomersPage = lazy(() => import("./CustomersPage").then(module => ({ default: module.CustomersPage })));
const ArchivedCustomersPage = lazy(() => import("./ArchivedCustomersPage").then(module => ({ default: module.ArchivedCustomersPage })));
const TicketsPage = lazy(() => import("./TicketsPage").then(module => ({ default: module.TicketsPage })));
const DeliveriesPage = lazy(() => import("./DeliveriesPage").then(module => ({ default: module.DeliveriesPage })));
const CallsPage = lazy(() => import("./CallsPage").then(module => ({ default: module.CallsPage })));
const TasksPage = lazy(() => import("./TasksPage").then(module => ({ default: module.TasksPage })));
const RepeatPurchasesPage = lazy(() => import("./RepeatPurchasesPage").then(module => ({ default: module.RepeatPurchasesPage })));
const TargetsPage = lazy(() => import("./TargetsPage").then(module => ({ default: module.TargetsPage })));
const SatisfactionPage = lazy(() => import("./SatisfactionPage").then(module => ({ default: module.SatisfactionPage })));
const ReportsPage = lazy(() => import("./ReportsPage").then(module => ({ default: module.ReportsPage })));
const NotificationsPage = lazy(() => import("./NotificationsPage").then(module => ({ default: module.NotificationsPage })));
const ProfilePage = lazy(() => import("./ProfilePage").then(module => ({ default: module.ProfilePage })));
const ActivityLogsPage = lazy(() => import("./ActivityLogsPage").then(module => ({ default: module.ActivityLogsPage })));

interface LayoutProps { onLogout: () => void; }

// Only pages with a fixed-height internal scroller belong here. Tickets and
// calls grow with their tables, so the main content area must scroll for them.
const SELF_SCROLL = new Set(["dashboard", "customers", "deliveries"]);
const PAGE_LABELS: Record<string, string> = {
  dashboard: "Dashboard",
  customers: "Customers",
  archived: "Archived Customers",
  tickets: "Tickets",
  calls: "Calls",
  tasks: "Tasks",
  targets: "My Targets",
  repeatpurchases: "Repeat Purchases",
  deliveries: "Deliveries",
  satisfaction: "Satisfaction",
  reports: "Reports",
  notifications: "Notifications",
  profile: "Profile",
  activitylogs: "Activity logs",
};
const VALID_PAGES = new Set(Object.keys(PAGE_LABELS));

function pageFromHash() {
  const raw = window.location.hash.replace(/^#/, "").trim().toLowerCase();
  return VALID_PAGES.has(raw) ? raw : "dashboard";
}

export function Layout({ onLogout }: LayoutProps) {
  const [currentPage, setCurrentPage] = useState(() => pageFromHash());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const { t } = useTheme();

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const resize = () => setSidebarCollapsed(media.matches);
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const nextPage = pageFromHash();
      setCurrentPage((current) => (current === nextPage ? current : nextPage));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const nextHash = `#${currentPage}`;
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, "", nextHash);
    }
    const pageLabel = PAGE_LABELS[currentPage] ?? "Dashboard";
    document.title = `${pageLabel} | Customer Support | Smart Living`;
  }, [currentPage]);

  const renderPage = () => {
    switch (currentPage) {
      case "dashboard":    return <Dashboard />;
      case "customers":    return <CustomersPage />;
      case "archived":     return <ArchivedCustomersPage />;
      case "tickets":      return <TicketsPage />;
      case "calls":        return <CallsPage />;
      case "tasks":        return <TasksPage />;
      case "repeatpurchases": return <RepeatPurchasesPage />;
      case "targets":      return <TargetsPage />;
      case "deliveries":   return <DeliveriesPage />;
      case "satisfaction": return <SatisfactionPage />;
      case "reports":      return <ReportsPage />;
      case "notifications":return <NotificationsPage />;
      case "profile":      return <ProfilePage />;
      case "activitylogs": return <ActivityLogsPage />;
      default:             return <Dashboard />;
    }
  };

  return (
    <div className="flex h-screen overflow-hidden"
      style={{ fontFamily: "var(--font-family-body)", background: t.pageBg }}>
      <Sidebar
        currentPage={currentPage}
        onNavigate={setCurrentPage}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        onLogout={onLogout}
      />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <TopNav currentPage={currentPage} onLogout={onLogout} onNavigate={setCurrentPage} />
        <main
          className={`flex-1 min-h-0 ${SELF_SCROLL.has(currentPage) ? "overflow-hidden" : "overflow-y-auto"}`}
          style={{ background: t.pageBg }}
        >
          <Suspense fallback={<div className="flex h-full items-center justify-center" style={{ color: t.textMuted }}>Loading page…</div>}>
            {renderPage()}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
