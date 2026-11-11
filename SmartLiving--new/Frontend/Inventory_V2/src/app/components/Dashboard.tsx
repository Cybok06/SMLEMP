import { WorkTimeSummary } from "./WorkTimeSummary";
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  CheckCircle,
  Clock,
  FileText,
  Package,
  Plus,
  Truck,
  Users,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { toast } from 'sonner';
import { useAccessSafe } from '../context/RoleAccessContext';
import { AccessBanner, PriceMask } from './PriceGuard';

type DashboardProps = {
  onNavigate?: (target: string) => void;
};

type DashboardPayload = {
  ok: boolean;
  metrics?: {
    customers90Plus: number;
    completedCustomers: number;
    inventoryValue: number;
    lowStockItems: number;
    pendingBranchRequests: number;
    undeliveredCustomers: number;
    auditLossValue: number;
    openInvestigations: number;
  };
  deliveriesByBranch?: Array<{ branch: string; deliveries: number }>;
  outflowThisWeek?: Array<{ day: string; units: number }>;
  lostStockThisWeek?: Array<{ day: string; units: number }>;
  completionTrend?: Array<{ month: string; completed: number; partial: number; total: number }>;
  inventoryMovement?: Array<{ date?: string; week?: string; inbound: number; outbound: number; adjustments: number }>;
  stockShortage?: Array<{ item?: string; currentStock?: number; forecast?: number; riskScore?: number; daysUntilStockout?: number }>;
  branchPerformance?: Array<{ branch: string; fulfillment: number; inventory: number }>;
  inventoryHealth?: Array<{ status: string; count: number; value: number; color: string }>;
  forecastAlerts?: Array<{ level: string; customers: number; estimatedCompletion: string; requiredStock: number; status: string }>;
  recentActivities?: Array<{ id: string; type: string; message: string; time: string; icon: string; color: string }>;
  error?: string;
};

const TOOLTIP_STYLE = { borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 };
const DASHBOARD_SECTIONS = ['metrics', 'pulse', 'middle', 'lower', 'activity'] as const;
type DashboardSection = (typeof DASHBOARD_SECTIONS)[number];

function buildSectionState(ready: boolean): Record<DashboardSection, boolean> {
  return {
    metrics: ready,
    pulse: ready,
    middle: ready,
    lower: ready,
    activity: ready,
  };
}

