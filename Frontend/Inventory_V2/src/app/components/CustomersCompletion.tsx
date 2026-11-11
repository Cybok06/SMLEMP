import { useDeferredValue, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Eye,
  Package,
  RefreshCw,
  Search,
  Truck,
  Users,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { PriceMask } from './PriceGuard';

type CompletionRow = {
  id: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  customerImage: string;
  branch: string;
  agentId: string;
  agentName: string;
  productCard: string;
  completion: number;
  paidAmount: number;
  totalAmount: number;
  remainingAmount: number;
  stockReady: number;
  deliveryStatus: string;
  purchaseStatus: string;
  enrollmentDate: string;
  estimatedFinish: string;
  profileUrl: string;
};

type CompletionAgentOption = {
  id: string;
  name: string;
  branch: string;
};

type CompletionCounts = {
  all: number;
  '70plus': number;
  '80plus': number;
  '90plus': number;
  completed: number;
  'awaiting-stock': number;
  'awaiting-delivery': number;
};

type CompletionPagination = {
  page: number;
  perPage: number;
  totalItems: number;
  totalPages: number;
};

type CompletionResponse = {
  ok: boolean;
  customers?: CompletionRow[];
  branches?: string[];
  agents?: CompletionAgentOption[];
  counts?: Partial<CompletionCounts>;
  pagination?: Partial<CompletionPagination>;
  error?: string;
};

type CustomerProfile = {
  customer: {
    id: string; name: string; phone: string; image: string; location: string;
    digitalAddress: string; status: string; createdAt: string; branch: string;
    agentName: string; managerName: string;
  };
  products: Array<{
    index: number; name: string; image: string; quantity: number; total: number;
    paid: number; remaining: number; completion: number; status: string;
    purchaseDate: string; estimatedEndDate: string;
  }>;
  payments: Array<{
    id: string; productIndex: number; amount: number; signedAmount: number;
    paymentType: string; date: string; reference: string; status: string;
  }>;
  summary: { productCount: number; totalValue: number; totalPaid: number; totalRemaining: number };
};

type CustomerProfileResponse = CustomerProfile & { ok: boolean; error?: string };

const TABS = [
  { id: 'all', label: 'All Customers' },
  { id: '70plus', label: '70%+' },
  { id: '80plus', label: '80%+' },
  { id: '90plus', label: '90%+' },
  { id: 'completed', label: 'Completed' },
  { id: 'awaiting-stock', label: 'Awaiting Stock' },
  { id: 'awaiting-delivery', label: 'Awaiting Delivery' },
] as const;

const DEFAULT_COUNTS: CompletionCounts = {
  all: 0,
  '70plus': 0,
  '80plus': 0,
  '90plus': 0,
  completed: 0,
  'awaiting-stock': 0,
  'awaiting-delivery': 0,
};

const DEFAULT_PAGINATION: CompletionPagination = {
  page: 1,
  perPage: 20,
  totalItems: 0,
  totalPages: 1,
};

function deliveryTone(status: string): string {
  const key = String(status || '').toLowerCase();
  if (key === 'delivered') return 'text-green-600 bg-green-50 border-green-200';
  if (key === 'awaiting-delivery' || key === 'completed') return 'text-blue-600 bg-blue-50 border-blue-200';
  if (key === 'awaiting-stock') return 'text-amber-600 bg-amber-50 border-amber-200';
  return 'text-gray-600 bg-gray-50 border-gray-200';
}

function deliveryLabel(status: string): string {
  const key = String(status || '').toLowerCase();
  if (key === 'awaiting-delivery') return 'Awaiting Delivery';
  if (key === 'awaiting-stock') return 'Awaiting Stock';
  if (key === 'completed') return 'Completed';
  if (key === 'delivered') return 'Delivered';
  return 'Not Ready';
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function CustomersCompletion() {
  const [rows, setRows] = useState<CompletionRow[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [agents, setAgents] = useState<CompletionAgentOption[]>([]);
  const [counts, setCounts] = useState<CompletionCounts>(DEFAULT_COUNTS);
  const [pagination, setPagination] = useState<CompletionPagination>(DEFAULT_PAGINATION);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const [activeTab, setActiveTab] = useState<string>('all');
  const [branchFilter, setBranchFilter] = useState('all');
  const [agentFilter, setAgentFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState('');

  const openProfile = async (customerId: string) => {
    setProfile(null);
    setProfileError('');
    setProfileLoading(true);
    try {
      const response = await fetch(`/api/inventory/customers/${customerId}/profile`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await parseJsonResponse<CustomerProfileResponse>(response);
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'Unable to load customer profile.');
      setProfile({
        customer: payload.customer,
        products: payload.products || [],
        payments: payload.payments || [],
        summary: payload.summary,
      });
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : 'Unable to load customer profile.');
    } finally {
      setProfileLoading(false);
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        perPage: '20',
        tab: activeTab,
        search: deferredSearchQuery.trim(),
        branch: branchFilter,
        agent: agentFilter,
      });
      const response = await fetch(`/api/inventory/customers-completion?${params.toString()}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await parseJsonResponse<CompletionResponse>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to load customers completion data.');
      }
      setRows(Array.isArray(payload.customers) ? payload.customers : []);
      setBranches(Array.isArray(payload.branches) ? payload.branches : []);
      setAgents(Array.isArray(payload.agents) ? payload.agents : []);
      setCounts({ ...DEFAULT_COUNTS, ...(payload.counts || {}) });
      setPagination({
        ...DEFAULT_PAGINATION,
        ...(payload.pagination || {}),
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load customers completion data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [activeTab, agentFilter, branchFilter, page, deferredSearchQuery]);

  const summaryCards = [
    { label: 'Customers', value: counts.all, icon: Users, tone: 'bg-slate-50 text-slate-700' },
    { label: '90%+', value: counts['90plus'], icon: CheckCircle, tone: 'bg-violet-50 text-violet-600' },
    { label: 'Awaiting Stock', value: counts['awaiting-stock'], icon: Package, tone: 'bg-amber-50 text-amber-600' },
    { label: 'Awaiting Delivery', value: counts['awaiting-delivery'], icon: Truck, tone: 'bg-blue-50 text-blue-600' },
  ];

  const visibleAgents = agents.filter((agent) => branchFilter === 'all' || agent.branch === branchFilter);
  const pageStart = pagination.totalItems === 0 ? 0 : (pagination.page - 1) * pagination.perPage + 1;
  const pageEnd = Math.min(pagination.totalItems, pagination.page * pagination.perPage);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Customers & Completion</h1>
          <p className="mt-1 text-gray-600">Track every customer in the database, payment completion, and fulfillment readiness by branch and agent.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => toast.info('Export for this page is not wired yet.')}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <Download className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Export</span>
          </button>
          <button
            type="button"
            onClick={() => void loadData()}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Refresh</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {summaryCards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="rounded-xl border border-gray-200 bg-white p-4">
              <div className={`mb-3 inline-flex rounded-lg p-2 ${card.tone}`}>
                <Icon className="h-4 w-4" />
              </div>
              <div className="text-xl font-semibold text-gray-900">{card.value}</div>
              <div className="mt-1 text-xs text-gray-600">{card.label}</div>
            </div>
          );
        })}
      </div>

      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="overflow-x-auto border-b border-gray-200 px-2 pt-2">
          <div className="flex items-center gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => {
                  setActiveTab(tab.id);
                  setPage(1);
                }}
                className={`-mb-px whitespace-nowrap rounded-t-lg border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'border-indigo-600 bg-indigo-50/40 text-indigo-600'
                    : 'border-transparent text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`}
              >
                <span>{tab.label}</span>
                <span className={`ml-2 rounded-md px-1.5 py-0.5 text-xs ${activeTab === tab.id ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                  {counts[tab.id] ?? 0}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-3 border-b border-gray-200 p-4 md:grid-cols-[minmax(0,1.5fr)_180px_220px]">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(event) => {
                setSearchQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Search customer, phone, product card, branch, or agent..."
              className="w-full rounded-lg border border-gray-200 py-2 pl-10 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <select
            value={branchFilter}
            onChange={(event) => {
              setBranchFilter(event.target.value);
              setAgentFilter('all');
              setPage(1);
            }}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="all">All Branches</option>
            {branches.map((branch) => (
              <option key={branch} value={branch}>
                {branch}
              </option>
            ))}
          </select>
          <select
            value={agentFilter}
            onChange={(event) => {
              setAgentFilter(event.target.value);
              setPage(1);
            }}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="all">All Agents</option>
            {visibleAgents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}{agent.branch ? ` (${agent.branch})` : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="p-4">
          {loading ? (
            <div className="rounded-lg border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              Loading customer completion data...
            </div>
          ) : rows.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center">
              <Users className="mx-auto mb-3 h-8 w-8 text-gray-300" />
              <p className="text-sm font-medium text-gray-700">No customer records found</p>
              <p className="mt-1 text-sm text-gray-500">Try changing the branch, agent, search, or completion filter.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-3">Customer</th>
                    <th className="px-4 py-3">Branch / Agent</th>
                    <th className="px-4 py-3">Product Card</th>
                    <th className="px-4 py-3">Completion</th>
                    <th className="px-4 py-3">Payment Progress</th>
                    <th className="px-4 py-3">Est. Finish</th>
                    <th className="px-4 py-3">Delivery Status</th>
                    <th className="px-4 py-3 text-right">Profile</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {rows.map((row) => (
                    <tr key={row.id} className="hover:bg-gray-50/60">
                      <td className="px-4 py-4">
                        <div className="font-medium text-gray-900">{row.customerName || 'Customer'}</div>
                        <div className="text-sm text-gray-500">{row.customerPhone || '-'}</div>
                      </td>
                      <td className="px-4 py-4">
                        <div className="text-sm text-gray-900">{row.branch || '-'}</div>
                        <div className="mt-1 text-xs text-gray-500">{row.agentName || 'Unassigned'}</div>
                      </td>
                      <td className="px-4 py-4">
                        <div className="text-sm text-gray-900">{row.productCard}</div>
                        <div className="mt-1 text-xs text-gray-500">Enrolled: {row.enrollmentDate || '-'}</div>
                      </td>
                      <td className="min-w-[180px] px-4 py-4">
                        <div className="flex items-center gap-3">
                          <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-200">
                            <div className="h-2 rounded-full bg-indigo-600" style={{ width: `${row.completion}%` }} />
                          </div>
                          <span className="text-sm font-semibold text-gray-900">{row.completion}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-4">
                        <div className="text-sm text-gray-900">
                          <PriceMask value={row.paidAmount} /> / <PriceMask value={row.totalAmount} />
                        </div>
                        <div className="mt-1 text-xs text-gray-500">
                          Remaining: <PriceMask value={row.remainingAmount} />
                        </div>
                      </td>
                      <td className="px-4 py-4 text-sm text-gray-700">{row.estimatedFinish || '-'}</td>
                      <td className="px-4 py-4">
                        <span className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs font-medium ${deliveryTone(row.deliveryStatus)}`}>
                          {row.deliveryStatus === 'delivered' ? <CheckCircle className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
                          {deliveryLabel(row.deliveryStatus)}
                        </span>
                      </td>
                      <td className="px-4 py-4">
                        <div className="flex items-center justify-end">
                          <button
                            type="button"
                            onClick={() => void openProfile(row.customerId)}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                          >
                            <Eye className="h-4 w-4" />
                            View
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex flex-col gap-3 border-t border-gray-200 px-4 py-3 text-sm text-gray-600 md:flex-row md:items-center md:justify-between">
          <div>
            Showing {pageStart}-{pageEnd} of {pagination.totalItems} records
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={pagination.page <= 1 || loading}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ChevronLeft className="h-4 w-4" />
              Prev
            </button>
            <div className="rounded-lg border border-gray-200 px-3 py-2 text-gray-700">
              Page {pagination.page} of {pagination.totalPages}
            </div>
            <button
              type="button"
              onClick={() => setPage((current) => Math.min(pagination.totalPages, current + 1))}
              disabled={pagination.page >= pagination.totalPages || loading}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {(profileLoading || profileError || profile) && (
        <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/40" role="dialog" aria-modal="true" aria-label="Customer profile">
          <button type="button" className="flex-1 cursor-default" aria-label="Close customer profile" onClick={() => { setProfile(null); setProfileError(''); setProfileLoading(false); }} />
          <aside className="h-full w-full max-w-2xl overflow-y-auto bg-white shadow-2xl">
            <div className="sticky top-0 z-10 flex items-start justify-between border-b border-gray-200 bg-white px-6 py-5">
              <div><h2 className="text-xl font-semibold text-gray-900">Customer Profile</h2><p className="mt-1 text-sm text-gray-500">Customer, products and payment history</p></div>
              <button type="button" onClick={() => { setProfile(null); setProfileError(''); setProfileLoading(false); }} className="rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="Close"><X className="h-5 w-5" /></button>
            </div>

            {profileLoading ? (
              <div className="space-y-4 p-6"><div className="h-28 animate-pulse rounded-xl bg-gray-100" /><div className="h-52 animate-pulse rounded-xl bg-gray-100" /><div className="h-52 animate-pulse rounded-xl bg-gray-100" /></div>
            ) : profileError ? (
              <div className="p-6"><div className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">{profileError}</div></div>
            ) : profile ? (
              <div className="space-y-6 p-6">
                <section className="rounded-xl border border-gray-200 p-5">
                  <div className="flex items-center gap-4">
                    {profile.customer.image ? <img src={profile.customer.image} alt="" className="h-16 w-16 rounded-full object-cover" /> : <div className="flex h-16 w-16 items-center justify-center rounded-full bg-indigo-100 text-xl font-semibold text-indigo-700">{profile.customer.name.slice(0, 1).toUpperCase()}</div>}
                    <div><h3 className="text-lg font-semibold text-gray-900">{profile.customer.name}</h3><p className="text-sm text-gray-600">{profile.customer.phone || 'No phone number'}</p><span className="mt-1 inline-flex rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize text-gray-700">{profile.customer.status || 'Active'}</span></div>
                  </div>
                  <dl className="mt-5 grid gap-3 sm:grid-cols-2">
                    <ProfileInfo label="Branch" value={profile.customer.branch || '-'} /><ProfileInfo label="Agent" value={profile.customer.agentName || 'Unassigned'} />
                    <ProfileInfo label="Manager" value={profile.customer.managerName || '-'} /><ProfileInfo label="Location" value={profile.customer.location || '-'} />
                    <ProfileInfo label="Digital address" value={profile.customer.digitalAddress || '-'} /><ProfileInfo label="Customer since" value={profile.customer.createdAt ? new Date(profile.customer.createdAt).toLocaleDateString() : '-'} />
                  </dl>
                </section>

                <section>
                  <h3 className="font-semibold text-gray-900">Account summary</h3>
                  <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <SummaryTile label="Products" value={String(profile.summary.productCount)} />
                    <SummaryTile label="Total value" value={<PriceMask value={profile.summary.totalValue} />} />
                    <SummaryTile label="Total paid" value={<PriceMask value={profile.summary.totalPaid} />} />
                    <SummaryTile label="Remaining" value={<PriceMask value={profile.summary.totalRemaining} />} />
                  </div>
                </section>

                <section>
                  <h3 className="font-semibold text-gray-900">Product details</h3>
                  <div className="mt-3 space-y-3">{profile.products.map((product) => <div key={product.index} className="rounded-xl border border-gray-200 p-4">
                    <div className="flex items-start justify-between gap-3"><div><div className="font-medium text-gray-900">{product.name}</div><div className="mt-1 text-xs text-gray-500">Product #{product.index + 1} · Quantity {product.quantity}</div></div><span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize text-gray-700">{product.status}</span></div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-200"><div className="h-full rounded-full bg-indigo-600" style={{ width: `${product.completion}%` }} /></div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-xs"><div>Total<br /><strong><PriceMask value={product.total} /></strong></div><div>Paid<br /><strong><PriceMask value={product.paid} /></strong></div><div>Remaining<br /><strong><PriceMask value={product.remaining} /></strong></div></div>
                  </div>)}</div>
                  {!profile.products.length && <div className="mt-3 rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">No product purchases found.</div>}
                </section>

                <section>
                  <h3 className="font-semibold text-gray-900">Payment history</h3>
                  <div className="mt-3 overflow-hidden rounded-xl border border-gray-200">
                    <table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-4 py-3">Date</th><th className="px-4 py-3">Product</th><th className="px-4 py-3">Type</th><th className="px-4 py-3 text-right">Amount</th></tr></thead>
                      <tbody className="divide-y divide-gray-100">{profile.payments.map((payment) => <tr key={payment.id}><td className="px-4 py-3">{payment.date ? new Date(payment.date).toLocaleDateString() : '-'}</td><td className="px-4 py-3">{payment.productIndex >= 0 ? `#${payment.productIndex + 1}` : '-'}</td><td className="px-4 py-3 capitalize">{payment.paymentType.toLowerCase().replaceAll('_', ' ')}</td><td className={`px-4 py-3 text-right font-medium ${payment.signedAmount < 0 ? 'text-rose-600' : 'text-emerald-600'}`}><PriceMask value={payment.signedAmount} /></td></tr>)}</tbody>
                    </table>
                    {!profile.payments.length && <div className="p-6 text-center text-sm text-gray-500">No payment history found.</div>}
                  </div>
                </section>
              </div>
            ) : null}
          </aside>
        </div>
      )}
    </div>
  );
}

function ProfileInfo({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-gray-50 p-3"><dt className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</dt><dd className="mt-1 text-sm font-medium text-gray-900">{value}</dd></div>;
}

function SummaryTile({ label, value }: { label: string; value: ReactNode }) {
  return <div className="rounded-xl border border-gray-200 p-3"><div className="text-base font-semibold text-gray-900">{value}</div><div className="mt-1 text-xs text-gray-500">{label}</div></div>;
}
