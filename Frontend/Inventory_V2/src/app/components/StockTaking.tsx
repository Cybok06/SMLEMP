import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Building2,
  Calendar,
  CheckCircle,
  ChevronRight,
  ClipboardCheck,
  Clock,
  Download,
  Eye,
  Filter,
  Info,
  MapPin,
  Package,
  Plus,
  Search,
  Send,
  User,
} from 'lucide-react';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useAccessSafe } from '../context/RoleAccessContext';

type StockTakingSessionSummary = {
  id: string;
  sessionNumber: string;
  branch: string;
  subWarehouse: string;
  locationId: string;
  locationCode: string;
  auditor: string;
  date: string;
  status: 'draft' | 'counting' | 'submitted' | 'reviewed' | 'approved' | 'closed';
  totalItems: number;
  countedItems: number;
  discrepancies: number;
  totalVariance: number;
  createdDate: string;
  submittedDate?: string;
  approvedDate?: string;
};

type StockCountItem = {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  category: string;
  brand: string;
  systemQuantity: number;
  actualCount: number | null;
  damagedQuantity: number;
  variance: number;
  varianceValue: number;
  unitCost: number;
  notes: string;
  counted: boolean;
  discrepancyReason?: string;
  investigationRequired: boolean;
};

type WarehouseLocationOption = {
  id: string;
  branchId: string;
  name: string;
  code: string;
  type: string;
  responsibleUser: string;
  stockUnits: number;
  capacity: number;
  status: 'active' | 'inactive';
  notes: string;
};

type DashboardMetrics = {
  activeSessions: number;
  pendingApproval: number;
  completedThisMonth: number;
  totalDiscrepancies: number;
  totalVarianceValue: number;
  shrinkageRate: number;
  locationsNotCounted: number;
  highVarianceAlerts: number;
};

type StockTakingAlert = {
  sessionNumber: string;
  productName: string;
  variance: number;
  reason: string;
  branch: string;
  locationName: string;
};

type StockTakingSessionDetail = StockTakingSessionSummary & {
  items: StockCountItem[];
};

type BootstrapResponse = {
  ok: boolean;
  branches?: string[];
  locations?: Record<string, WarehouseLocationOption[]>;
  sessions?: StockTakingSessionSummary[];
  metrics?: DashboardMetrics;
  varianceTrend?: { month: string; variance: number }[];
  reasonBreakdown?: { reason: string; count: number }[];
  alerts?: StockTakingAlert[];
  error?: string;
};

const STATUS_COLORS: Record<string, string> = {
  draft: 'text-gray-600 bg-gray-50 border-gray-200',
  counting: 'text-blue-600 bg-blue-50 border-blue-200',
  submitted: 'text-purple-600 bg-purple-50 border-purple-200',
  reviewed: 'text-orange-600 bg-orange-50 border-orange-200',
  approved: 'text-green-600 bg-green-50 border-green-200',
  closed: 'text-gray-600 bg-gray-100 border-gray-300',
};

const REASON_LABELS: Record<string, string> = {
  damage: 'Damage',
  theft: 'Theft',
  'counting-error': 'Counting Error',
  'wrong-transfer': 'Wrong Transfer',
  'missing-item': 'Missing Item',
  'unrecorded-movement': 'Unrecorded Movement',
  other: 'Other',
};

