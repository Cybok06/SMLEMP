import { useState, useEffect, useRef, useMemo } from 'react';
import {
  LayoutDashboard,
  Package,
  Users,
  Warehouse,
  Building2,
  Truck,
  ShoppingCart,
  ClipboardCheck,
  BarChart3,
  Settings as SettingsIcon,
  Search,
  Bell,
  ChevronLeft,
  ChevronRight,
  User,
  Lock,
  Sun,
  Moon,
  Layers,
  Zap,
  Cpu,
  AlertCircle,
  LogOut,
  FileText,
} from 'lucide-react';
import { Dashboard } from './components/Dashboard';
import { ProductCards } from './components/ProductCards';
import { CustomersCompletion } from './components/CustomersCompletion';
import { Inventory } from './components/Inventory';
import { WarehouseOperations } from './components/WarehouseOperations';
import { FulfillmentDelivery } from './components/FulfillmentDelivery';
import { UndeliveredItems } from './components/UndeliveredItems';
import { SubmittedCards } from './components/SubmittedCards';
import { SuppliersPurchases } from './components/SuppliersPurchases';
import { AuditAccountability } from './components/AuditAccountability';
import { ReportsAnalytics } from './components/ReportsAnalytics';
import { Settings, DEFAULT_ROLES, type Role } from './components/Settings';
import { NotificationCenter } from './components/NotificationCenter';
import { Toaster, toast } from 'sonner';
import { RoleAccessProvider } from './context/RoleAccessContext';

type InventorySessionUser = {
  id?: string;
  name?: string;
  username?: string;
  role?: string;
  is_main_admin?: boolean;
  inventory_role_id?: string;
  inventory_role_name?: string;
};

// ─── Sidebar config ─────────────────────────────────────────���─────────────────
const SIDEBAR_SECTIONS = [
  {
    label: 'Core',
    icon: Layers,
    items: [
      { id: 'logtime', label: 'Log Time / Leave', icon: ClipboardCheck, badge: null },
      { id: 'dashboard',     label: 'Dashboard',            icon: LayoutDashboard, badge: null },
      { id: 'product-cards', label: 'Product Cards',        icon: Package,         badge: null },
      { id: 'customers',     label: 'Customers',            icon: Users,           badge: 3 },
    ],
  },
  {
    label: 'Operations',
    icon: Zap,
    items: [
      { id: 'inventory',        label: 'Inventory',         icon: Warehouse,    badge: 2 },
      { id: 'warehouse',        label: 'Warehouse',         icon: Building2,    badge: null },
      { id: 'undelivered-items', label: 'Undelivered Items', icon: AlertCircle, badge: null },
      { id: 'submitted-cards',  label: 'Submitted Cards',   icon: FileText,     badge: 6 },
      { id: 'fulfillment',      label: 'Fulfillment',       icon: Truck,        badge: 5 },
      { id: 'suppliers',        label: 'Suppliers',         icon: ShoppingCart, badge: null },
    ],
  },
  {
    label: 'Intelligence',
    icon: Cpu,
    items: [
      { id: 'audit',   label: 'Audit & Accountability', icon: ClipboardCheck, badge: 4 },
      { id: 'reports', label: 'Reports & Analytics',    icon: BarChart3,      badge: null },
    ],
  },
  {
    label: 'System',
    icon: SettingsIcon,
    items: [
      { id: 'settings', label: 'Settings', icon: SettingsIcon, badge: null },
    ],
  },
];

const MENU_TITLE_MAP = SIDEBAR_SECTIONS.flatMap((section) => section.items).reduce<Record<string, string>>(
  (acc, item) => {
    acc[item.id] = item.label;
    return acc;
  },
  {}
);

