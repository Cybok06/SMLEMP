import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  CheckCircle,
  Clipboard,
  ClipboardCheck,
  Clock,
  Download,
  Eye,
  FileText,
  Package,
  Plus,
  RefreshCw,
  Search,
  Shield,
  TrendingDown,
  User,
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
import { PriceMask } from './PriceGuard';
import { StockTaking } from './StockTaking';
import { StockDeduction } from './StockDeduction';

interface AuditAccountabilityProps {
  defaultTab?: string | null;
  onTabChange?: () => void;
}

type AuditMetrics = {
  totalAudits: number;
  activeAudits: number;
  totalLosses: number;
  totalLossValue: number;
  shrinkagePercent: number;
  highRiskProducts: number;
  highRiskBranches: number;
  staffRiskAlerts: number;
  openInvestigations: number;
  resolvedThisMonth: number;
};

type AuditRow = {
  id: string;
  type: string;
  location: string;
  branch: string;
  scheduledDate: string;
  completedDate: string;
  auditor: string;
  status: string;
  itemsToAudit: number;
  itemsCompleted: number;
  discrepanciesFound: number;
  progress: number;
  submittedDate: string;
};

type InvestigationRow = {
  id: string;
  discrepancyId: string;
  auditId: string;
  item: string;
  sku: string;
  category: string;
  location: string;
  branch: string;
  systemStock: number;
  physicalStock: number;
  variance: number;
  varianceValue: number;
  reportedDate: string;
  status: string;
  priority: string;
  assignedTo: string;
  lastHandler: string;
  reason: string;
  notes: string;
  timeline: Array<{ date: string; action: string; user: string; type: string }>;
};

type ResolutionRow = {
  id: string;
  investigationId: string;
  item: string;
  branch: string;
  variance: number;
  resolvedDate: string;
  resolvedBy: string;
  action: string;
  actionDetails: string;
  status: string;
  staffAction: string;
  cost: number;
};

type AlertRow = {
  id: string;
  type: string;
  severity: string;
  message: string;
  details: string;
  createdDate: string;
  status: string;
};

type RiskProductRow = {
  item: string;
  sku: string;
  category: string;
  incidents: number;
  lossValue: number;
  riskScore: number;
};

type RiskBranchRow = {
  branch: string;
  incidents: number;
  lossValue: number;
  shrinkage: number;
};

type StaffRiskRow = {
  id: string;
  staffName: string;
  role: string;
  location: string;
  incidents: number;
  lastIncident: string;
  riskLevel: string;
  details: string;
};

type AnalyticsPayload = {
  lossTrends: Array<{ month: string; losses: number; value: number }>;
  varianceTrend: Array<{ month: string; variance: number }>;
  shrinkageByCategory: Array<{ category: string; shrinkage: number; value: number }>;
  highRiskProducts: RiskProductRow[];
  highRiskBranches: RiskBranchRow[];
  staffRiskAlerts: StaffRiskRow[];
  reasonBreakdown: Array<{ reason: string; count: number }>;
};

type AuditBootstrapResponse = {
  ok: boolean;
  metrics?: Partial<AuditMetrics>;
  audits?: AuditRow[];
  investigations?: InvestigationRow[];
  resolutions?: ResolutionRow[];
  alerts?: AlertRow[];
  analytics?: Partial<AnalyticsPayload>;
  error?: string;
};

const DEFAULT_METRICS: AuditMetrics = {
  totalAudits: 0,
  activeAudits: 0,
  totalLosses: 0,
  totalLossValue: 0,
  shrinkagePercent: 0,
  highRiskProducts: 0,
  highRiskBranches: 0,
  staffRiskAlerts: 0,
  openInvestigations: 0,
  resolvedThisMonth: 0,
};

const DEFAULT_ANALYTICS: AnalyticsPayload = {
  lossTrends: [],
  varianceTrend: [],
  shrinkageByCategory: [],
  highRiskProducts: [],
  highRiskBranches: [],
  staffRiskAlerts: [],
  reasonBreakdown: [],
};

