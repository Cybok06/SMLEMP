import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  AlertOctagon,
  Clock,
  CheckCircle2,
  Package,
  Truck,
  ClipboardCheck,
  ShoppingCart,
  Users,
  Info,
  X,
  RefreshCw,
  ArrowRight,
  Bell
} from 'lucide-react';

export type NotificationSeverity = 'critical' | 'operational' | 'info';

export type NotificationItem = {
  id: string;
  severity: NotificationSeverity;
  category: 'inventory' | 'forecast' | 'warehouse' | 'fulfillment' | 'audit' | 'customer' | 'supplier';
  title: string;
  description: string;
  createdAt: string;
  read: boolean;
  group: 'today' | 'yesterday' | 'earlier';
  actions: { label: string; target: string; primary?: boolean }[];
};

const initialNotifications: NotificationItem[] = [
  {
    id: 'N-001',
    severity: 'critical',
    category: 'inventory',
    title: 'Stock shortage — 65" Smart TV',
    description: 'Forecasted demand (300) exceeds available stock (45) for the next 30 days.',
    createdAt: '2 min ago',
    read: false,
    group: 'today',
    actions: [
      { label: 'Create PO', target: 'suppliers', primary: true },
      { label: 'View Details', target: 'inventory' }
    ]
  },
  {
    id: 'N-002',
    severity: 'critical',
    category: 'forecast',
    title: '12 customers at 90%+ with no stock ready',
    description: 'Refrigerator inventory cannot fulfill upcoming completions in this branch.',
    createdAt: '14 min ago',
    read: false,
    group: 'today',
    actions: [
      { label: 'Create PO', target: 'suppliers', primary: true },
      { label: 'View Customers', target: 'customers' }
    ]
  },
  {
    id: 'N-003',
    severity: 'critical',
    category: 'fulfillment',
    title: 'Delivery failed — DLV-2891',
    description: 'Customer C-2847 not available; package returned to warehouse.',
    createdAt: '38 min ago',
    read: false,
    group: 'today',
    actions: [
      { label: 'Reschedule Delivery', target: 'fulfillment', primary: true },
      { label: 'View Details', target: 'fulfillment' }
    ]
  },
  {
    id: 'N-004',
    severity: 'critical',
    category: 'audit',
    title: 'Inventory discrepancy detected',
    description: 'Cycle count for SKU TV-65-001 shows -8 units versus system records.',
    createdAt: '1 hr ago',
    read: false,
    group: 'today',
    actions: [
      { label: 'Start Investigation', target: 'audit', primary: true },
      { label: 'View Ledger', target: 'inventory' }
    ]
  },
  {
    id: 'N-005',
    severity: 'operational',
    category: 'warehouse',
    title: 'Branch request pending approval',
    description: 'North Branch requested 45 units of refrigerators.',
    createdAt: '1 hr ago',
    read: false,
    group: 'today',
    actions: [
      { label: 'Approve Request', target: 'warehouse', primary: true },
      { label: 'Review', target: 'warehouse' }
    ]
  },
  {
    id: 'N-006',
    severity: 'operational',
    category: 'fulfillment',
    title: 'Dispatch delayed — DLV-2885',
    description: 'Vehicle still pending assignment; customer slot in 2 hours.',
    createdAt: '2 hr ago',
    read: true,
    group: 'today',
    actions: [{ label: 'Dispatch Now', target: 'fulfillment', primary: true }]
  },
  {
    id: 'N-007',
    severity: 'operational',
    category: 'supplier',
    title: 'Supplier delivery awaiting confirmation',
    description: 'PO-1232 partial delivery received — sign-off required.',
    createdAt: '3 hr ago',
    read: true,
    group: 'today',
    actions: [
      { label: 'Confirm Receipt', target: 'suppliers', primary: true },
      { label: 'Open PO', target: 'suppliers' }
    ]
  },
  {
    id: 'N-008',
    severity: 'info',
    category: 'fulfillment',
    title: 'Delivery completed — DLV-2882',
    description: 'Customer C-2810 received order successfully.',
    createdAt: '4 hr ago',
    read: true,
    group: 'today',
    actions: [{ label: 'View', target: 'fulfillment' }]
  },
  {
    id: 'N-009',
    severity: 'info',
    category: 'supplier',
    title: 'Stock received — GRN-5677',
    description: '200 units of Wall Art Set added to Main Warehouse.',
    createdAt: 'Yesterday',
    read: true,
    group: 'yesterday',
    actions: [{ label: 'View Inventory', target: 'inventory' }]
  },
  {
    id: 'N-010',
    severity: 'info',
    category: 'customer',
    title: 'Customer completed payment',
    description: 'Customer C-2834 finished installment plan.',
    createdAt: 'Yesterday',
    read: true,
    group: 'yesterday',
    actions: [{ label: 'View Customer', target: 'customers' }]
  },
  {
    id: 'N-011',
    severity: 'info',
    category: 'inventory',
    title: 'New product card created',
    description: 'PC-1042 — Premium Living Set was added by Sarah Miller.',
    createdAt: '2 days ago',
    read: true,
    group: 'earlier',
    actions: [{ label: 'Open Card', target: 'product-cards' }]
  }
];