const DISCREPANCY_REASONS = [
  { value: 'damage', label: 'Damage' },
  { value: 'theft', label: 'Theft' },
  { value: 'counting-error', label: 'Counting Error' },
  { value: 'wrong-transfer', label: 'Wrong Transfer' },
  { value: 'missing-item', label: 'Missing Item' },
  { value: 'unrecorded-movement', label: 'Unrecorded Movement' },
  { value: 'other', label: 'Other' },
];

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function StockTaking() {
  const [activeTab, setActiveTab] = useState('sessions');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [sessions, setSessions] = useState<StockTakingSessionSummary[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [locations, setLocations] = useState<Record<string, WarehouseLocationOption[]>>({});
  const [metrics, setMetrics] = useState<DashboardMetrics>({
    activeSessions: 0,
    pendingApproval: 0,
    completedThisMonth: 0,
    totalDiscrepancies: 0,
    totalVarianceValue: 0,
    shrinkageRate: 0,
    locationsNotCounted: 0,
    highVarianceAlerts: 0,
  });
  const [varianceTrend, setVarianceTrend] = useState<{ month: string; variance: number }[]>([]);
  const [reasonBreakdown, setReasonBreakdown] = useState<{ reason: string; count: number }[]>([]);
  const [alerts, setAlerts] = useState<StockTakingAlert[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showNewSessionModal, setShowNewSessionModal] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const loadDashboard = async () => {
    setIsLoading(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/stock-taking/bootstrap', {
        credentials: 'same-origin',
      });
      const data = await parseJsonResponse<BootstrapResponse>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to load stock taking data.');
      }
      setBranches(Array.isArray(data.branches) ? data.branches : []);
      setLocations(data.locations || {});
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
      setMetrics(data.metrics || metrics);
      setVarianceTrend(Array.isArray(data.varianceTrend) ? data.varianceTrend : []);
      setReasonBreakdown(Array.isArray(data.reasonBreakdown) ? data.reasonBreakdown : []);
      setAlerts(Array.isArray(data.alerts) ? data.alerts : []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load stock taking data.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadDashboard();
  }, []);

  const filteredSessions = useMemo(
    () =>
      sessions.filter((session) => {
        const query = searchQuery.trim().toLowerCase();
        const matchesSearch =
          !query ||
          session.sessionNumber.toLowerCase().includes(query) ||
          session.branch.toLowerCase().includes(query) ||
          session.subWarehouse.toLowerCase().includes(query) ||
          session.auditor.toLowerCase().includes(query);
        const matchesStatus = filterStatus === 'all' || session.status === filterStatus;
        return matchesSearch && matchesStatus;
      }),
    [sessions, searchQuery, filterStatus]
  );

  const pendingSessions = useMemo(
    () => sessions.filter((session) => session.status === 'submitted'),
    [sessions]
  );

  if (selectedSessionId) {
    return (
      <StockTakingDetail
        sessionId={selectedSessionId}
        onBack={() => setSelectedSessionId(null)}
        onRefresh={loadDashboard}
        canApprove={true}
      />
    );
  }

  return (
    <>
      {showNewSessionModal && (
        <NewSessionModal
          branches={branches}
          locations={locations}
          onClose={() => setShowNewSessionModal(false)}
          onCreated={async (sessionId) => {
            setShowNewSessionModal(false);
            await loadDashboard();
            setSelectedSessionId(sessionId);
          }}
        />
      )}

      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Stock Taking / Physical Count</h1>
            <p className="mt-1 text-gray-600">Physical inventory verification, discrepancy review, and controlled approval</p>
          </div>
          <button
            onClick={() => setShowNewSessionModal(true)}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-white shadow-sm transition-colors hover:bg-indigo-700"
          >
            <Plus className="h-4 w-4" />
            <span className="text-sm font-medium">New Stock Count Session</span>
          </button>
        </div>

        <div className="border-b border-gray-200">
          <div className="flex gap-6">
            {[
              { id: 'sessions', label: 'Stock Count Sessions', icon: ClipboardCheck },
              { id: 'pending-approval', label: 'Pending Approval', icon: Clock },
              { id: 'reports', label: 'Reports & Analytics', icon: BarChart3 },
              { id: 'alerts', label: 'Alerts', icon: AlertTriangle },
            ].map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 border-b-2 pb-3 transition-colors ${activeTab === tab.id ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-600 hover:text-gray-900'}`}
                >
                  <Icon className="h-4 w-4" />
                  <span className="text-sm font-medium">{tab.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

        {activeTab === 'sessions' && (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
              <KpiCard label="Active Sessions" value={String(metrics.activeSessions)} icon={Clock} tone="blue" />
              <KpiCard label="Pending Approval" value={String(metrics.pendingApproval)} icon={Send} tone="purple" />
              <KpiCard label="Completed" value={String(metrics.completedThisMonth)} icon={CheckCircle} tone="green" />
              <KpiCard label="Discrepancies" value={String(metrics.totalDiscrepancies)} icon={AlertTriangle} tone="orange" />
              <KpiCard label="Variance Value" value={`GHS ${metrics.totalVarianceValue.toLocaleString()}`} icon={BarChart3} tone="red" />
              <KpiCard label="Shrinkage Rate" value={`${metrics.shrinkageRate}%`} icon={AlertCircle} tone="amber" />
              <KpiCard label="Locations Not Counted" value={String(metrics.locationsNotCounted)} icon={MapPin} tone="indigo" />
              <KpiCard label="High Variance Alerts" value={String(metrics.highVarianceAlerts)} icon={AlertCircle} tone="red" />
            </div>

            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-center justify-between gap-4">
                <div className="max-w-md flex-1">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
                    <input
                      type="text"
                      placeholder="Search by session, branch, location, or auditor..."
                      value={searchQuery}
                      onChange={(event) => setSearchQuery(event.target.value)}
                      className="w-full rounded-lg border border-gray-200 py-2 pl-10 pr-4 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <select
                    value={filterStatus}
                    onChange={(event) => setFilterStatus(event.target.value)}
                    className="rounded-lg border border-gray-200 px-4 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="all">All Statuses</option>
                    <option value="draft">Draft</option>
                    <option value="counting">Counting</option>
                    <option value="submitted">Submitted</option>
                    <option value="approved">Approved</option>
                  </select>
                  <button className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50">
                    <Download className="h-4 w-4 text-gray-600" />
                    <span className="text-sm font-medium text-gray-700">Export</span>
                  </button>
                </div>
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="border-b border-gray-200 bg-gray-50">
                    <tr>
                      <th className="px-5 py-3 text-left text-xs font-medium uppercase text-gray-500">Session</th>
                      <th className="px-5 py-3 text-left text-xs font-medium uppercase text-gray-500">Location</th>
                      <th className="px-5 py-3 text-left text-xs font-medium uppercase text-gray-500">Auditor</th>
                      <th className="px-5 py-3 text-center text-xs font-medium uppercase text-gray-500">Progress</th>
                      <th className="px-5 py-3 text-center text-xs font-medium uppercase text-gray-500">Discrepancies</th>
                      <th className="px-5 py-3 text-right text-xs font-medium uppercase text-gray-500">Variance</th>
                      <th className="px-5 py-3 text-center text-xs font-medium uppercase text-gray-500">Status</th>
                      <th className="px-5 py-3 text-center text-xs font-medium uppercase text-gray-500">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {filteredSessions.map((session) => (
                      <tr key={session.id} className="hover:bg-gray-50">
                        <td className="px-5 py-4">
                          <div className="font-medium text-gray-900">{session.sessionNumber}</div>
                          <div className="text-sm text-gray-500">{session.date}</div>
                        </td>
                        <td className="px-5 py-4">
                          <div className="font-medium text-gray-900">{session.branch}</div>
                          <div className="text-sm text-gray-500">{session.subWarehouse}</div>
                        </td>
                        <td className="px-5 py-4 text-sm text-gray-700">{session.auditor}</td>
                        <td className="px-5 py-4 text-center text-sm text-gray-700">
                          {session.countedItems}/{session.totalItems}
                        </td>
                        <td className="px-5 py-4 text-center">
                          <span className={`font-semibold ${session.discrepancies > 0 ? 'text-orange-600' : 'text-green-600'}`}>{session.discrepancies}</span>
                        </td>
                        <td className={`px-5 py-4 text-right font-semibold ${session.totalVariance < 0 ? 'text-red-600' : session.totalVariance > 0 ? 'text-green-600' : 'text-gray-700'}`}>
                          {session.totalVariance > 0 ? '+' : ''}GHS {Math.abs(session.totalVariance).toLocaleString()}
                        </td>
                        <td className="px-5 py-4 text-center">
                          <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_COLORS[session.status] || STATUS_COLORS.draft}`}>
                            {session.status}
                          </span>
                        </td>
                        <td className="px-5 py-4 text-center">
                          <button
                            onClick={() => setSelectedSessionId(session.id)}
                            className="inline-flex items-center gap-1 rounded-lg bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-100"
                          >
                            <Eye className="h-4 w-4" />
                            Open
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!isLoading && filteredSessions.length === 0 && (
                      <tr>
                        <td colSpan={8} className="px-5 py-12 text-center text-sm text-gray-500">No stock taking sessions found.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {activeTab === 'pending-approval' && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {pendingSessions.length > 0 ? pendingSessions.map((session) => (
              <div key={session.id} className="rounded-lg border border-gray-200 bg-white p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-gray-900">{session.sessionNumber}</h3>
                    <p className="mt-1 text-sm text-gray-600">{session.branch} • {session.subWarehouse}</p>
                  </div>
                  <span className="inline-flex rounded-full border border-purple-200 bg-purple-50 px-2.5 py-1 text-xs font-medium text-purple-700">submitted</span>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-gray-500">Auditor</div>
                    <div className="font-medium text-gray-900">{session.auditor}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Submitted</div>
                    <div className="font-medium text-gray-900">{session.submittedDate || session.createdDate}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Discrepancies</div>
                    <div className={`font-medium ${session.discrepancies > 0 ? 'text-orange-600' : 'text-green-600'}`}>{session.discrepancies}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Variance</div>
                    <div className={`font-medium ${session.totalVariance < 0 ? 'text-red-600' : session.totalVariance > 0 ? 'text-green-600' : 'text-gray-900'}`}>
                      {session.totalVariance > 0 ? '+' : ''}GHS {Math.abs(session.totalVariance).toLocaleString()}
                    </div>
                  </div>
                </div>
                <div className="mt-4">
                  <button
                    onClick={() => setSelectedSessionId(session.id)}
                    className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700"
                  >
                    Review Session
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )) : (
              <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-sm text-gray-500 lg:col-span-2">
                All stock taking sessions have been processed.
              </div>
            )}
          </div>
        )}

        {activeTab === 'reports' && (
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <div className="rounded-lg border border-gray-200 bg-white p-5">
              <h3 className="font-semibold text-gray-900">Variance Trend</h3>
              <p className="mt-1 text-sm text-gray-500">Recent stock count variance by month</p>
              <div className="mt-4 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={varianceTrend}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="variance" stroke="#ef4444" strokeWidth={2} name="Variance Value" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="rounded-lg border border-gray-200 bg-white p-5">
              <h3 className="font-semibold text-gray-900">Discrepancy Reasons</h3>
              <p className="mt-1 text-sm text-gray-500">Most common causes recorded during counts</p>
              <div className="mt-4 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={reasonBreakdown} dataKey="count" nameKey="reason" cx="50%" cy="50%" outerRadius={90} label>
                      {reasonBreakdown.map((entry, index) => (
                        <Cell key={`${entry.reason}-${index}`} fill={['#6366f1', '#ef4444', '#f59e0b', '#14b8a6', '#8b5cf6', '#ec4899'][index % 6]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="rounded-lg border border-gray-200 bg-white p-5 xl:col-span-2">
              <h3 className="font-semibold text-gray-900">Reason Breakdown</h3>
              <div className="mt-4 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={reasonBreakdown}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="reason" />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="count" fill="#6366f1" name="Occurrences" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'alerts' && (
          <div className="space-y-4">
            {alerts.length > 0 ? alerts.map((alert, index) => (
              <div key={`${alert.sessionNumber}-${alert.productName}-${index}`} className="rounded-lg border border-red-200 bg-red-50 p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="font-semibold text-gray-900">{alert.productName}</h3>
                    <p className="mt-1 text-sm text-gray-700">
                      {alert.branch} • {alert.locationName} • Session {alert.sessionNumber}
                    </p>
                    <p className="mt-2 text-sm text-red-700">
                      Variance: {alert.variance > 0 ? '+' : ''}{alert.variance} • Reason: {REASON_LABELS[alert.reason] || alert.reason}
                    </p>
                  </div>
                  <span className="inline-flex rounded-full border border-red-200 bg-white px-2.5 py-1 text-xs font-medium text-red-700">investigate</span>
                </div>
              </div>
            )) : (
              <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-sm text-gray-500">
                No investigation alerts at the moment.
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function KpiCard({ label, value, icon: Icon, tone }: { label: string; value: string; icon: typeof Clock; tone: 'blue' | 'purple' | 'green' | 'orange' | 'red' | 'amber' | 'indigo' }) {
  const toneMap: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-600',
    purple: 'bg-purple-50 text-purple-600',
    green: 'bg-green-50 text-green-600',
    orange: 'bg-orange-50 text-orange-600',
    red: 'bg-red-50 text-red-600',
    amber: 'bg-amber-50 text-amber-600',
    indigo: 'bg-indigo-50 text-indigo-600',
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between">
        <div className={`rounded-lg p-2 ${toneMap[tone]}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
      <div className="mb-1 text-2xl font-semibold text-gray-900">{value}</div>
      <div className="text-sm text-gray-600">{label}</div>
    </div>
  );
}

function NewSessionModal({
  branches,
  locations,
  onClose,
  onCreated,
}: {
  branches: string[];
  locations: Record<string, WarehouseLocationOption[]>;
  onClose: () => void;
  onCreated: (sessionId: string) => Promise<void> | void;
}) {
  const [branch, setBranch] = useState('');
  const [locationId, setLocationId] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState('');

  const availableLocations = branch ? (locations[branch] || []).filter((item) => item.status === 'active') : [];

  const handleCreate = async () => {
    setIsSaving(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/stock-taking-sessions', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ branch, locationId, date }),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockTakingSessionSummary }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to create stock taking session.');
      }
      await onCreated(data.session.id);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : 'Unable to create stock taking session.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-xl rounded-lg bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">New Stock Count Session</h3>
          <button onClick={onClose} className="rounded p-2 hover:bg-gray-100">
            <AlertCircle className="h-4 w-4 text-gray-500" />
          </button>
        </div>

        <div className="mt-5 space-y-4">
          {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700">Branch</label>
            <select value={branch} onChange={(event) => { setBranch(event.target.value); setLocationId(''); }} className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500">
              <option value="">Select branch...</option>
              {branches.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700">Warehouse / Room</label>
            <select value={locationId} onChange={(event) => setLocationId(event.target.value)} className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500" disabled={!branch}>
              <option value="">Select warehouse/room...</option>
              {availableLocations.map((item) => (
                <option key={item.id} value={item.id}>{item.name} ({item.code})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700">Count Date</label>
            <input type="date" value={date} onChange={(event) => setDate(event.target.value)} className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
            Creating a session snapshots the current system quantities for this location. Auditors then enter the physical count against that baseline.
          </div>
        </div>

        <div className="mt-6 flex items-center justify-end gap-3">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          <button onClick={() => void handleCreate()} disabled={!branch || !locationId || isSaving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300">
            {isSaving ? 'Creating...' : 'Create Session'}
          </button>
        </div>
      </div>
    </div>
  );
}

function StockTakingDetail({
  sessionId,
  onBack,
  onRefresh,
  canApprove,
}: {
  sessionId: string;
  onBack: () => void;
  onRefresh: () => Promise<void> | void;
  canApprove: boolean;
}) {
  const { canViewPricing } = useAccessSafe();
  const [session, setSession] = useState<StockTakingSessionDetail | null>(null);
  const [items, setItems] = useState<StockCountItem[]>([]);
  const [activeSubTab, setActiveSubTab] = useState('count-sheet');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState('');

  const loadDetail = async () => {
    setIsLoading(true);
    setError('');
    try {
      const response = await fetch(`/api/inventory/stock-taking-sessions/${encodeURIComponent(sessionId)}`, {
        credentials: 'same-origin',
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockTakingSessionDetail }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to load stock taking session.');
      }
      setSession(data.session);
      setItems(Array.isArray(data.session.items) ? data.session.items : []);
    } catch (detailError) {
      setError(detailError instanceof Error ? detailError.message : 'Unable to load stock taking session.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadDetail();
  }, [sessionId]);

  const countedItems = items.filter((item) => item.counted).length;
  const discrepancyItems = items.filter((item) => item.counted && item.variance !== 0);
  const totalVariance = items.reduce((sum, item) => sum + item.varianceValue, 0);

  const updateItem = (id: string, updates: Partial<StockCountItem>) => {
    setItems((current) =>
      current.map((item) => {
        if (item.id !== id) return item;
        const next = { ...item, ...updates };
        const actualCount = next.actualCount ?? null;
        const damagedQuantity = Math.max(0, next.damagedQuantity || 0);
        const counted = actualCount !== null;
        const effectiveCount = counted ? Math.max(0, actualCount - damagedQuantity) : next.systemQuantity;
        const variance = counted ? effectiveCount - next.systemQuantity : 0;
        const varianceValue = variance * next.unitCost;
        return {
          ...next,
          counted,
          damagedQuantity,
          variance,
          varianceValue,
          investigationRequired: variance !== 0 && (Math.abs(variance) >= 3 || ['theft', 'missing-item', 'unrecorded-movement'].includes(next.discrepancyReason || '')),
          discrepancyReason: variance === 0 ? '' : next.discrepancyReason || '',
        };
      })
    );
  };

  const persistCounts = async () => {
    setIsSaving(true);
    setError('');
    try {
      const response = await fetch(`/api/inventory/stock-taking-sessions/${encodeURIComponent(sessionId)}/counts`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: items.map((item) => ({
            id: item.id,
            actualCount: item.actualCount,
            damagedQuantity: item.damagedQuantity,
            discrepancyReason: item.discrepancyReason,
            notes: item.notes,
          })),
        }),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockTakingSessionDetail }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to save counts.');
      }
      setSession(data.session);
      setItems(data.session.items || []);
      await onRefresh();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save counts.');
    } finally {
      setIsSaving(false);
    }
  };

  const submitSession = async () => {
    setIsSaving(true);
    setError('');
    try {
      const response = await fetch(`/api/inventory/stock-taking-sessions/${encodeURIComponent(sessionId)}/submit`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockTakingSessionDetail }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to submit session.');
      }
      setSession(data.session);
      setItems(data.session.items || []);
      await onRefresh();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to submit session.');
    } finally {
      setIsSaving(false);
    }
  };

  const approveSession = async () => {
    setIsSaving(true);
    setError('');
    try {
      const response = await fetch(`/api/inventory/stock-taking-sessions/${encodeURIComponent(sessionId)}/approve`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockTakingSessionDetail }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to approve session.');
      }
      setSession(data.session);
      setItems(data.session.items || []);
      await onRefresh();
    } catch (approveError) {
      setError(approveError instanceof Error ? approveError.message : 'Unable to approve session.');
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading || !session) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-8 text-sm text-gray-500">
        Loading stock taking session...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="inline-flex items-center gap-2 text-sm font-medium text-gray-600 hover:text-gray-900">
        <ArrowLeft className="h-4 w-4" />
        Back to sessions
      </button>

      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">{session.sessionNumber}</h2>
            <p className="mt-1 text-gray-600">Stock count session for {session.branch} - {session.subWarehouse}</p>
          </div>
          <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_COLORS[session.status] || STATUS_COLORS.draft}`}>
            {session.status}
          </span>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <InfoCard icon={Building2} label="Branch" value={session.branch} />
          <InfoCard icon={MapPin} label="Location" value={`${session.subWarehouse} (${session.locationCode})`} />
          <InfoCard icon={User} label="Auditor" value={session.auditor} />
          <InfoCard icon={Calendar} label="Count Date" value={session.date} />
        </div>

        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <SummaryCard label="Progress" value={`${countedItems}/${items.length}`} tone="blue" />
          <SummaryCard label="Discrepancies" value={String(discrepancyItems.length)} tone={discrepancyItems.length > 0 ? 'orange' : 'green'} />
          <SummaryCard label="Variance Value" value={`${totalVariance > 0 ? '+' : ''}GHS ${Math.abs(totalVariance).toLocaleString()}`} tone={totalVariance < 0 ? 'red' : totalVariance > 0 ? 'green' : 'gray'} />
        </div>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="border-b border-gray-200">
        <div className="flex gap-6">
          {[
            { id: 'count-sheet', label: 'Count Sheet' },
            { id: 'discrepancies', label: 'Discrepancies' },
            { id: 'approval', label: 'Approval Workflow' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveSubTab(tab.id)}
              className={`border-b-2 pb-3 text-sm font-medium transition-colors ${activeSubTab === tab.id ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-600 hover:text-gray-900'}`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {activeSubTab === 'count-sheet' && (
        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Product</th>
                  <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">System Qty</th>
                  <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Actual Count</th>
                  <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Damaged</th>
                  <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Variance</th>
                  {canViewPricing && <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Variance Value</th>}
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Reason</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {items.map((item) => (
                  <tr key={item.id} className={item.variance !== 0 && item.counted ? 'bg-red-50/40' : ''}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{item.productName}</div>
                      <div className="text-sm text-gray-500">{item.sku} • {item.category}</div>
                    </td>
                    <td className="px-4 py-3 text-center font-medium text-gray-900">{item.systemQuantity}</td>
                    <td className="px-4 py-3 text-center">
                      <input
                        type="number"
                        min="0"
                        value={item.actualCount ?? ''}
                        disabled={session.status !== 'draft' && session.status !== 'counting'}
                        onChange={(event) => updateItem(item.id, { actualCount: event.target.value === '' ? null : parseInt(event.target.value, 10) })}
                        className="w-24 rounded-lg border border-gray-300 px-3 py-1.5 text-center text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100"
                      />
                    </td>
                    <td className="px-4 py-3 text-center">
                      <input
                        type="number"
                        min="0"
                        value={item.damagedQuantity}
                        disabled={session.status !== 'draft' && session.status !== 'counting'}
                        onChange={(event) => updateItem(item.id, { damagedQuantity: parseInt(event.target.value || '0', 10) })}
                        className="w-20 rounded-lg border border-gray-300 px-3 py-1.5 text-center text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100"
                      />
                    </td>
                    <td className={`px-4 py-3 text-center font-semibold ${item.variance < 0 ? 'text-red-600' : item.variance > 0 ? 'text-green-600' : 'text-gray-700'}`}>
                      {item.variance > 0 ? '+' : ''}{item.variance}
                    </td>
                    {canViewPricing && (
                      <td className={`px-4 py-3 text-right font-semibold ${item.varianceValue < 0 ? 'text-red-600' : item.varianceValue > 0 ? 'text-green-600' : 'text-gray-700'}`}>
                        {item.varianceValue > 0 ? '+' : ''}GHS {Math.abs(item.varianceValue).toLocaleString()}
                      </td>
                    )}
                    <td className="px-4 py-3">
                      <select
                        value={item.discrepancyReason || ''}
                        disabled={(session.status !== 'draft' && session.status !== 'counting') || item.variance === 0}
                        onChange={(event) => updateItem(item.id, { discrepancyReason: event.target.value })}
                        className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100"
                      >
                        <option value="">Select reason...</option>
                        {DISCREPANCY_REASONS.map((reason) => (
                          <option key={reason.value} value={reason.value}>{reason.label}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3">
                      <input
                        type="text"
                        value={item.notes}
                        disabled={session.status !== 'draft' && session.status !== 'counting'}
                        onChange={(event) => updateItem(item.id, { notes: event.target.value })}
                        className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100"
                        placeholder="Count notes..."
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeSubTab === 'discrepancies' && (
        <div className="space-y-4">
          {discrepancyItems.length > 0 ? discrepancyItems.map((item) => (
            <div key={item.id} className="rounded-lg border border-orange-200 bg-orange-50 p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="font-semibold text-gray-900">{item.productName}</h3>
                  <p className="mt-1 text-sm text-gray-700">
                    {item.sku} • System {item.systemQuantity} • Counted {item.actualCount ?? 0} • Damaged {item.damagedQuantity}
                  </p>
                  <p className={`mt-2 text-sm font-medium ${item.variance < 0 ? 'text-red-700' : 'text-green-700'}`}>
                    Variance: {item.variance > 0 ? '+' : ''}{item.variance} • Value: {item.varianceValue > 0 ? '+' : ''}GHS {Math.abs(item.varianceValue).toLocaleString()}
                  </p>
                </div>
                {item.investigationRequired && (
                  <span className="inline-flex rounded-full border border-red-200 bg-white px-2.5 py-1 text-xs font-medium text-red-700">
                    Investigation required
                  </span>
                )}
              </div>
              <div className="mt-3 text-sm text-gray-700">
                Reason: <span className="font-medium">{REASON_LABELS[item.discrepancyReason || 'other'] || item.discrepancyReason || 'Not set'}</span>
              </div>
              {item.notes && <div className="mt-1 text-sm text-gray-600">{item.notes}</div>}
            </div>
          )) : (
            <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-sm text-gray-500">
              No discrepancies recorded for this session.
            </div>
          )}
        </div>
      )}

      {activeSubTab === 'approval' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
            Stock taking discrepancies stay separate from live inventory until the session is approved. Approval writes signed inventory adjustments for each variance to the selected location.
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <SummaryCard label="Counted Items" value={`${countedItems}/${items.length}`} tone="blue" />
              <SummaryCard label="Discrepancies" value={String(discrepancyItems.length)} tone={discrepancyItems.length > 0 ? 'orange' : 'green'} />
              <SummaryCard label="Current Status" value={session.status} tone="gray" />
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-end gap-3">
        {(session.status === 'draft' || session.status === 'counting') && (
          <>
            <button onClick={() => void persistCounts()} disabled={isSaving} className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50">
              {isSaving ? 'Saving...' : 'Save Progress'}
            </button>
            <button onClick={() => void submitSession()} disabled={isSaving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300">
              Submit for Approval
            </button>
          </>
        )}
        {session.status === 'submitted' && canApprove && (
          <button onClick={() => void approveSession()} disabled={isSaving} className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:bg-gray-300">
            Approve and Apply Inventory Adjustment
          </button>
        )}
      </div>
    </div>
  );
}

function InfoCard({ icon: Icon, label, value }: { icon: typeof Building2; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-2 flex items-center gap-2 text-gray-500">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase">{label}</span>
      </div>
      <div className="font-medium text-gray-900">{value}</div>
    </div>
  );
}

function SummaryCard({ label, value, tone }: { label: string; value: string; tone: 'blue' | 'orange' | 'green' | 'red' | 'gray' }) {
  const tones: Record<string, string> = {
    blue: 'text-blue-600 bg-blue-50',
    orange: 'text-orange-600 bg-orange-50',
    green: 'text-green-600 bg-green-50',
    red: 'text-red-600 bg-red-50',
    gray: 'text-gray-700 bg-gray-50',
  };
  return (
    <div className={`rounded-lg border border-gray-200 p-4 ${tones[tone]}`}>
      <div className="text-sm">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