function statusTone(status: string): string {
  const key = String(status || '').toLowerCase();
  if (['approved', 'completed', 'resolved'].includes(key)) return 'text-green-600 bg-green-50 border-green-200';
  if (['submitted', 'reviewed', 'escalated'].includes(key)) return 'text-amber-600 bg-amber-50 border-amber-200';
  if (['counting', 'draft', 'investigating', 'active'].includes(key)) return 'text-blue-600 bg-blue-50 border-blue-200';
  return 'text-gray-600 bg-gray-50 border-gray-200';
}

function priorityTone(priority: string): string {
  const key = String(priority || '').toLowerCase();
  if (key === 'high' || key === 'critical') return 'text-red-600 bg-red-50 border-red-200';
  if (key === 'medium' || key === 'warning') return 'text-amber-600 bg-amber-50 border-amber-200';
  return 'text-blue-600 bg-blue-50 border-blue-200';
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function AuditAccountability({ defaultTab, onTabChange }: AuditAccountabilityProps = {}) {
  const [activeTab, setActiveTab] = useState(defaultTab || 'dashboard');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState<AuditMetrics>(DEFAULT_METRICS);
  const [audits, setAudits] = useState<AuditRow[]>([]);
  const [investigations, setInvestigations] = useState<InvestigationRow[]>([]);
  const [resolutions, setResolutions] = useState<ResolutionRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsPayload>(DEFAULT_ANALYTICS);

  const loadData = async () => {
    if (activeTab === 'stock-taking' || activeTab === 'stock-deduction') {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const response = await fetch('/api/inventory/audit/bootstrap', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await parseJsonResponse<AuditBootstrapResponse>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to load audit data.');
      }
      setMetrics({ ...DEFAULT_METRICS, ...(payload.metrics || {}) });
      setAudits(Array.isArray(payload.audits) ? payload.audits : []);
      setInvestigations(Array.isArray(payload.investigations) ? payload.investigations : []);
      setResolutions(Array.isArray(payload.resolutions) ? payload.resolutions : []);
      setAlerts(Array.isArray(payload.alerts) ? payload.alerts : []);
      setAnalytics({ ...DEFAULT_ANALYTICS, ...(payload.analytics || {}) });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load audit data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [activeTab]);

  useEffect(() => {
    if (defaultTab) {
      setActiveTab(defaultTab);
      onTabChange?.();
    }
  }, [defaultTab, onTabChange]);

  const tabs = [
    { id: 'dashboard', label: 'Dashboard', icon: BarChart3 },
    { id: 'audits', label: 'Audits', icon: ClipboardCheck },
    { id: 'stock-taking', label: 'Stock Taking', icon: Clipboard },
    { id: 'stock-deduction', label: 'Stock Deduction', icon: TrendingDown },
    { id: 'investigations', label: 'Investigations', icon: FileText },
    { id: 'resolutions', label: 'Resolutions', icon: CheckCircle },
    { id: 'analytics', label: 'Analytics', icon: Activity },
    { id: 'alerts', label: 'Alerts', icon: Bell },
  ] as const;

  const search = searchQuery.trim().toLowerCase();
  const filteredAudits = useMemo(
    () =>
      audits.filter((row) =>
        !search ||
        [row.id, row.location, row.branch, row.auditor, row.status].some((value) =>
          String(value || '').toLowerCase().includes(search),
        ),
      ),
    [audits, search],
  );
  const filteredInvestigations = useMemo(
    () =>
      investigations.filter((row) =>
        !search ||
        [row.id, row.item, row.sku, row.branch, row.location, row.assignedTo, row.reason, row.status].some((value) =>
          String(value || '').toLowerCase().includes(search),
        ),
      ),
    [investigations, search],
  );
  const filteredResolutions = useMemo(
    () =>
      resolutions.filter((row) =>
        !search ||
        [row.id, row.item, row.branch, row.resolvedBy, row.actionDetails].some((value) =>
          String(value || '').toLowerCase().includes(search),
        ),
      ),
    [resolutions, search],
  );
  const filteredAlerts = useMemo(
    () =>
      alerts.filter((row) =>
        !search ||
        [row.id, row.message, row.details, row.severity, row.status].some((value) =>
          String(value || '').toLowerCase().includes(search),
        ),
      ),
    [alerts, search],
  );

  const summaryCards = [
    { label: 'Total Audits', value: metrics.totalAudits, icon: ClipboardCheck, tone: 'bg-slate-50 text-slate-700' },
    { label: 'Active Audits', value: metrics.activeAudits, icon: Clock, tone: 'bg-blue-50 text-blue-600' },
    { label: 'Open Investigations', value: metrics.openInvestigations, icon: FileText, tone: 'bg-amber-50 text-amber-600' },
    { label: 'Resolved This Month', value: metrics.resolvedThisMonth, icon: CheckCircle, tone: 'bg-green-50 text-green-600' },
    { label: 'Loss Value', value: <PriceMask value={metrics.totalLossValue} />, icon: TrendingDown, tone: 'bg-rose-50 text-rose-600' },
  ];

  const renderEmpty = (title: string, text: string) => (
    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center">
      <Package className="mx-auto mb-3 h-8 w-8 text-gray-300" />
      <p className="text-sm font-medium text-gray-700">{title}</p>
      <p className="mt-1 text-sm text-gray-500">{text}</p>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Audit & Accountability</h1>
          <p className="mt-1 text-gray-600">Live audit control for stock-taking sessions, discrepancy investigations, resolutions, and branch risk signals.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => toast.info('Audit export is not wired yet.')}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <Download className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Export Report</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('stock-taking')}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-white transition-colors hover:bg-indigo-700"
          >
            <Plus className="h-4 w-4" />
            <span className="text-sm font-medium">New Audit</span>
          </button>
        </div>
      </div>

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

        {!['stock-taking', 'stock-deduction'].includes(activeTab) && (
          <div className="border-b border-gray-200 p-4">
            <div className="relative max-w-md">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search current audit tab..."
                className="w-full rounded-lg border border-gray-200 py-2 pl-10 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>
        )}

        <div className="p-6">
          {activeTab === 'stock-taking' ? (
            <StockTaking />
          ) : activeTab === 'stock-deduction' ? (
            <StockDeduction />
          ) : loading ? (
            <div className="rounded-lg border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              Loading audit data...
            </div>
          ) : activeTab === 'dashboard' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
                {summaryCards.map((card) => {
                  const Icon = card.icon;
                  return (
                    <div key={card.label} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <div className={`mb-3 inline-flex rounded-lg p-2 ${card.tone}`}>
                        <Icon className="h-4 w-4" />
                      </div>
                      <div className="text-xl font-semibold text-gray-900">{card.value}</div>
                      <div className="mt-1 text-xs text-gray-600">{card.label}</div>
                    </div>
                  );
                })}
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-4 flex items-center gap-2">
                    <TrendingDown className="h-5 w-5 text-rose-500" />
                    <h3 className="font-semibold text-gray-900">Loss Trend</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={analytics.lossTrends}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="month" />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Line type="monotone" dataKey="losses" stroke="#ef4444" strokeWidth={2} />
                        <Line type="monotone" dataKey="value" stroke="#4f46e5" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-4 flex items-center gap-2">
                    <Shield className="h-5 w-5 text-amber-500" />
                    <h3 className="font-semibold text-gray-900">Category Shrinkage</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={analytics.shrinkageByCategory} dataKey="value" nameKey="category" cx="50%" cy="50%" outerRadius={90} label>
                          {analytics.shrinkageByCategory.map((entry, index) => (
                            <Cell key={`${entry.category}-${index}`} fill={['#ef4444', '#f59e0b', '#eab308', '#10b981', '#3b82f6'][index % 5]} />
                          ))}
                        </Pie>
                        <Tooltip />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              <div className="grid gap-6 lg:grid-cols-3">
                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-rose-500" />
                    <h3 className="font-semibold text-gray-900">High-Risk Products</h3>
                  </div>
                  <div className="space-y-3">
                    {analytics.highRiskProducts.slice(0, 5).map((row) => (
                      <div key={row.item} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.item}</div>
                          <span className={`rounded border px-2 py-0.5 text-xs font-medium ${priorityTone(row.riskScore >= 70 ? 'high' : row.riskScore >= 45 ? 'medium' : 'low')}`}>
                            Risk {row.riskScore}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-gray-500">
                          {row.sku || '-'} | {row.incidents} incidents | <PriceMask value={row.lossValue} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <BarChart3 className="h-4 w-4 text-indigo-500" />
                    <h3 className="font-semibold text-gray-900">Branch Risk</h3>
                  </div>
                  <div className="space-y-3">
                    {analytics.highRiskBranches.slice(0, 5).map((row) => (
                      <div key={row.branch} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="font-medium text-gray-900">{row.branch || 'Unknown Branch'}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          {row.incidents} incidents | <PriceMask value={row.lossValue} /> | {row.shrinkage}% impact
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <User className="h-4 w-4 text-amber-500" />
                    <h3 className="font-semibold text-gray-900">Staff Risk Alerts</h3>
                  </div>
                  <div className="space-y-3">
                    {analytics.staffRiskAlerts.slice(0, 5).map((row) => (
                      <div key={row.id} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.staffName}</div>
                          <span className={`rounded border px-2 py-0.5 text-xs font-medium ${priorityTone(row.riskLevel)}`}>
                            {row.riskLevel}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-gray-500">
                          {row.location} | {row.incidents} incidents
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : activeTab === 'audits' ? (
            filteredAudits.length === 0 ? renderEmpty('No audits found', 'Stock-taking sessions will appear here as live audits.') : (
              <div className="space-y-4">
                {filteredAudits.map((audit) => (
                  <div key={audit.id} className="rounded-xl border border-gray-200 bg-gray-50 p-5">
                    <div className="mb-4 flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-3">
                          <h3 className="font-semibold text-gray-900">{audit.id}</h3>
                          <span className={`rounded border px-2 py-1 text-xs font-medium ${statusTone(audit.status)}`}>{audit.status}</span>
                        </div>
                        <div className="mt-1 text-sm text-gray-600">
                          {audit.branch} / {audit.location} | Auditor: {audit.auditor}
                        </div>
                      </div>
                      <div className="text-right text-xs text-gray-500">
                        <div>Count Date: {audit.scheduledDate || '-'}</div>
                        <div>Submitted: {audit.submittedDate || '-'}</div>
                      </div>
                    </div>

                    <div className="grid gap-4 md:grid-cols-4">
                      <div>
                        <div className="text-xs text-gray-500">Items</div>
                        <div className="text-sm font-semibold text-gray-900">{audit.itemsToAudit}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Counted</div>
                        <div className="text-sm font-semibold text-blue-600">{audit.itemsCompleted}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Discrepancies</div>
                        <div className="text-sm font-semibold text-rose-600">{audit.discrepanciesFound}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Progress</div>
                        <div className="text-sm font-semibold text-indigo-600">{audit.progress}%</div>
                      </div>
                    </div>

                    <div className="mt-4 h-2 overflow-hidden rounded-full bg-gray-200">
                      <div className="h-2 rounded-full bg-indigo-600" style={{ width: `${audit.progress}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : activeTab === 'investigations' ? (
            filteredInvestigations.length === 0 ? renderEmpty('No investigations found', 'Discrepancies requiring review will appear here.') : (
              <div className="space-y-4">
                {filteredInvestigations.map((row) => (
                  <div key={row.id} className="rounded-xl border border-gray-200 bg-gray-50 p-5">
                    <div className="mb-4 flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-3">
                          <h3 className="font-semibold text-gray-900">{row.id}</h3>
                          <span className={`rounded border px-2 py-1 text-xs font-medium ${statusTone(row.status)}`}>{row.status}</span>
                          <span className={`rounded border px-2 py-1 text-xs font-medium ${priorityTone(row.priority)}`}>{row.priority}</span>
                        </div>
                        <div className="mt-1 text-sm text-gray-700">{row.item} ({row.sku || '-'})</div>
                        <div className="mt-1 text-xs text-gray-500">{row.branch} / {row.location} | Audit {row.auditId}</div>
                      </div>
                      <div className="text-right text-xs text-gray-500">
                        <div>Assigned: {row.assignedTo}</div>
                        <div>Handler: {row.lastHandler}</div>
                      </div>
                    </div>

                    <div className="grid gap-4 rounded-lg border border-gray-200 bg-white p-4 md:grid-cols-5">
                      <div>
                        <div className="text-xs text-gray-500">System Stock</div>
                        <div className="text-sm font-semibold text-gray-900">{row.systemStock}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Physical Stock</div>
                        <div className="text-sm font-semibold text-blue-600">{row.physicalStock}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Variance</div>
                        <div className={`text-sm font-semibold ${row.variance < 0 ? 'text-rose-600' : 'text-green-600'}`}>{row.variance > 0 ? '+' : ''}{row.variance}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Value Impact</div>
                        <div className="text-sm font-semibold text-gray-900"><PriceMask value={Math.abs(row.varianceValue)} /></div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Reason</div>
                        <div className="text-sm font-medium text-gray-900">{row.reason}</div>
                      </div>
                    </div>

                    <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <div className="mb-2 flex items-center gap-2 font-medium text-blue-900">
                        <Eye className="h-4 w-4" />
                        Investigation Timeline
                      </div>
                      <div className="space-y-2 text-sm text-blue-900">
                        {row.timeline.map((step, index) => (
                          <div key={`${row.id}-${index}`} className="flex items-start justify-between gap-4">
                            <div>{step.action}</div>
                            <div className="text-right text-xs text-blue-700">
                              <div>{step.user}</div>
                              <div>{step.date || '-'}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : activeTab === 'resolutions' ? (
            filteredResolutions.length === 0 ? renderEmpty('No resolutions found', 'Approved discrepancy resolutions will appear here.') : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <th className="px-4 py-3">Resolution</th>
                      <th className="px-4 py-3">Item</th>
                      <th className="px-4 py-3">Branch</th>
                      <th className="px-4 py-3">Variance</th>
                      <th className="px-4 py-3">Cost</th>
                      <th className="px-4 py-3">Resolved By</th>
                      <th className="px-4 py-3">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {filteredResolutions.map((row) => (
                      <tr key={row.id} className="hover:bg-gray-50/60">
                        <td className="px-4 py-4">
                          <div className="font-medium text-gray-900">{row.id}</div>
                          <div className="text-xs text-gray-500">{row.resolvedDate || '-'}</div>
                        </td>
                        <td className="px-4 py-4 text-sm text-gray-900">{row.item}</td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.branch || '-'}</td>
                        <td className="px-4 py-4 text-sm font-semibold text-gray-900">{row.variance}</td>
                        <td className="px-4 py-4 text-sm text-gray-900"><PriceMask value={row.cost} /></td>
                        <td className="px-4 py-4 text-sm text-gray-700">{row.resolvedBy || '-'}</td>
                        <td className="px-4 py-4">
                          <div className="text-sm font-medium text-gray-900">{row.action}</div>
                          <div className="text-xs text-gray-500">{row.actionDetails}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : activeTab === 'analytics' ? (
            <div className="space-y-6">
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-4 flex items-center gap-2">
                    <BarChart3 className="h-5 w-5 text-indigo-500" />
                    <h3 className="font-semibold text-gray-900">Variance Trend</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={analytics.varianceTrend}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="month" />
                        <YAxis />
                        <Tooltip />
                        <Bar dataKey="variance" fill="#4f46e5" radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-4 flex items-center gap-2">
                    <ClipboardCheck className="h-5 w-5 text-amber-500" />
                    <h3 className="font-semibold text-gray-900">Discrepancy Reasons</h3>
                  </div>
                  <div className="space-y-3">
                    {analytics.reasonBreakdown.length === 0 ? (
                      <div className="text-sm text-gray-500">No discrepancy reasons recorded yet.</div>
                    ) : (
                      analytics.reasonBreakdown.map((row) => (
                        <div key={row.reason} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                          <div className="flex items-center justify-between">
                            <div className="font-medium text-gray-900">{row.reason}</div>
                            <div className="text-sm text-gray-600">{row.count}</div>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>

              <div className="grid gap-6 lg:grid-cols-3">
                <div className="rounded-xl border border-gray-200 p-4 lg:col-span-2">
                  <div className="mb-4 flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5 text-rose-500" />
                    <h3 className="font-semibold text-gray-900">High-Risk Products</h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-50">
                        <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                          <th className="px-4 py-3">Product</th>
                          <th className="px-4 py-3">Category</th>
                          <th className="px-4 py-3">Incidents</th>
                          <th className="px-4 py-3">Loss Value</th>
                          <th className="px-4 py-3">Risk</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100 bg-white">
                        {analytics.highRiskProducts.map((row) => (
                          <tr key={row.item}>
                            <td className="px-4 py-4">
                              <div className="font-medium text-gray-900">{row.item}</div>
                              <div className="text-xs text-gray-500">{row.sku || '-'}</div>
                            </td>
                            <td className="px-4 py-4 text-sm text-gray-700">{row.category}</td>
                            <td className="px-4 py-4 text-sm text-gray-700">{row.incidents}</td>
                            <td className="px-4 py-4 text-sm text-gray-900"><PriceMask value={row.lossValue} /></td>
                            <td className="px-4 py-4">
                              <span className={`rounded border px-2 py-1 text-xs font-medium ${priorityTone(row.riskScore >= 70 ? 'high' : row.riskScore >= 45 ? 'medium' : 'low')}`}>
                                {row.riskScore}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="rounded-xl border border-gray-200 p-4">
                  <div className="mb-4 flex items-center gap-2">
                    <User className="h-5 w-5 text-amber-500" />
                    <h3 className="font-semibold text-gray-900">Staff Alerts</h3>
                  </div>
                  <div className="space-y-3">
                    {analytics.staffRiskAlerts.map((row) => (
                      <div key={row.id} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-gray-900">{row.staffName}</div>
                          <span className={`rounded border px-2 py-0.5 text-xs font-medium ${priorityTone(row.riskLevel)}`}>{row.riskLevel}</span>
                        </div>
                        <div className="mt-1 text-xs text-gray-500">{row.location} | {row.incidents} incidents</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : activeTab === 'alerts' ? (
            filteredAlerts.length === 0 ? renderEmpty('No alerts found', 'High-variance and unresolved discrepancy alerts will appear here.') : (
              <div className="space-y-4">
                {filteredAlerts.map((row) => (
                  <div key={row.id} className="rounded-xl border border-gray-200 bg-gray-50 p-5">
                    <div className="flex items-start gap-4">
                      <div className={`rounded-lg border p-3 ${priorityTone(row.severity)}`}>
                        <Bell className="h-5 w-5" />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-3">
                          <h3 className="font-semibold text-gray-900">{row.message}</h3>
                          <span className={`rounded border px-2 py-1 text-xs font-medium ${priorityTone(row.severity)}`}>{row.severity}</span>
                        </div>
                        <div className="mt-2 text-sm text-gray-600">{row.details}</div>
                        <div className="mt-2 text-xs text-gray-500">{row.createdDate || '-'} | {row.status}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : null}
        </div>
      </div>
    </div>
  );
}