// ─── Premium Sidebar ───────────────────────────────────────────────────────────
function PremiumSidebar({
  collapsed,
  onCollapse,
  activeMenu,
  onNavigate,
  visibleIds,
  currentRoleName,
  menuBadges,
}: {
  collapsed: boolean;
  onCollapse: () => void;
  activeMenu: string;
  onNavigate: (id: string) => void;
  visibleIds: Set<string>;
  currentRoleName: string;
  menuBadges: Record<string, number | null>;
}) {
  const sidebarStyle: React.CSSProperties = {
    background: 'linear-gradient(175deg, #0c0f1e 0%, #151033 30%, #1e1050 60%, #2a1060 100%)',
    boxShadow: '4px 0 24px rgba(79,46,229,0.18), 2px 0 8px rgba(0,0,0,0.4)',
    flexShrink: 0,
    width: collapsed ? 72 : 256,
    transition: 'width 300ms cubic-bezier(0.4,0,0.2,1)',
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
    overflow: 'hidden',
  };

  // Decorative orbs
  const orbStyle1: React.CSSProperties = {
    position: 'absolute', top: -60, right: -40, width: 180, height: 180,
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(139,92,246,0.18) 0%, transparent 70%)',
    pointerEvents: 'none',
  };
  const orbStyle2: React.CSSProperties = {
    position: 'absolute', bottom: 80, left: -60, width: 200, height: 200,
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(79,70,229,0.14) 0%, transparent 70%)',
    pointerEvents: 'none',
  };

  return (
    <aside style={sidebarStyle}>
      {/* decorative orbs */}
      <div style={orbStyle1} />
      <div style={orbStyle2} />

      {/* ── Brand header ── */}
      <div style={{
        height: 64, display: 'flex', alignItems: 'center',
        padding: collapsed ? '0 12px' : '0 16px',
        justifyContent: collapsed ? 'center' : 'space-between',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        background: 'rgba(255,255,255,0.03)',
        backdropFilter: 'blur(8px)',
        flexShrink: 0,
        gap: 8,
      }}>
        {/* Logo + name */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1, overflow: 'hidden' }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10, flexShrink: 0,
            overflow: 'hidden',
            boxShadow: '0 0 0 2px rgba(139,92,246,0.5), 0 4px 12px rgba(0,0,0,0.4)'
          }}>
            <img
              src="https://res.cloudinary.com/dljpgzbto/image/upload/v1743993646/company-logo_dksb23.jpg"
              alt="SMARTLIVING V2 Emporium Plus"
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          </div>
          {!collapsed && (
            <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden',
              opacity: collapsed ? 0 : 1, transition: 'opacity 200ms ease' }}>
              <span style={{ fontSize: 13, fontWeight: 800, color: '#fff', letterSpacing: '0.04em', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
                SMARTLIVING V2
              </span>
              <span style={{ fontSize: 10, fontWeight: 600, color: '#a78bfa', letterSpacing: '0.1em', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
                EMPORIUM PLUS
              </span>
            </div>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={onCollapse}
          style={{
            flexShrink: 0,
            width: 28, height: 28, borderRadius: 8,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.6)',
            cursor: 'pointer',
            transition: 'background 200ms, color 200ms',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.15)'; (e.currentTarget as HTMLElement).style.color = '#fff'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.08)'; (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.6)'; }}
        >
          {collapsed
            ? <ChevronRight style={{ width: 14, height: 14 }} />
            : <ChevronLeft  style={{ width: 14, height: 14 }} />}
        </button>
      </div>

      {/* ── Nav ── */}
      <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px 0', scrollbarWidth: 'none' }}>
        {SIDEBAR_SECTIONS.map((section) => {
          const visibleItems = section.items.filter(i => i.id === 'logtime' || visibleIds.has(i.id));
          if (visibleItems.length === 0) return null;
          return (
            <div key={section.label} style={{ marginBottom: 4 }}>
              {/* Section label */}
              {!collapsed && (
                <div style={{
                  padding: '10px 18px 4px',
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <section.icon style={{ width: 10, height: 10, color: 'rgba(167,139,250,0.6)' }} />
                  <span style={{
                    fontSize: 9.5, fontWeight: 700, letterSpacing: '0.12em',
                    color: 'rgba(167,139,250,0.6)', textTransform: 'uppercase',
                  }}>
                    {section.label}
                  </span>
                </div>
              )}
              {collapsed && <div style={{ height: 8 }} />}

              {/* Items */}
              {visibleItems.map(item => {
                const isActive = activeMenu === item.id;
                const Icon = item.icon;
                return (
                  <SidebarItem
                    key={item.id}
                    id={item.id}
                    label={item.label}
                    icon={Icon}
                    badge={Object.prototype.hasOwnProperty.call(menuBadges, item.id) ? menuBadges[item.id] : item.badge}
                    isActive={isActive}
                    collapsed={collapsed}
                    onClick={() => item.id === 'logtime' ? window.location.assign('/log-time') : onNavigate(item.id)}
                  />
                );
              })}
            </div>
          );
        })}
      </nav>

      {/* ── User footer ── */}
      <div style={{
        borderTop: '1px solid rgba(255,255,255,0.07)',
        padding: collapsed ? '12px 0' : '12px 14px',
        background: 'rgba(0,0,0,0.2)',
        backdropFilter: 'blur(8px)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: collapsed ? 'center' : 'flex-start',
        gap: 10,
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: '50%', flexShrink: 0,
          background: 'linear-gradient(135deg, #818cf8, #6d28d9)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 0 0 2px rgba(129,140,248,0.35)',
        }}>
          <User style={{ width: 15, height: 15, color: '#fff' }} />
        </div>
        {!collapsed && (
          <div style={{ minWidth: 0, overflow: 'hidden', flex: 1 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {currentRoleName}
            </div>
            <div style={{ fontSize: 10, color: 'rgba(167,139,250,0.7)', marginTop: 1 }}>Active session</div>
          </div>
        )}
        {!collapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#34d399',
              boxShadow: '0 0 6px rgba(52,211,153,0.8)' }} />
            <LogoutButton collapsed={false} />
          </div>
        )}
        {collapsed && <LogoutButton collapsed={true} />}
      </div>
    </aside>
  );
}

function LogoutButton({ collapsed }: { collapsed: boolean }) {
  const [hovered, setHovered] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  return (
    <button
      title="Log out"
      disabled={loggingOut}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => {
        if (window.confirm('Are you sure you want to log out?')) {
          setLoggingOut(true);
          window.location.href = '/logout';
        }
      }}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        width: collapsed ? 36 : 28,
        height: collapsed ? 36 : 28,
        borderRadius: collapsed ? 9 : 7,
        border: '1px solid rgba(255,255,255,0.1)',
        background: hovered ? 'rgba(244,63,94,0.22)' : 'rgba(255,255,255,0.07)',
        color: hovered ? '#f87171' : 'rgba(255,255,255,0.45)',
        cursor: loggingOut ? 'wait' : 'pointer',
        transition: 'background 200ms, color 200ms, border-color 200ms',
        borderColor: hovered ? 'rgba(244,63,94,0.35)' : 'rgba(255,255,255,0.1)',
        flexShrink: 0,
        opacity: loggingOut ? 0.7 : 1,
      }}
    >
      <LogOut style={{ width: 13, height: 13 }} />
    </button>
  );
}

