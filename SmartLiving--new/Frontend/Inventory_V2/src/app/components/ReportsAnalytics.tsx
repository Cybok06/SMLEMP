import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Building2,
  CheckCircle,
  ClipboardCheck,
  Clock,
  Download,
  Package,
  ShoppingCart,
  Star,
  Target,
  TrendingUp,
  Truck,
  UserCheck,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { toast } from 'sonner';
import { useAccessSafe } from '../context/RoleAccessContext';
import { AccessBanner, PriceMask } from './PriceGuard';

type ProductRow = {
  id: string;
  name: string;
  sku: string;
  category: string;
  totalStock: number;
  unitCost: number;
  status: string;
};

type ReportsPayload = {
  ok: boolean;
  inventory?: {
    metrics: {
      totalProducts: number;
      totalStock: number;
      totalValue: number;
      criticalCount: number;
    };
    stockLevels: ProductRow[];
    movementTrend: Array<{ date: string; inbound: number; outbound: number; adjustments: number }>;
    valueByCategory: Array<{ category: string; value: number; percentage: number }>;
    valueByBranch: Array<{ branch: string; manager: string; products: number; stockUnits: number; inventoryValue: number }>;
  };
  warehouse?: {
    transfers: Array<{ date: string; from: string; to: string; items: number; status: string }>;
    utilization: Array<{ location: string; branch: string; capacity: number; current: number; utilization: number }>;
    requestTrends: Array<{ month: string; requests: number; approved: number; rejected: number }>;
  };
  forecast?: {
    demandForecast: Array<{ month: string; actual: number; forecast: number; variance: number }>;
    completionForecast: Array<{ week: string; expected: number; optimistic: number; pessimistic: number }>;
    stockoutRisk: Array<{ item: string; currentStock: number; forecast: number; riskScore: number; daysUntilStockout: number }>;
  };
  fulfillment?: {
    metrics: Array<{ date: string; orders: number; delivered: number; pending: number; cancelled: number }>;
    statusBreakdown: Array<{ status: string; count: number }>;
    routes: Array<{ route: string; deliveries: number; accurate: number; discrepancies: number; onTimePct: number }>;
  };
  audit?: {
    metrics?: { totalAudits: number; totalLosses: number; totalLossValue: number; openInvestigations: number };
    analytics?: {
      varianceTrend?: Array<{ month: string; variance: number }>;
      reasonBreakdown?: Array<{ reason: string; count: number }>;
    };
  };
  procurement?: {
    trend: Array<{ month: string; ordered: number; received: number; accuracy: number; discrepancies: number }>;
    detail: Array<{ po: string; supplier: string; date: string; ordered: number; received: number; variance: number; accuracyPct: number; status: string; leadDays: number }>;
    varianceByCategory: Array<{ category: string; ordered: number; received: number; variance: number; accuracyPct: number }>;
  };
  deliveryAccuracy?: {
    trend: Array<{ date: string; total: number; accurate: number; discrepancies: number; onTime: number; delayed: number; accuracyPct: number; onTimePct: number }>;
    breakdown: Array<{ type: string; count: number; pct: number }>;
    routes: Array<{ route: string; deliveries: number; accurate: number; discrepancies: number; onTimePct: number }>;
  };
  supplierPerformance?: {
    scorecard: Array<{ supplier: string; orders: number; fulfilledOnTime: number; avgLeadDays: number; accuracyPct: number; discrepancyRate: number; score: number; tier: string }>;
    suppliersCount: number;
    costUpdates: number;
  };
  staffHandling?: {
    rows: Array<{ staffName: string; role: string; branch: string; handled: number; delivered: number; pending: number; accuracyPct: number }>;
  };
  error?: string;
};

const TOOLTIP_STYLE = { borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 };

function getInventoryVolumeColor(index: number, total: number) {
  if (total <= 1) return '#16a34a';
  const ratio = index / Math.max(1, total - 1);
  if (ratio <= 0.33) return '#16a34a';
  if (ratio <= 0.66) return '#eab308';
  return '#dc2626';
}