const severityStyles: Record<
  NotificationSeverity,
  { dot: string; chip: string; label: string; ring: string }
> = {
  critical: {
    dot: 'bg-rose-500',
    chip: 'bg-rose-50 text-rose-700 border-rose-200',
    label: 'Critical',
    ring: 'ring-rose-200'
  },
  operational: {
    dot: 'bg-amber-500',
    chip: 'bg-amber-50 text-amber-700 border-amber-200',
    label: 'Pending',
    ring: 'ring-amber-200'
  },
  info: {
    dot: 'bg-sky-500',
    chip: 'bg-sky-50 text-sky-700 border-sky-200',
    label: 'Info',
    ring: 'ring-sky-200'
  }
};

const categoryIcon: Record<NotificationItem['category'], any> = {
  inventory: Package,
  forecast: AlertTriangle,
  warehouse: ClipboardCheck,
  fulfillment: Truck,
  audit: AlertOctagon,
  customer: Users,
  supplier: ShoppingCart
};

type Tab = 'all' | 'critical' | 'operational' | 'info';

type Props = {
  open: boolean;
  onClose: () => void;
  onNavigate: (target: string) => void;
  /** anchor element for outside-click ignore */
  anchorRef?: React.RefObject<HTMLElement>;
};

export function NotificationCenter({ open, onClose, onNavigate, anchorRef }: Props) {
  const [items, setItems] = useState<NotificationItem[]>(initialNotifications);
  const [tab, setTab] = useState<Tab>('all');
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t)) return;
      if (anchorRef?.current?.contains(t)) return;
      onClose();
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', esc);
    };
  }, [open, onClose, anchorRef]);

  const unreadCount = items.filter((i) => !i.read).length;

  const filtered = useMemo(() => {
    if (tab === 'all') return items;
    return items.filter((i) => i.severity === tab);
  }, [tab, items]);

  const grouped = useMemo(() => {
    const groups: Record<'today' | 'yesterday' | 'earlier', NotificationItem[]> = {
      today: [],
      yesterday: [],
      earlier: []
    };
    filtered.forEach((n) => groups[n.group].push(n));
    return groups;
  }, [filtered]);

  const markAllRead = () => setItems(items.map((i) => ({ ...i, read: true })));
  const markRead = (id: string) =>
    setItems(items.map((i) => (i.id === id ? { ...i, read: true } : i)));
  const refresh = () => {
    // Demo: simulate a fresh critical alert at the top
    setItems((prev) => [
      {
        id: `N-${Math.random().toString(36).slice(2, 7).toUpperCase()}`,
        severity: 'critical',
        category: 'inventory',
        title: 'New low-stock alert — AC Unit 1.5T',
        description: 'Available stock dropped below threshold (8 units).',
        createdAt: 'Just now',
        read: false,
        group: 'today',
        actions: [
          { label: 'Create PO', target: 'suppliers', primary: true },
          { label: 'View Details', target: 'inventory' }
        ]
      },
      ...prev
    ]);
  };

  if (!open) return null;

  const tabs: { id: Tab; label: string; count: number }[] = [
    { id: 'all', label: 'All', count: items.length },
    { id: 'critical', label: 'Critical', count: items.filter((i) => i.severity === 'critical').length },
    {
      id: 'operational',
      label: 'Operational',
      count: items.filter((i) => i.severity === 'operational').length
    },
    { id: 'info', label: 'Info', count: items.filter((i) => i.severity === 'info').length }
  ];

  return (
    <div
      ref={panelRef}
      className="absolute right-2 sm:right-4 top-[60px] z-50 w-[380px] sm:w-[440px] max-w-[calc(100vw-1rem)] sl-popover overflow-hidden"
      style={{ animation: 'sl-pop .18s ease', background: 'var(--popover)', backgroundColor: 'var(--popover)' }}
      role="dialog"
      aria-label="Notifications"
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: 'var(--accent)' }}
          >
            <Bell className="w-4 h-4" style={{ color: 'var(--accent-foreground)' }} />
          </div>
          <div>
            <div className="text-sm font-semibold text-foreground">Notifications</div>
            <div className="text-xs text-muted-foreground">
              {unreadCount > 0 ? `${unreadCount} unread` : 'You are all caught up'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={refresh}
            title="Refresh"
            className="p-1.5 rounded-md hover:bg-muted transition-colors"
          >
            <RefreshCw className="w-4 h-4 text-muted-foreground" />
          </button>
          <button
            onClick={markAllRead}
            className="text-xs font-medium px-2 py-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            Mark all as read
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md hover:bg-muted transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4 text-muted-foreground" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 px-2 pt-2 border-b border-border">
        {tabs.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-md border-b-2 -mb-px transition-colors ${
                active
                  ? 'text-[var(--brand-700)] border-[var(--brand-600)] bg-[color-mix(in_oklab,var(--brand-50)_60%,transparent)]'
                  : 'text-muted-foreground border-transparent hover:text-foreground hover:bg-muted'
              }`}
            >
              {t.label}
              <span
                className={`inline-flex min-w-[18px] h-[18px] items-center justify-center px-1 rounded-full text-[10px] font-semibold ${
                  active
                    ? 'bg-[var(--brand-600)] text-white'
                    : 'bg-muted text-muted-foreground'
                }`}
              >
                {t.count}
              </span>
            </button>
          );
        })}
      </div>

      {/* List */}
      <div className="max-h-[480px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="py-12 text-center text-sm text-muted-foreground">
            <CheckCircle2 className="w-8 h-8 mx-auto mb-2 text-emerald-500" />
            No notifications in this view.
          </div>
        ) : (
          (['today', 'yesterday', 'earlier'] as const).map((g) =>
            grouped[g].length === 0 ? null : (
              <div key={g}>
                <div
                  className="px-4 py-2 text-[10px] uppercase tracking-wider font-semibold text-muted-foreground sticky top-0 border-b border-border"
                  style={{ background: 'var(--muted)' }}
                >
                  {g === 'today' ? 'Today' : g === 'yesterday' ? 'Yesterday' : 'Earlier'}
                </div>
                <ul>
                  {grouped[g].map((n) => (
                    <NotificationRow
                      key={n.id}
                      item={n}
                      onMarkRead={() => markRead(n.id)}
                      onNavigate={(target) => {
                        markRead(n.id);
                        onNavigate(target);
                        onClose();
                      }}
                    />
                  ))}
                </ul>
              </div>
            )
          )
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2.5 border-t border-border flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Real-time alert center</span>
        <button
          onClick={() => {
            onNavigate('audit');
            onClose();
          }}
          className="text-xs font-medium text-[var(--brand-700)] hover:underline inline-flex items-center gap-1"
        >
          View all activity
          <ArrowRight className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

function NotificationRow({
  item,
  onMarkRead,
  onNavigate
}: {
  item: NotificationItem;
  onMarkRead: () => void;
  onNavigate: (target: string) => void;
}) {
  const Icon = categoryIcon[item.category] || Info;
  const sty = severityStyles[item.severity];

  return (
    <li
      className={`relative px-4 py-3 border-b border-border last:border-0 transition-colors ${
        !item.read ? 'bg-[color-mix(in_oklab,var(--brand-50)_30%,transparent)]' : 'hover:bg-muted/40'
      }`}
    >
      {!item.read && (
        <span
          className="absolute left-1.5 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-[var(--brand-600)]"
          aria-label="Unread"
        />
      )}
      <div className="flex items-start gap-3">
        <div
          className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center border ${sty.chip}`}
        >
          <Icon className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-medium text-foreground leading-snug">{item.title}</div>
            <span
              className={`shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded-full border ${sty.chip}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${sty.dot}`} />
              {sty.label}
            </span>
          </div>
          <div className="text-xs text-muted-foreground mt-0.5 leading-snug">{item.description}</div>
          <div className="flex items-center justify-between mt-2 gap-2">
            <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {item.createdAt}
            </span>
            <div className="flex items-center gap-1.5 flex-wrap justify-end">
              {item.actions.map((a) => (
                <button
                  key={a.label}
                  onClick={() => onNavigate(a.target)}
                  className={`text-xs font-medium px-2.5 py-1 rounded-md transition-colors ${
                    a.primary
                      ? 'text-white bg-[var(--brand-600)] hover:bg-[var(--brand-700)] shadow-sm'
                      : 'text-foreground border border-border hover:bg-muted'
                  }`}
                >
                  {a.label}
                </button>
              ))}
              {!item.read && (
                <button
                  onClick={onMarkRead}
                  title="Mark as read"
                  className="text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Mark read
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </li>
  );
}