// ─── Individual sidebar item ───────────────────────────────────────────────────
function SidebarItem({
  id, label, icon: Icon, badge, isActive, collapsed, onClick,
}: {
  id: string; label: string; icon: any; badge: number | null;
  isActive: boolean; collapsed: boolean; onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  const baseStyle: React.CSSProperties = {
    position: 'relative',
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: collapsed ? '10px 0' : '9px 14px 9px 16px',
    justifyContent: collapsed ? 'center' : 'flex-start',
    cursor: 'pointer',
    border: 'none',
    background: 'transparent',
    transition: 'background 220ms ease, box-shadow 220ms ease',
    margin: '1px 0',
    textAlign: 'left',
    // Active state
    ...(isActive ? {
      background: 'linear-gradient(90deg, rgba(99,102,241,0.30) 0%, rgba(139,92,246,0.12) 100%)',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05)',
    } : hovered ? {
      background: 'rgba(255,255,255,0.055)',
      boxShadow: 'inset 0 0 20px rgba(139,92,246,0.08)',
    } : {}),
  };

  const indicatorStyle: React.CSSProperties = {
    position: 'absolute', left: 0, top: '50%',
    transform: 'translateY(-50%)',
    width: 3, borderRadius: '0 3px 3px 0',
    background: 'linear-gradient(180deg, #818cf8, #6d28d9)',
    boxShadow: '0 0 8px rgba(129,140,248,0.7)',
    transition: 'height 250ms cubic-bezier(0.4,0,0.2,1), opacity 200ms ease',
    height: isActive ? 28 : hovered ? 12 : 0,
    opacity: isActive ? 1 : hovered ? 0.5 : 0,
  };

  const iconContainerStyle: React.CSSProperties = {
    width: 34, height: 34, borderRadius: 9,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    flexShrink: 0,
    transition: 'background 220ms ease, box-shadow 220ms ease',
    background: isActive
      ? 'linear-gradient(135deg, rgba(99,102,241,0.55), rgba(139,92,246,0.4))'
      : hovered
        ? 'rgba(255,255,255,0.07)'
        : 'rgba(255,255,255,0.04)',
    boxShadow: isActive ? '0 2px 10px rgba(99,102,241,0.35), inset 0 1px 0 rgba(255,255,255,0.15)' : 'none',
  };

  const iconColor = isActive ? '#c4b5fd' : hovered ? 'rgba(255,255,255,0.75)' : 'rgba(255,255,255,0.42)';

  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      style={baseStyle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Left indicator bar */}
      <span style={indicatorStyle} />

      {/* Icon */}
      <div style={iconContainerStyle}>
        <Icon style={{ width: 15, height: 15, color: iconColor, transition: 'color 200ms' }} />
      </div>

      {/* Label + badge */}
      {!collapsed && (
        <>
          <span style={{
            fontSize: 13, fontWeight: isActive ? 600 : 500,
            color: isActive ? '#e2e8f0' : hovered ? 'rgba(255,255,255,0.75)' : 'rgba(255,255,255,0.45)',
            transition: 'color 200ms',
            flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {label}
            {id === 'logtime' && <span aria-hidden="true" style={{marginLeft:6,padding:'2px 6px',borderRadius:999,background:'#d1fae5',color:'#065f46',fontSize:9,fontWeight:700,border:'1px solid #6ee7b7'}}>New</span>}
          </span>
          {badge !== null && (
            <span style={{
              fontSize: 10, fontWeight: 700, lineHeight: 1,
              padding: '2px 6px', borderRadius: 20,
              background: badge > 0
                ? 'linear-gradient(135deg, #f43f5e, #e11d48)'
                : 'rgba(255,255,255,0.08)',
              color: '#fff',
              boxShadow: badge > 0 ? '0 2px 8px rgba(244,63,94,0.5)' : 'none',
              flexShrink: 0,
              minWidth: 20, textAlign: 'center',
            }}>
              {badge}
            </span>
          )}
        </>
      )}

      {/* Collapsed badge dot */}
      {collapsed && badge !== null && badge > 0 && (
        <span style={{
          position: 'absolute', top: 6, right: 10,
          width: 7, height: 7, borderRadius: '50%',
          background: '#f43f5e',
          boxShadow: '0 0 6px rgba(244,63,94,0.7)',
        }} />
      )}
    </button>
  );
}

// ─── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeMenu, setActiveMenu] = useState('dashboard');
  const [auditTabOverride, setAuditTabOverride] = useState<string | null>(null);
  const [roles, setRoles] = useState<Role[]>(DEFAULT_ROLES);
  const [currentRoleId, setCurrentRoleId] = useState('inventory-user');
  const [sessionUser, setSessionUser] = useState<InventorySessionUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState('');
  const [menuBadges, setMenuBadges] = useState<Record<string, number | null>>({});
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window === 'undefined') return 'light';
    const stored = localStorage.getItem('sl-theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    root.style.colorScheme = theme;
    localStorage.setItem('sl-theme', theme);
  }, [theme]);

  useEffect(() => {
    let isMounted = true;

    fetch('/api/inventory/session', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Session request failed: ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        if (!isMounted) return;
        if (!data?.ok || !data?.user) throw new Error('Unable to load your inventory session.');
        const user = data?.user ?? null;
        const apiRoles = Array.isArray(data?.roles) ? data.roles : [];
        setSessionUser(user);
        if (apiRoles.length > 0) {
          setRoles(apiRoles);
        }
        setCurrentRoleId(data?.effective_role_id || user?.inventory_role_id || (user?.is_main_admin ? 'admin' : 'inventory-user'));
      })
      .catch((error) => {
        if (!isMounted) return;
        setSessionUser(null);
        setCurrentRoleId('inventory-user');
        setSessionError(error instanceof Error ? error.message : 'Unable to load your inventory session.');
      })
      .finally(() => {
        if (isMounted) setSessionLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const loadSubmittedCardCounts = async () => {
    try {
      const response = await fetch('/api/inventory/submitted-cards/counts', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const data = await response.json();
      if (!response.ok || !data?.ok) {
        throw new Error(data?.error || `Count request failed: ${response.status}`);
      }
      setMenuBadges((current) => ({ ...current, 'submitted-cards': Number(data?.counts?.open ?? 0) }));
    } catch {
      setMenuBadges((current) => current);
    }
  };

  useEffect(() => {
    void loadSubmittedCardCounts();
  }, []);

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));

  const [notifOpen, setNotifOpen] = useState(false);
  const bellRef = useRef<HTMLButtonElement>(null);

  const currentRole: Role = roles.find((r) => r.id === currentRoleId) || {
    id: 'unassigned', name: 'No inventory role assigned', description: '', permissions: {},
  };
  const isMainAdmin = Boolean(sessionUser?.is_main_admin);

  const visibleIds = useMemo(
    () => new Set(
      Object.entries(currentRole.permissions)
        .filter(([id, p]) => p?.visible && p.view && (id !== 'settings' || isMainAdmin))
        .map(([id]) => id)
    ),
    [currentRole, isMainAdmin]
  );

  const allIds = SIDEBAR_SECTIONS.flatMap(s => s.items.map(i => i.id));

  useEffect(() => {
    if (!visibleIds.has(activeMenu)) {
      const first = allIds.find(id => visibleIds.has(id));
      if (first) setActiveMenu(first);
    }
  }, [visibleIds]);

  const canAccess = (id: string) => visibleIds.has(id);
  const navigate = (id: string) => {
    if (!canAccess(id)) {
      toast.error('Your role does not have access to this page.');
      return;
    }
    setActiveMenu(id);
  };

  useEffect(() => {
    const activeTitle = auditTabOverride && activeMenu === 'audit'
      ? 'Stock Taking'
      : MENU_TITLE_MAP[activeMenu] || 'Inventory Dashboard';
    document.title = `${activeTitle} | SMARTLIVING V2`;
  }, [activeMenu, auditTabOverride]);

  if (sessionLoading || sessionError) {
    return (
      <div className="size-full flex items-center justify-center bg-background text-foreground">
        <div className="sl-card p-8 text-center" role={sessionError ? 'alert' : 'status'}>
          <p>{sessionError || 'Loading your inventory session...'}</p>
          {sessionError && <button className="sl-button mt-4" onClick={() => window.location.reload()}>Retry</button>}
        </div>
      </div>
    );
  }

  return (
    <RoleAccessProvider currentRole={currentRole}>
      <div className="size-full flex bg-background text-foreground transition-colors duration-300" style={{ overflow: 'hidden' }}>
        <Toaster richColors position="top-right" theme={theme} closeButton />

        {/* ── Premium Sidebar ── */}
        <PremiumSidebar
          collapsed={sidebarCollapsed}
          onCollapse={() => setSidebarCollapsed(c => !c)}
          activeMenu={activeMenu}
          onNavigate={navigate}
          visibleIds={visibleIds}
          currentRoleName={currentRole.name}
          menuBadges={menuBadges}
        />

        {/* ── Main content ── */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Topbar */}
          <header className="sl-navbar h-16 flex items-center justify-between px-4 sm:px-6 transition-colors duration-300 flex-shrink-0">
            <div className="flex-1 max-w-xl">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search customers, products, orders..."
                  className="sl-input pl-10"
                />
              </div>
            </div>
            <div className="flex items-center gap-2 sm:gap-3">
              <div className="relative">
                <button
                  ref={bellRef}
                  onClick={() => setNotifOpen((o) => !o)}
                  aria-label="Open notifications"
                  aria-expanded={notifOpen}
                  className="relative p-2 rounded-lg hover:bg-muted transition-colors"
                >
                  <Bell className="w-5 h-5 text-muted-foreground" />
                  <span className="absolute top-1 right-1 w-2 h-2 bg-[var(--danger-500)] rounded-full ring-2 ring-[var(--card)]" />
                </button>
                <NotificationCenter
                  open={notifOpen}
                  onClose={() => setNotifOpen(false)}
                  onNavigate={navigate}
                  anchorRef={bellRef as React.RefObject<HTMLElement>}
                />
              </div>

              <ThemeToggle theme={theme} onToggle={toggleTheme} />

              <div className="hidden sm:flex flex-col items-end leading-tight">
                <span className="text-sm font-medium text-foreground">{sessionUser?.name || 'Inventory User'}</span>
                <span className="text-xs text-muted-foreground">{currentRole.name}</span>
              </div>

              <button className="p-1 rounded-full hover:bg-muted transition-colors" aria-label="Profile">
                <div className="w-9 h-9 rounded-full flex items-center justify-center text-white shadow-sm"
                  style={{ background: 'linear-gradient(135deg, var(--brand-500), var(--brand-700))' }}>
                  <User className="w-5 h-5" />
                </div>
              </button>
            </div>
          </header>

          {/* Page content */}
          <main className="flex-1 overflow-auto p-6">
            {!canAccess(activeMenu) ? (
              <AccessDenied />
            ) : (
              <>
                {activeMenu === 'dashboard'     && <Dashboard onNavigate={navigate} />}
                {activeMenu === 'product-cards' && <ProductCards />}
                {activeMenu === 'customers'     && <CustomersCompletion />}
                {activeMenu === 'inventory'     && <Inventory onNavigate={(menu, tab) => {
                  if (menu === 'audit' && tab) {
                    setAuditTabOverride(tab);
                  }
                  navigate(menu);
                }} />}
                {activeMenu === 'undelivered-items' && <UndeliveredItems />}
                {activeMenu === 'warehouse'       && <WarehouseOperations />}
                {activeMenu === 'fulfillment'     && <FulfillmentDelivery />}
                {activeMenu === 'submitted-cards' && <SubmittedCards onCountsChange={loadSubmittedCardCounts} />}
                {activeMenu === 'suppliers'       && <SuppliersPurchases currentRole={currentRole} />}
                {activeMenu === 'audit'         && <AuditAccountability
                  defaultTab={auditTabOverride}
                  onTabChange={() => setAuditTabOverride(null)}
                />}
                {activeMenu === 'reports'       && <ReportsAnalytics />}
                {activeMenu === 'settings'      && (
                  <Settings
                    roles={roles}
                    setRoles={setRoles}
                    currentRoleId={currentRoleId}
                    setCurrentRoleId={setCurrentRoleId}
                  />
                )}
              </>
            )}
          </main>

          {/* Footer */}
          <footer className="flex-shrink-0 border-t border-gray-200 bg-white/80 backdrop-blur-sm px-6 py-2.5 flex items-center justify-center gap-2">
            <span className="text-xs text-gray-400 tracking-wide">Powered by</span>
            <a
              href="https://axeleraflux.com/"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center opacity-75 hover:opacity-100 transition-opacity duration-200"
              aria-label="AxeleraFlux"
            >
              <img
                src="https://axeleraflux.com/assets/company_logo-Bz3g_-8-.png"
                alt="AxeleraFlux"
                className="h-5 w-auto object-contain"
              />
            </a>
          </footer>
        </div>
      </div>
    </RoleAccessProvider>
  );
}

// ─── Theme Toggle ──────────────────────────────────────────────────────────────
function ThemeToggle({ theme, onToggle }: { theme: 'light' | 'dark'; onToggle: () => void }) {
  const isDark = theme === 'dark';
  return (
    <button
      onClick={onToggle}
      role="switch"
      aria-checked={isDark}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="relative inline-flex items-center h-9 w-16 rounded-full border border-border transition-colors duration-300 focus:outline-none"
      style={{
        background: isDark
          ? 'linear-gradient(135deg, #1e293b, #0f172a)'
          : 'linear-gradient(135deg, #e0e7ff, #f1f5f9)',
      }}
    >
      <span className="absolute left-2 text-amber-500 transition-opacity duration-300" style={{ opacity: isDark ? 0.35 : 1 }}>
        <Sun className="w-4 h-4" />
      </span>
      <span className="absolute right-2 text-indigo-200 transition-opacity duration-300" style={{ opacity: isDark ? 1 : 0.35 }}>
        <Moon className="w-4 h-4" />
      </span>
      <span
        className="absolute top-1 h-7 w-7 rounded-full shadow-md transition-transform duration-300 ease-out flex items-center justify-center"
        style={{
          transform: isDark ? 'translateX(30px)' : 'translateX(4px)',
          background: isDark ? 'linear-gradient(135deg, #818cf8, #4f46e5)' : '#ffffff',
        }}
      >
        {isDark
          ? <Moon className="w-3.5 h-3.5 text-white" />
          : <Sun  className="w-3.5 h-3.5 text-amber-500" />}
      </span>
    </button>
  );
}

// ─── Access Denied ─────────────────────────────────────────────────────────────
function AccessDenied() {
  return (
    <div className="sl-card p-12 text-center max-w-xl mx-auto mt-12">
      <div className="w-14 h-14 rounded-full flex items-center justify-center mx-auto mb-4"
        style={{ background: 'var(--danger-50)' }}>
        <Lock className="w-7 h-7" style={{ color: 'var(--danger-600)' }} />
      </div>
      <h3 className="text-lg font-semibold text-foreground mb-1">Access restricted</h3>
      <p className="text-sm text-muted-foreground">
        Your current role does not have permission to view this page. Contact an administrator if you need access.
      </p>
    </div>
  );
}