function KpiCard({
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
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-start justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</span>
        <div className={`rounded-lg p-2 ${tone}`}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
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

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function ReportsAnalytics() {
  const [activeTab, setActiveTab] = useState('inventory');
  const [loading, setLoading] = useState(true);
  const [payload, setPayload] = useState<ReportsPayload | null>(null);
  const { canViewPricing } = useAccessSafe();

  const tabs = [
    { id: 'inventory', label: 'Inventory', icon: Package },
    { id: 'warehouse', label: 'Warehouse', icon: Building2 },
    { id: 'forecast', label: 'Forecast', icon: TrendingUp },
    { id: 'fulfillment', label: 'Fulfillment', icon: Truck },
    { id: 'audit', label: 'Audit', icon: ClipboardCheck },
    { id: 'procurement', label: 'Purchase Accuracy', icon: ShoppingCart },
    { id: 'delivery-acc', label: 'Delivery Accuracy', icon: Target },
    { id: 'supplier-perf', label: 'Supplier Perf.', icon: Star },
    { id: 'staff-perf', label: 'Staff Handling', icon: UserCheck },
  ] as const;

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/inventory/reports/bootstrap', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const nextPayload = await parseJsonResponse<ReportsPayload>(response);
      if (!response.ok || !nextPayload.ok) {
        throw new Error(nextPayload.error || 'Unable to load reports data.');
      }
      setPayload(nextPayload);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load reports data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const inventoryMetrics = payload?.inventory?.metrics;
  const branchInventoryRows = payload?.inventory?.valueByBranch || [];
  const forecastStockRisk = useMemo(
    () => [...(payload?.forecast?.stockoutRisk || [])].sort((a, b) => b.riskScore - a.riskScore).slice(0, 10),
    [payload],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Reports & Analytics</h1>
          <p className="mt-1 text-gray-600">Live inventory reporting across stock, warehouse movement, fulfillment, audit, procurement, suppliers, and staff handling.</p>
        </div>
        <button
          type="button"
          onClick={() => toast.info('Export for the reports page is not wired yet.')}
          className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
        >
          <Download className="h-4 w-4 text-gray-600" />
          <span className="text-sm font-medium text-gray-700">Export Report</span>
        </button>
      </div>

      {!canViewPricing ? <AccessBanner title="Pricing hidden" description="Financial amounts are masked for your current access level." /> : null}

      <div className="rounded-lg border border-gray-200 bg-white">
        <div className="overflow-x-auto border-b border-gray-200">
          <div className="flex">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-6 py-4 transition-colors ${
                    activeTab === tab.id ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span className="text-sm font-medium">{tab.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="p-6">
          {loading || !payload ? (
            <div className="rounded-lg border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              Loading reports data...
            </div>
          ) : activeTab === 'inventory' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                <KpiCard label="Products" value={inventoryMetrics?.totalProducts || 0} icon={Package} tone="bg-indigo-50 text-indigo-600" />
                <KpiCard label="Stock Units" value={inventoryMetrics?.totalStock || 0} icon={BarChart3} tone="bg-blue-50 text-blue-600" />
                <KpiCard label="Inventory Value" value={<PriceMask value={inventoryMetrics?.totalValue || 0} />} icon={TrendingUp} tone="bg-emerald-50 text-emerald-600" />
                <KpiCard label="Critical SKUs" value={inventoryMetrics?.criticalCount || 0} icon={AlertTriangle} tone="bg-rose-50 text-rose-600" />
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Inventory Movement Trend" sub="Inbound, outbound, and adjustment movement from live product entries" />
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={payload.inventory?.movementTrend || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Bar dataKey="inbound" fill="#10b981" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="outbound" fill="#6366f1" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="adjustments" fill="#f59e0b" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Value by Category" />
                  <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                      <Pie data={payload.inventory?.valueByCategory || []} dataKey="value" nameKey="category" cx="50%" cy="50%" outerRadius={88} label>
                        {(payload.inventory?.valueByCategory || []).map((row, index) => (
                          <Cell key={`${row.category}-${index}`} fill={['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981'][index % 5]} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Branch Inventory Value" sub="Live inventory stock value by branch with assigned manager coverage." />
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-50">
                        <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                          <th className="px-4 py-3">Branch</th>
                          <th className="px-4 py-3">Manager</th>
                          <th className="px-4 py-3">Products</th>
                          <th className="px-4 py-3">Units</th>
                          <th className="px-4 py-3">Inventory Value</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100 bg-white">
                        {branchInventoryRows.length === 0 ? (
                          <tr>
                            <td colSpan={5} className="px-4 py-6 text-sm text-gray-500">No branch inventory values available yet.</td>
                          </tr>
                        ) : (
                          branchInventoryRows.map((row) => (
                            <tr key={`${row.branch}-${row.manager}`}>
                              <td className="px-4 py-4 font-medium text-gray-900">{row.branch || 'Unassigned'}</td>
                              <td className="px-4 py-4 text-sm text-gray-700">{row.manager || 'Unassigned'}</td>
                              <td className="px-4 py-4 text-sm text-gray-700">{row.products}</td>
                              <td className="px-4 py-4 text-sm text-gray-700">{row.stockUnits.toLocaleString()}</td>
                              <td className="px-4 py-4 text-sm text-gray-900"><PriceMask value={row.inventoryValue || 0} /></td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Inventory Volume by Branch" sub="Highest stock value ranks green, then yellow, then red down the ladder." />
                  <ResponsiveContainer width="100%" height={320}>
                    <BarChart data={branchInventoryRows} layout="vertical" margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis type="number" stroke="#6b7280" />
                      <YAxis type="category" dataKey="branch" stroke="#6b7280" width={90} />
                      <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value: number) => [canViewPricing ? `GHS ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : 'Hidden', 'Inventory Value']} />
                      <Bar dataKey="inventoryValue" radius={[0, 6, 6, 0]}>
                        {branchInventoryRows.map((row, index) => (
                          <Cell key={`${row.branch}-${index}`} fill={getInventoryVolumeColor(index, branchInventoryRows.length)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Product</th>
                      <th className="px-4 py-3">Category</th>
                      <th className="px-4 py-3">Stock</th>
                      <th className="px-4 py-3">Unit Cost</th>
                      <th className="px-4 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.inventory?.stockLevels || []).map((row) => (
                      <tr key={row.id}>
                        <td className="px-4 py-4">
                          <div className="font-medium text-gray-900">{row.name}</div>
                          <div className="text-xs text-gray-500">{row.sku || '-'}</div>
                        </td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.category || '-'}</td>
                        <td className="px-4 py-4 text-sm text-gray-900">{row.totalStock}</td>
                        <td className="px-4 py-4 text-sm text-gray-900"><PriceMask value={row.unitCost || 0} /></td>
                        <td className="px-4 py-4 text-sm font-medium text-gray-700">{row.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'warehouse' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Location Utilization" />
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={payload.warehouse?.utilization || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="location" stroke="#6b7280" fontSize={11} />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Bar dataKey="utilization" fill="#6366f1" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Branch Request Trend" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.warehouse?.requestTrends || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="month" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line type="monotone" dataKey="requests" stroke="#6366f1" strokeWidth={2} />
                      <Line type="monotone" dataKey="approved" stroke="#10b981" strokeWidth={2} />
                      <Line type="monotone" dataKey="rejected" stroke="#ef4444" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">From</th>
                      <th className="px-4 py-3">To</th>
                      <th className="px-4 py-3">Items</th>
                      <th className="px-4 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.warehouse?.transfers || []).map((row, index) => (
                      <tr key={`${row.date}-${index}`}>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.date || '-'}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.from || '-'}</td>
                        <td className="px-4 py-4 text-sm text-gray-900">{row.to || '-'}</td>
                        <td className="px-4 py-4 text-sm text-gray-900">{row.items}</td>
                        <td className="px-4 py-4 text-sm font-medium text-gray-700">{row.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'forecast' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Demand Forecast" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.forecast?.demandForecast || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="month" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line type="monotone" dataKey="actual" stroke="#10b981" strokeWidth={2} />
                      <Line type="monotone" dataKey="forecast" stroke="#6366f1" strokeWidth={2} strokeDasharray="5 5" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Completion Forecast" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.forecast?.completionForecast || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="week" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line type="monotone" dataKey="optimistic" stroke="#10b981" strokeWidth={2} />
                      <Line type="monotone" dataKey="expected" stroke="#6366f1" strokeWidth={2} />
                      <Line type="monotone" dataKey="pessimistic" stroke="#ef4444" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Item</th>
                      <th className="px-4 py-3">Current Stock</th>
                      <th className="px-4 py-3">Forecast Demand</th>
                      <th className="px-4 py-3">Risk</th>
                      <th className="px-4 py-3">Days to Stockout</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {forecastStockRisk.map((row) => (
                      <tr key={row.item}>
                        <td className="px-4 py-4 font-medium text-gray-900">{row.item}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.currentStock}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.forecast}</td>
                        <td className="px-4 py-4 text-sm font-semibold text-gray-900">{row.riskScore}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.daysUntilStockout}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'fulfillment' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Daily Fulfillment Trend" />
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={payload.fulfillment?.metrics || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Bar dataKey="orders" fill="#6366f1" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="delivered" fill="#10b981" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="pending" fill="#f59e0b" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Status Breakdown" />
                  <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                      <Pie data={payload.fulfillment?.statusBreakdown || []} dataKey="count" nameKey="status" cx="50%" cy="50%" outerRadius={88} label>
                        {(payload.fulfillment?.statusBreakdown || []).map((row, index) => (
                          <Cell key={`${row.status}-${index}`} fill={['#10b981', '#f59e0b', '#6366f1', '#ef4444'][index % 4]} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Route</th>
                      <th className="px-4 py-3">Deliveries</th>
                      <th className="px-4 py-3">Delivered</th>
                      <th className="px-4 py-3">Discrepancies</th>
                      <th className="px-4 py-3">On-Time %</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.fulfillment?.routes || []).map((row) => (
                      <tr key={row.route}>
                        <td className="px-4 py-4 font-medium text-gray-900">{row.route}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.deliveries}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.accurate}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.discrepancies}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.onTimePct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'audit' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                <KpiCard label="Audits" value={payload.audit?.metrics?.totalAudits || 0} icon={ClipboardCheck} tone="bg-indigo-50 text-indigo-600" />
                <KpiCard label="Losses" value={payload.audit?.metrics?.totalLosses || 0} icon={AlertTriangle} tone="bg-rose-50 text-rose-600" />
                <KpiCard label="Loss Value" value={<PriceMask value={payload.audit?.metrics?.totalLossValue || 0} />} icon={TrendingUp} tone="bg-amber-50 text-amber-600" />
                <KpiCard label="Open Cases" value={payload.audit?.metrics?.openInvestigations || 0} icon={Activity} tone="bg-blue-50 text-blue-600" />
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Variance Trend" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.audit?.analytics?.varianceTrend || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="month" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Line type="monotone" dataKey="variance" stroke="#ef4444" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Top Discrepancy Reasons" />
                  <div className="space-y-3">
                    {(payload.audit?.analytics?.reasonBreakdown || []).map((row) => (
                      <div key={row.reason} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.reason}</div>
                          <div className="text-sm text-gray-600">{row.count}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : activeTab === 'procurement' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Monthly PO Accuracy" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.procurement?.trend || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="month" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line type="monotone" dataKey="accuracy" stroke="#10b981" strokeWidth={2} />
                      <Line type="monotone" dataKey="discrepancies" stroke="#ef4444" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Accuracy by Category" />
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={payload.procurement?.varianceByCategory || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="category" stroke="#6b7280" fontSize={11} />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Bar dataKey="accuracyPct" fill="#6366f1" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">PO</th>
                      <th className="px-4 py-3">Supplier</th>
                      <th className="px-4 py-3">Ordered</th>
                      <th className="px-4 py-3">Received</th>
                      <th className="px-4 py-3">Variance</th>
                      <th className="px-4 py-3">Accuracy</th>
                      <th className="px-4 py-3">Lead Days</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.procurement?.detail || []).map((row) => (
                      <tr key={row.po}>
                        <td className="px-4 py-4">
                          <div className="font-medium text-gray-900">{row.po}</div>
                          <div className="text-xs text-gray-500">{row.date || '-'}</div>
                        </td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.supplier}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.ordered}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.received}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.variance}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.accuracyPct}%</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.leadDays}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'delivery-acc' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Delivery Accuracy Trend" />
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={payload.deliveryAccuracy?.trend || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" stroke="#6b7280" />
                      <YAxis stroke="#6b7280" />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Legend />
                      <Line type="monotone" dataKey="accuracyPct" stroke="#10b981" strokeWidth={2} />
                      <Line type="monotone" dataKey="onTimePct" stroke="#6366f1" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className="rounded-lg border border-gray-200 p-5">
                  <SectionTitle title="Delivery Breakdown" />
                  <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                      <Pie data={payload.deliveryAccuracy?.breakdown || []} dataKey="count" nameKey="type" cx="50%" cy="50%" outerRadius={88} label>
                        {(payload.deliveryAccuracy?.breakdown || []).map((row, index) => (
                          <Cell key={`${row.type}-${index}`} fill={['#ef4444', '#f59e0b', '#6366f1', '#10b981'][index % 4]} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Route</th>
                      <th className="px-4 py-3">Deliveries</th>
                      <th className="px-4 py-3">Accurate</th>
                      <th className="px-4 py-3">Discrepancies</th>
                      <th className="px-4 py-3">On-Time %</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.deliveryAccuracy?.routes || []).map((row) => (
                      <tr key={row.route}>
                        <td className="px-4 py-4 font-medium text-gray-900">{row.route}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.deliveries}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.accurate}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.discrepancies}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.onTimePct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'supplier-perf' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                <KpiCard label="Suppliers" value={payload.supplierPerformance?.suppliersCount || 0} icon={Star} tone="bg-indigo-50 text-indigo-600" />
                <KpiCard label="Cost Updates" value={payload.supplierPerformance?.costUpdates || 0} icon={Activity} tone="bg-blue-50 text-blue-600" />
                <KpiCard label="Tracked Scorecards" value={(payload.supplierPerformance?.scorecard || []).length} icon={CheckCircle} tone="bg-emerald-50 text-emerald-600" />
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Supplier</th>
                      <th className="px-4 py-3">Orders</th>
                      <th className="px-4 py-3">On Time</th>
                      <th className="px-4 py-3">Lead Days</th>
                      <th className="px-4 py-3">Accuracy</th>
                      <th className="px-4 py-3">Score</th>
                      <th className="px-4 py-3">Tier</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.supplierPerformance?.scorecard || []).map((row) => (
                      <tr key={row.supplier}>
                        <td className="px-4 py-4 font-medium text-gray-900">{row.supplier}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.orders}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.fulfilledOnTime}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.avgLeadDays}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.accuracyPct}%</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.score}</td>
                        <td className="px-4 py-4 text-sm font-medium text-gray-700">{row.tier}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : activeTab === 'staff-perf' ? (
            <div className="space-y-6">
              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Staff</th>
                      <th className="px-4 py-3">Role</th>
                      <th className="px-4 py-3">Branch</th>
                      <th className="px-4 py-3">Handled</th>
                      <th className="px-4 py-3">Delivered</th>
                      <th className="px-4 py-3">Pending</th>
                      <th className="px-4 py-3">Accuracy</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(payload.staffHandling?.rows || []).map((row) => (
                      <tr key={`${row.staffName}-${row.branch}`}>
                        <td className="px-4 py-4 font-medium text-gray-900">{row.staffName}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.role}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.branch || '-'}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.handled}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.delivered}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.pending}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.accuracyPct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