function MetricCard({
  label,
  value,
  sub,
  icon: Icon,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  icon: any;
  tone: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between">
        <div className="text-sm text-gray-600">{label}</div>
        <div className={`rounded-lg p-2 ${tone}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
      <div className="text-2xl font-semibold text-gray-900">{value}</div>
      {sub ? <div className="mt-1 text-xs text-gray-500">{sub}</div> : null}
    </div>
  );
}

function SectionTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <h3 className="font-semibold text-gray-900">{title}</h3>
      {sub ? <p className="mt-0.5 text-xs text-gray-500">{sub}</p> : null}
    </div>
  );
}

function SkeletonCard({ rows = 3, className = '' }: { rows?: number; className?: string }) {
  return (
    <div className={`animate-pulse rounded-xl border border-gray-200 bg-white p-5 ${className}`}>
      <div className="mb-4 h-5 w-44 rounded bg-gray-200" />
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="h-12 rounded-lg bg-gray-100" />
        ))}
      </div>
    </div>
  );
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function Dashboard({ onNavigate }: DashboardProps = {}) {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [sectionReady, setSectionReady] = useState<Record<DashboardSection, boolean>>(buildSectionState(false));
  const revealTimersRef = useRef<number[]>([]);
  const { canViewPricing, canAccessPage } = useAccessSafe();

  const goTo = (target: string, label: string) => {
    if (onNavigate) {
      onNavigate(target);
      toast.success(`Opening ${label}`, { description: 'Redirecting you to the right page.' });
      return;
    }
    toast.info(label);
  };

  const clearRevealTimers = () => {
    revealTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    revealTimersRef.current = [];
  };

  const revealSectionsSequentially = () => {
    clearRevealTimers();
    setSectionReady(buildSectionState(false));
    DASHBOARD_SECTIONS.forEach((section, index) => {
      const timer = window.setTimeout(() => {
        setSectionReady((current) => ({ ...current, [section]: true }));
      }, index * 140);
      revealTimersRef.current.push(timer);
    });
  };

  const loadDashboard = async (signal?: AbortSignal) => {
    const hadPayload = Boolean(payload);
    if (!hadPayload) {
      setLoading(true);
      setSectionReady(buildSectionState(false));
    } else {
      setRefreshing(true);
    }

    try {
      const response = await fetch('/api/inventory/dashboard', {
        credentials: 'same-origin',
        signal,
        headers: { Accept: 'application/json' },
      });
      const nextPayload = await parseJsonResponse<DashboardPayload>(response);
      if (!response.ok || !nextPayload.ok) {
        throw new Error(nextPayload.error || 'Unable to load dashboard data.');
      }
      setPayload(nextPayload);

      if (hadPayload) {
        setSectionReady(buildSectionState(true));
      } else {
        revealSectionsSequentially();
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      toast.error(error instanceof Error ? error.message : 'Unable to load dashboard data.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadDashboard(controller.signal);
    return () => {
      controller.abort();
      clearRevealTimers();
    };
  }, []);

  const metrics = payload?.metrics;
  const totalDeliveries = useMemo(
    () => (payload?.deliveriesByBranch || []).reduce((sum, row) => sum + Number(row.deliveries || 0), 0),
    [payload],
  );
  const totalOutflow = useMemo(
    () => (payload?.outflowThisWeek || []).reduce((sum, row) => sum + Number(row.units || 0), 0),
    [payload],
  );
  const totalLost = useMemo(
    () => (payload?.lostStockThisWeek || []).reduce((sum, row) => sum + Number(row.units || 0), 0),
    [payload],
  );
  const maxInventoryHealthCount = useMemo(
    () => Math.max(1, ...(payload?.inventoryHealth || []).map((item) => Number(item.count || 0))),
    [payload],
  );

  return (
    <div className="space-y-6">
      <AccessBanner />
      <WorkTimeSummary />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
          <p className="mt-1 text-gray-600">Live command view of customer completion pressure, stock flow, branch fulfillment, and current audit risk.</p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!canAccessPage('reports', 'view')}
            onClick={() => goTo('reports', 'Reports & Analytics')}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <BarChart3 className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">View Reports</span>
          </button>
          <button
            type="button"
            disabled={!canAccessPage('product-cards', 'create')}
            onClick={() => goTo('product-cards', 'Product Cards')}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <FileText className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Create Product Card</span>
          </button>
          <button
            type="button"
            disabled={!canAccessPage('warehouse', 'create')}
            onClick={() => goTo('warehouse', 'Warehouse Operations')}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <Clock className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Create Request</span>
          </button>
          <button
            type="button"
            disabled={!canAccessPage('inventory', 'edit')}
            onClick={() => goTo('inventory', 'Inventory')}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-white transition-colors hover:bg-indigo-700"
          >
            <Plus className="h-4 w-4" />
            <span className="text-sm font-medium">Add Stock</span>
          </button>
        </div>
      </div>

      {refreshing ? (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-700">
          Refreshing dashboard data in the background...
        </div>
      ) : null}

      {!payload && loading ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <SkeletonCard key={index} rows={2} />
            ))}
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <SkeletonCard rows={4} />
            <SkeletonCard rows={4} />
            <SkeletonCard rows={4} />
          </div>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <SkeletonCard className="lg:col-span-2" rows={5} />
            <SkeletonCard rows={5} />
          </div>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SkeletonCard rows={5} />
            <SkeletonCard rows={5} />
          </div>
          <SkeletonCard rows={6} />
        </>
      ) : payload ? (
        <>
          {sectionReady.metrics ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
              <MetricCard label="Customers 90%+" value={metrics?.customers90Plus || 0} sub="Near completion" icon={Users} tone="bg-violet-50 text-violet-600" />
              <MetricCard label="Completed Customers" value={metrics?.completedCustomers || 0} sub="Paid or delivered" icon={CheckCircle} tone="bg-green-50 text-green-600" />
              <MetricCard label="Inventory Value" value={<PriceMask value={metrics?.inventoryValue || 0} />} sub={canViewPricing ? 'Current stock position' : 'Masked by role'} icon={Package} tone="bg-emerald-50 text-emerald-600" />
              <MetricCard label="Low Stock Items" value={metrics?.lowStockItems || 0} sub="Critical attention" icon={AlertTriangle} tone="bg-amber-50 text-amber-600" />
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <SkeletonCard key={index} rows={2} />
              ))}
            </div>
          )}

          {sectionReady.pulse ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-xl border border-gray-200 bg-white p-5">
                <div className="mb-1 flex items-start justify-between">
                  <div>
                    <div className="text-sm text-gray-600">Deliveries this week</div>
                    <div className="mt-1 text-2xl font-semibold text-gray-900">{totalDeliveries.toLocaleString()}</div>
                    <div className="mt-0.5 text-xs text-gray-500">Delivered packages by branch</div>
                  </div>
                  <div className="rounded-lg bg-indigo-50 p-2"><Truck className="h-5 w-5 text-indigo-600" /></div>
                </div>
                <div className="mb-3 flex items-center gap-1 text-xs font-medium text-emerald-600"><ArrowUpRight className="h-3.5 w-3.5" />Live branch-delivery signal</div>
                <ResponsiveContainer width="100%" height={110}>
                  <BarChart data={payload.deliveriesByBranch || []}>
                    <XAxis dataKey="branch" stroke="#6b7280" fontSize={11} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Bar dataKey="deliveries" fill="#6366f1" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-5">
                <div className="mb-1 flex items-start justify-between">
                  <div>
                    <div className="text-sm text-gray-600">Stock outflow this week</div>
                    <div className="mt-1 text-2xl font-semibold text-gray-900">{totalOutflow.toLocaleString()}</div>
                    <div className="mt-0.5 text-xs text-gray-500">Units moved out of inventory</div>
                  </div>
                  <div className="rounded-lg bg-emerald-50 p-2"><Package className="h-5 w-5 text-emerald-600" /></div>
                </div>
                <ResponsiveContainer width="100%" height={110}>
                  <AreaChart data={payload.outflowThisWeek || []}>
                    <defs>
                      <linearGradient id="dashOutflow" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#10b981" stopOpacity={0.45} />
                        <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="day" stroke="#6b7280" fontSize={11} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Area type="monotone" dataKey="units" stroke="#10b981" fill="url(#dashOutflow)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-5">
                <div className="mb-1 flex items-start justify-between">
                  <div>
                    <div className="text-sm text-gray-600">Lost stock this week</div>
                    <div className="mt-1 text-2xl font-semibold text-gray-900">{totalLost.toLocaleString()}</div>
                    <div className="mt-0.5 text-xs text-gray-500">Negative stock-taking variance</div>
                  </div>
                  <div className="rounded-lg bg-rose-50 p-2"><AlertTriangle className="h-5 w-5 text-rose-600" /></div>
                </div>
                <ResponsiveContainer width="100%" height={110}>
                  <BarChart data={payload.lostStockThisWeek || []}>
                    <XAxis dataKey="day" stroke="#6b7280" fontSize={11} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Bar dataKey="units" fill="#ef4444" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <SkeletonCard rows={4} />
              <SkeletonCard rows={4} />
              <SkeletonCard rows={4} />
            </div>
          )}

          {sectionReady.middle ? (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <div className="rounded-xl border border-gray-200 bg-white p-5 lg:col-span-2">
                <SectionTitle title="Customer Completion Trend" sub="Monthly completed vs active customer purchases" />
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={payload.completionTrend || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="month" stroke="#6b7280" />
                    <YAxis stroke="#6b7280" />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Legend />
                    <Bar dataKey="completed" fill="#10b981" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="partial" fill="#6366f1" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-5">
                <SectionTitle title="Inventory Health" sub="Available, reserved, and shortage view" />
                <div className="space-y-4">
                  {(payload.inventoryHealth || []).map((row) => (
                    <div key={row.status}>
                      <div className="mb-1 flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-900">{row.status}</span>
                        <span className="text-sm text-gray-600">{row.count}</span>
                      </div>
                      <div className="mb-1 h-2 overflow-hidden rounded-full bg-gray-100">
                        <div className={`h-2 rounded-full ${row.color}`} style={{ width: `${Math.min(100, ((row.count || 0) / maxInventoryHealthCount) * 100)}%` }} />
                      </div>
                      <div className="text-xs text-gray-500"><PriceMask value={row.value || 0} /></div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <SkeletonCard className="lg:col-span-2" rows={5} />
              <SkeletonCard rows={5} />
            </div>
          )}

          {sectionReady.lower ? (
            <>
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="rounded-xl border border-gray-200 bg-white p-5">
                  <SectionTitle title="Branch Performance" sub="Live branch fulfillment against branch stock position" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.branchPerformance || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="branch" stroke="#6b7280" />
                      <YAxis yAxisId="left" stroke="#6b7280" />
                      <YAxis yAxisId="right" orientation="right" stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line yAxisId="left" type="monotone" dataKey="fulfillment" stroke="#10b981" strokeWidth={2} />
                      <Line yAxisId="right" type="monotone" dataKey="inventory" stroke="#6366f1" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-xl border border-gray-200 bg-white p-5">
                  <SectionTitle title="Customer Completion Pressure" sub="Stock pressure expected from near-completion customers" />
                  <div className="space-y-3">
                    {(payload.forecastAlerts || []).map((row) => (
                      <div key={row.level} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.level}</div>
                          <div className="text-sm text-gray-600">{row.customers} customers</div>
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-3 text-sm text-gray-600">
                          <div>ETA: <span className="font-medium text-gray-900">{row.estimatedCompletion}</span></div>
                          <div>Required stock: <span className="font-medium text-gray-900">{row.requiredStock}</span></div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="rounded-xl border border-gray-200 bg-white p-5">
                  <SectionTitle title="Inventory Movement Snapshot" sub="Recent inbound, outbound, and adjustment movement" />
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={(payload.inventoryMovement || []).map((row, index) => ({ ...row, label: row.week || row.date || `P${index + 1}` }))}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="label" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Bar dataKey="inbound" fill="#10b981" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="outbound" fill="#6366f1" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="adjustments" fill="#f59e0b" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-xl border border-gray-200 bg-white p-5">
                  <SectionTitle title="Top Stock Risks" sub="Products most exposed to near-term stock pressure" />
                  <div className="space-y-3">
                    {(payload.stockShortage || []).map((row, index) => (
                      <div key={`${row.item || 'item'}-${index}`} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.item || 'Product'}</div>
                          <div className="text-sm font-semibold text-rose-600">{row.riskScore || 0}</div>
                        </div>
                        <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-gray-600">
                          <div>Stock: <span className="font-medium text-gray-900">{row.currentStock || 0}</span></div>
                          <div>Demand: <span className="font-medium text-gray-900">{row.forecast || 0}</span></div>
                          <div>Days: <span className="font-medium text-gray-900">{row.daysUntilStockout || 0}</span></div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <SkeletonCard rows={5} />
              <SkeletonCard rows={5} />
            </div>
          )}

          {sectionReady.activity ? (
            <div className="rounded-xl border border-gray-200 bg-white p-5">
              <SectionTitle title="Recent Activity" sub="Latest delivery, request, stock-taking, and purchasing events" />
              <div className="space-y-3">
                {(payload.recentActivities || []).map((row) => (
                  <div key={row.id} className="flex items-start justify-between rounded-lg border border-gray-200 bg-gray-50 p-4">
                    <div className="flex items-start gap-3">
                      <div className="rounded-lg bg-white p-2">
                        {row.type === 'delivery' ? (
                          <CheckCircle className="h-4 w-4 text-green-600" />
                        ) : row.type === 'request' ? (
                          <Clock className="h-4 w-4 text-blue-600" />
                        ) : row.type === 'purchase' ? (
                          <Package className="h-4 w-4 text-indigo-600" />
                        ) : (
                          <AlertTriangle className="h-4 w-4 text-orange-600" />
                        )}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-gray-900">{row.message}</div>
                        <div className="mt-1 text-xs text-gray-500">{row.type}</div>
                      </div>
                    </div>
                    <div className="text-xs text-gray-500">{row.time ? row.time.replace('T', ' ').slice(0, 16) : '-'}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <SkeletonCard rows={6} />
          )}
        </>
      ) : null}
    </div>
  );
}
