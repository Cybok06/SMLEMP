import { InventoryDeductionModal, type DeductionJob } from './InventoryDeductionModal';
import { useEffect, useState, type ReactNode } from 'react';
import {
  Box,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  CheckSquare,
  Clock3,
  Download,
  Eye,
  LoaderCircle,
  Package,
  RefreshCw,
  Search,
  Square,
  Truck,
  User,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { PriceMask } from './PriceGuard';

type SubmittedCardStatus = 'pending' | 'packaging' | 'delivering' | 'delivered';

type SubmittedCardHistoryItem = {
  status: string;
  label: string;
  actorName: string;
  actorRole: string;
  timestamp: string;
  notes: string;
};

type SubmittedCardRecord = {
  id: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  productIndex: number | null;
  productName: string;
  productImage: string;
  purchaseType: string;
  quantity: number;
  productTotal: number;
  amountPaid: number;
  amountLeft: number;
  branch: string;
  agentName: string;
  status: SubmittedCardStatus;
  statusLabel: string;
  nextStatus: SubmittedCardStatus | null;
  nextStatusLabel: string;
  submittedAt: string;
  updatedAt: string;
  daysWaiting: number;
  source: string;
  history: SubmittedCardHistoryItem[];
};

type SubmittedCardCounts = {
  total: number;
  open: number;
  pending: number;
  packaging: number;
  delivering: number;
  delivered: number;
};

type BootstrapResponse = {
  ok: boolean;
  cards?: SubmittedCardRecord[];
  counts?: SubmittedCardCounts;
  branches?: string[];
  agents?: string[];
  pagination?: {
    page: number;
    perPage: number;
    total: number;
    totalPages: number;
  };
  error?: string;
};

type StatusResponse = {
  ok: boolean;
  card?: SubmittedCardRecord;
  counts?: SubmittedCardCounts;
  error?: string;
};

type BulkStatusResponse = {
  ok: boolean;
  updated?: number;
  skipped?: number;
  status?: SubmittedCardStatus;
  statusLabel?: string;
  counts?: SubmittedCardCounts;
  error?: string;
};

type SubmittedCardsProps = {
  onCountsChange?: () => Promise<void> | void;
};

const EMPTY_COUNTS: SubmittedCardCounts = {
  total: 0,
  open: 0,
  pending: 0,
  packaging: 0,
  delivering: 0,
  delivered: 0,
};

const EMPTY_PAGINATION = {
  page: 1,
  perPage: 20,
  total: 0,
  totalPages: 1,
};

function formatDateTime(value: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString();
}

function formatDate(value: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleDateString();
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 160) || `HTTP ${response.status}`);
  }
}

function statusBadge(status: SubmittedCardStatus): string {
  const styles: Record<SubmittedCardStatus, string> = {
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    packaging: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    delivering: 'bg-blue-50 text-blue-700 border-blue-200',
    delivered: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  };
  return `inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${styles[status]}`;
}

function statusButtonStyle(status: SubmittedCardStatus): string {
  const styles: Record<SubmittedCardStatus, string> = {
    pending: 'bg-amber-600 hover:bg-amber-700',
    packaging: 'bg-indigo-600 hover:bg-indigo-700',
    delivering: 'bg-blue-600 hover:bg-blue-700',
    delivered: 'bg-emerald-600 hover:bg-emerald-700',
  };
  return styles[status];
}

function summaryTone(id: string): { bg: string; fg: string } {
  const tones: Record<string, { bg: string; fg: string }> = {
    total: { bg: 'bg-slate-50', fg: 'text-slate-700' },
    open: { bg: 'bg-amber-50', fg: 'text-amber-700' },
    packaging: { bg: 'bg-indigo-50', fg: 'text-indigo-700' },
    delivering: { bg: 'bg-blue-50', fg: 'text-blue-700' },
    delivered: { bg: 'bg-emerald-50', fg: 'text-emerald-700' },
  };
  return tones[id] || tones.total;
}

export function SubmittedCards({ onCountsChange }: SubmittedCardsProps) {
  const [cards, setCards] = useState<SubmittedCardRecord[]>([]);
  const [counts, setCounts] = useState<SubmittedCardCounts>(EMPTY_COUNTS);
  const [branches, setBranches] = useState<string[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [pagination, setPagination] = useState(EMPTY_PAGINATION);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | SubmittedCardStatus>('all');
  const [branchFilter, setBranchFilter] = useState('all');
  const [agentFilter, setAgentFilter] = useState('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);
  const [selectedCard, setSelectedCard] = useState<SubmittedCardRecord | null>(null);
  const [updatingCardId, setUpdatingCardId] = useState<string | null>(null);
  const [selectedCardIds, setSelectedCardIds] = useState<string[]>([]);
  const [bulkStatus, setBulkStatus] = useState<SubmittedCardStatus | ''>('');
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const [deductionJobs, setDeductionJobs] = useState<DeductionJob[]>([]);
  const [imageFailed, setImageFailed] = useState(false);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({
        page: String(page),
        perPage: '20',
      });
      if (searchQuery.trim()) params.set('search', searchQuery.trim());
      if (statusFilter !== 'all') params.set('status', statusFilter);
      if (branchFilter !== 'all') params.set('branch', branchFilter);
      if (agentFilter !== 'all') params.set('agent', agentFilter);
      if (dateFrom) params.set('dateFrom', dateFrom);
      if (dateTo) params.set('dateTo', dateTo);

      const response = await fetch(`/api/inventory/submitted-cards/bootstrap?${params.toString()}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const data = await parseJsonResponse<BootstrapResponse>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to load submitted cards.');
      }
      setCards(Array.isArray(data.cards) ? data.cards : []);
      setSelectedCardIds((current) => {
        const nextIds = new Set((data.cards || []).map((card) => card.id));
        return current.filter((id) => nextIds.has(id));
      });
      setCounts(data.counts || EMPTY_COUNTS);
      setBranches(Array.isArray(data.branches) ? data.branches : []);
      setAgents(Array.isArray(data.agents) ? data.agents : []);
      setPagination(data.pagination || EMPTY_PAGINATION);
      if (data.pagination && data.pagination.page !== page) {
        setPage(data.pagination.page);
      }
      if (selectedCard) {
        const refreshed = (data.cards || []).find((card) => card.id === selectedCard.id) || null;
        setSelectedCard(refreshed);
      }
      if (onCountsChange) {
        await onCountsChange();
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load submitted cards.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [page, searchQuery, statusFilter, branchFilter, agentFilter, dateFrom, dateTo]);

  useEffect(() => {
    if (!selectedCard) return;
    const latest = cards.find((card) => card.id === selectedCard.id);
    if (latest && latest !== selectedCard) {
      setSelectedCard(latest);
    }
  }, [cards, selectedCard]);

  useEffect(() => {
    setImageFailed(false);
  }, [selectedCard?.id, selectedCard?.productImage]);

  const summaryCards = [
    { id: 'total', label: 'Total Submitted', value: counts.total, sub: `${counts.open} still open`, icon: Package },
    { id: 'open', label: 'Open Queue', value: counts.open, sub: `${counts.pending} waiting to start`, icon: Clock3 },
    { id: 'packaging', label: 'Packaging', value: counts.packaging, sub: 'Being prepared now', icon: Box },
    { id: 'delivering', label: 'Delivering', value: counts.delivering, sub: 'Out for customer handoff', icon: Truck },
    { id: 'delivered', label: 'Delivered', value: counts.delivered, sub: 'Completed handovers', icon: CheckCircle2 },
  ];

  const selectableCardIds = cards.filter((card) => card.status !== 'delivered').map((card) => card.id);
  const selectedCount = selectedCardIds.length;
  const allVisibleSelected = selectableCardIds.length > 0 && selectableCardIds.every((id) => selectedCardIds.includes(id));

  const toggleCardSelection = (cardId: string) => {
    setSelectedCardIds((current) =>
      current.includes(cardId) ? current.filter((id) => id !== cardId) : [...current, cardId],
    );
  };

  const toggleAllVisibleCards = () => {
    setSelectedCardIds((current) => {
      if (allVisibleSelected) {
        return current.filter((id) => !selectableCardIds.includes(id));
      }
      const merged = new Set([...current, ...selectableCardIds]);
      return Array.from(merged);
    });
  };

  const applyStatusUpdate = async (card: SubmittedCardRecord, nextStatus?: SubmittedCardStatus | null) => {
    if (!nextStatus) return;
    if (nextStatus === 'packaging') {
      setDeductionJobs([{id: card.id, label: `${card.customerName} - Packaging`, url: `/api/inventory/submitted-cards/${card.id}/status`, body: {status: 'packaging'}}]);
      return;
    }
    setUpdatingCardId(card.id);
    try {
      const response = await fetch(`/api/inventory/submitted-cards/${card.id}/status`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ status: nextStatus }),
      });
      const data = await parseJsonResponse<StatusResponse>(response);
      if (!response.ok || !data.ok || !data.card) {
        throw new Error(data.error || 'Unable to update submitted card status.');
      }
      if (selectedCard?.id === data.card.id) {
        setSelectedCard(data.card);
      }
      if (onCountsChange) {
        await onCountsChange();
      }
      await loadData();
      toast.success(`${data.card.customerName} moved to ${data.card.statusLabel}.`);
    } catch (updateError) {
      toast.error(updateError instanceof Error ? updateError.message : 'Unable to update submitted card status.');
    } finally {
      setUpdatingCardId(null);
    }
  };

  const applyBulkStatusUpdate = async () => {
    if (!bulkStatus) {
      toast.error('Choose the status to apply.');
      return;
    }
    if (!selectedCardIds.length) {
      toast.error('Select at least one submitted card.');
      return;
    }
    const label = {
      pending: 'Submitted',
      packaging: 'Packaging',
      delivering: 'Delivering',
      delivered: 'Delivered',
    }[bulkStatus];
    if (bulkStatus === 'packaging') {
      setDeductionJobs(selectedCardIds.map(id => ({id, label: cards.find(card => card.id === id)?.customerName || id, url: `/api/inventory/submitted-cards/${id}/status`, body: {status: 'packaging'}})));
      return;
    }
    if (!window.confirm(`Update ${selectedCardIds.length} selected card(s) to ${label}?`)) return;

    setBulkUpdating(true);
    try {
      const response = await fetch('/api/inventory/submitted-cards/bulk-status', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ cardIds: selectedCardIds, status: bulkStatus }),
      });
      const data = await parseJsonResponse<BulkStatusResponse>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to bulk update submitted cards.');
      }
      setSelectedCardIds([]);
      setBulkStatus('');
      if (onCountsChange) {
        await onCountsChange();
      }
      await loadData();
      toast.success(`Updated ${data.updated || 0} card(s) to ${data.statusLabel || label}.`);
      if (data.skipped) {
        toast.info(`Skipped ${data.skipped} card(s) already at that status or not eligible.`);
      }
    } catch (updateError) {
      toast.error(updateError instanceof Error ? updateError.message : 'Unable to bulk update submitted cards.');
    } finally {
      setBulkUpdating(false);
    }
  };

  const openCardDetail = (cardId: string) => {
    const latest = cards.find((card) => card.id === cardId);
    if (latest) {
      setSelectedCard(latest);
    }
  };

  const exportReport = async (format: 'pdf' | 'csv') => {
    try {
      const params = new URLSearchParams();
      if (searchQuery.trim()) params.set('search', searchQuery.trim());
      if (statusFilter !== 'all') params.set('status', statusFilter);
      if (branchFilter !== 'all') params.set('branch', branchFilter);
      if (agentFilter !== 'all') params.set('agent', agentFilter);
      if (dateFrom) params.set('dateFrom', dateFrom);
      if (dateTo) params.set('dateTo', dateTo);
      const response = await fetch(`/api/inventory/submitted-cards/export.${format}?${params.toString()}`, {
        credentials: 'same-origin',
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text.trim().slice(0, 180) || `Export failed with HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      const extension = format === 'pdf' ? 'pdf' : 'csv';
      link.href = url;
      link.download = `submitted-cards.${extension}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      toast.success(format === 'pdf' ? 'Submitted cards PDF exported.' : 'Submitted cards Excel export downloaded.');
    } catch (exportError) {
      toast.error(exportError instanceof Error ? exportError.message : 'Unable to export submitted cards.');
    }
  };

  return (
    <div className="space-y-6">
      {deductionJobs.length > 0 && <InventoryDeductionModal jobs={deductionJobs} onClose={() => setDeductionJobs([])} onFinished={() => { void loadData(); void onCountsChange?.(); setSelectedCardIds([]); setSelectedCard(null); }} />}

      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Submitted Cards</h1>
          <p className="text-gray-600 mt-1">Track fully paid customer cards after agent submission and move them from submitted to delivered.</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => void loadData()}
            className="flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Refresh</span>
          </button>
          <button
            onClick={() => void exportReport('pdf')}
            className="flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <Download className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Export PDF</span>
          </button>
          <button
            onClick={() => void exportReport('csv')}
            className="flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <Download className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Export Excel</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {summaryCards.map((card) => {
          const Icon = card.icon;
          const tone = summaryTone(card.id);
          return (
            <div key={card.id} className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-sm transition-shadow">
              <div className={`inline-flex p-2 rounded-lg ${tone.bg} mb-3`}>
                <Icon className={`w-4 h-4 ${tone.fg}`} />
              </div>
              <div className="text-xl font-semibold text-gray-900">{card.value}</div>
              <div className="text-xs text-gray-600 mt-1">{card.label}</div>
              <div className="text-xs text-gray-400 mt-0.5">{card.sub}</div>
            </div>
          );
        })}
      </div>

      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3 p-4 border-b border-gray-200">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(event) => {
                setPage(1);
                setSearchQuery(event.target.value);
              }}
              placeholder="Search customer, product, phone, or agent..."
              className="w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(event) => {
              setPage(1);
              setStatusFilter(event.target.value as 'all' | SubmittedCardStatus);
            }}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="all">All statuses</option>
            <option value="pending">Submitted</option>
            <option value="packaging">Packaging</option>
            <option value="delivering">Delivering</option>
            <option value="delivered">Delivered</option>
          </select>
          <select
            value={branchFilter}
            onChange={(event) => {
              setPage(1);
              setBranchFilter(event.target.value);
            }}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="all">All branches</option>
            {branches.map((branch) => (
              <option key={branch} value={branch}>
                {branch}
              </option>
            ))}
          </select>
          <select
            value={agentFilter}
            onChange={(event) => {
              setPage(1);
              setAgentFilter(event.target.value);
            }}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="all">All agents</option>
            {agents.map((agent) => (
              <option key={agent} value={agent}>
                {agent}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => {
              setPage(1);
              setDateFrom(event.target.value);
            }}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <input
            type="date"
            value={dateTo}
            onChange={(event) => {
              setPage(1);
              setDateTo(event.target.value);
            }}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>

        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 px-4 py-3 border-b border-gray-200 bg-slate-50/70">
          <button
            type="button"
            onClick={toggleAllVisibleCards}
            disabled={selectableCardIds.length === 0}
            className="inline-flex items-center gap-2 text-sm font-medium text-gray-700 disabled:opacity-50"
          >
            {allVisibleSelected ? <CheckSquare className="w-4 h-4 text-indigo-600" /> : <Square className="w-4 h-4 text-gray-400" />}
            Select all visible
            <span className="text-gray-400">({selectedCount} selected)</span>
          </button>
          <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
            <select
              value={bulkStatus}
              onChange={(event) => setBulkStatus(event.target.value as SubmittedCardStatus | '')}
              className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">Bulk update status</option>
              <option value="pending">Submitted</option>
              <option value="packaging">Packaging</option>
              <option value="delivering">Delivering</option>
              <option value="delivered">Delivered</option>
            </select>
            <button
              type="button"
              onClick={() => void applyBulkStatusUpdate()}
              disabled={bulkUpdating || selectedCount === 0 || !bulkStatus}
              className="inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-lg bg-indigo-600 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {bulkUpdating ? <LoaderCircle className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
              Bulk Update
            </button>
          </div>
        </div>

        {loading ? (
          <div className="p-10 flex items-center justify-center text-gray-500 gap-2">
            <LoaderCircle className="w-5 h-5 animate-spin" />
            <span>Loading submitted cards...</span>
          </div>
        ) : error ? (
          <div className="p-6 text-sm text-rose-600">{error}</div>
        ) : cards.length === 0 ? (
          <div className="p-10 text-center text-gray-500">
            <div className="text-base font-medium text-gray-700">No submitted cards found.</div>
            <div className="text-sm text-gray-500 mt-1">Once agents submit fully paid customer cards, they will appear here for inventory handling.</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3 w-12">
                    <button
                      type="button"
                      onClick={toggleAllVisibleCards}
                      disabled={selectableCardIds.length === 0}
                      className="inline-flex rounded text-gray-400 hover:text-indigo-600 disabled:opacity-40"
                      title="Select all visible"
                    >
                      {allVisibleSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
                    </button>
                  </th>
                  <th className="px-4 py-3">Customer</th>
                  <th className="px-4 py-3">Product</th>
                  <th className="px-4 py-3">Branch</th>
                  <th className="px-4 py-3">Submitted</th>
                  <th className="px-4 py-3">Waiting</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Paid</th>
                  <th className="px-4 py-3 text-right">Balance</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {cards.map((card) => (
                  <tr key={card.id} className="hover:bg-gray-50/70">
                    <td className="px-4 py-4 align-top">
                      {card.status !== 'delivered' ? (
                        <button
                          type="button"
                          onClick={() => toggleCardSelection(card.id)}
                          className="inline-flex rounded text-gray-400 hover:text-indigo-600"
                          title="Select submitted card"
                        >
                          {selectedCardIds.includes(card.id) ? <CheckSquare className="w-4 h-4 text-indigo-600" /> : <Square className="w-4 h-4" />}
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300">--</span>
                      )}
                    </td>
                    <td className="px-4 py-4 align-top">
                      <div className="font-medium text-gray-900">{card.customerName}</div>
                      <div className="text-sm text-gray-500">{card.customerPhone || 'No phone'}</div>
                      <div className="text-xs text-gray-400 mt-1">Agent: {card.agentName || '-'}</div>
                    </td>
                    <td className="px-4 py-4 align-top">
                      <div className="font-medium text-gray-900">{card.productName}</div>
                      <div className="text-sm text-gray-500">{card.purchaseType || 'Installment'} · Qty {card.quantity}</div>
                    </td>
                    <td className="px-4 py-4 align-top text-sm text-gray-700">{card.branch || '-'}</td>
                    <td className="px-4 py-4 align-top text-sm text-gray-700">{formatDate(card.submittedAt)}</td>
                    <td className="px-4 py-4 align-top">
                      <span className="inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                        {card.daysWaiting} day{card.daysWaiting === 1 ? '' : 's'}
                      </span>
                    </td>
                    <td className="px-4 py-4 align-top">
                      <span className={statusBadge(card.status)}>{card.statusLabel}</span>
                    </td>
                    <td className="px-4 py-4 align-top text-right text-sm text-gray-700">
                      <PriceMask value={card.amountPaid} />
                    </td>
                    <td className="px-4 py-4 align-top text-right text-sm text-gray-700">
                      <PriceMask value={card.amountLeft} />
                    </td>
                    <td className="px-4 py-4 align-top">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => openCardDetail(card.id)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
                        >
                          <Eye className="w-4 h-4" />
                          View
                        </button>
                        {card.nextStatus && (
                          <button
                            type="button"
                            onClick={() => void applyStatusUpdate(card, card.nextStatus)}
                            disabled={updatingCardId === card.id}
                            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white rounded-lg transition-colors disabled:opacity-60 ${statusButtonStyle(card.nextStatus)}`}
                          >
                            {updatingCardId === card.id ? <LoaderCircle className="w-4 h-4 animate-spin" /> : <Package className="w-4 h-4" />}
                            {card.nextStatusLabel}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 py-3 border-t border-gray-200 bg-gray-50/60">
              <div className="text-sm text-gray-600">
                Showing {(pagination.total === 0 ? 0 : (pagination.page - 1) * pagination.perPage + 1)}-
                {Math.min(pagination.page * pagination.perPage, pagination.total)} of {pagination.total}
              </div>
              <div className="flex items-center gap-2 justify-end">
                <button
                  type="button"
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={pagination.page <= 1}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Prev
                </button>
                <span className="text-sm text-gray-600">
                  Page {pagination.page} of {pagination.totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((current) => Math.min(pagination.totalPages, current + 1))}
                  disabled={pagination.page >= pagination.totalPages}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  Next
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {selectedCard && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-3xl max-h-[85vh] bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-200 flex-shrink-0">
              <div>
                <div className="flex items-center gap-3 flex-wrap">
                  <h2 className="text-xl font-semibold text-gray-900">{selectedCard.customerName}</h2>
                  <span className={statusBadge(selectedCard.status)}>{selectedCard.statusLabel}</span>
                </div>
                <p className="text-sm text-gray-500 mt-1">{selectedCard.productName} · Qty {selectedCard.quantity} · Submitted {formatDateTime(selectedCard.submittedAt)}</p>
              </div>
              <button onClick={() => setSelectedCard(null)} className="p-2 rounded-lg hover:bg-gray-100 text-gray-500">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[220px,1fr] gap-0 overflow-y-auto min-h-0">
              <div className="border-b lg:border-b-0 lg:border-r border-gray-200 p-5 bg-gray-50/60">
                <div className="aspect-[4/3] rounded-xl bg-gray-100 overflow-hidden flex items-center justify-center mb-4">
                  {selectedCard.productImage && !imageFailed ? (
                    <img
                      key={selectedCard.id}
                      src={selectedCard.productImage}
                      alt={selectedCard.productName}
                      className="w-full h-full object-cover"
                      onError={(event) => {
                        event.currentTarget.style.display = 'none';
                        setImageFailed(true);
                      }}
                    />
                  ) : (
                    <div className="flex flex-col items-center justify-center gap-2 text-gray-400">
                      <Package className="w-10 h-10 text-gray-300" />
                      <span className="text-xs">No product image</span>
                    </div>
                  )}
                </div>

                <div className="space-y-3 text-sm text-gray-600">
                  <div className="flex items-center gap-2">
                    <User className="w-4 h-4 text-gray-400" />
                    <span>{selectedCard.customerPhone || 'No phone number'}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Package className="w-4 h-4 text-gray-400" />
                    <span>{selectedCard.purchaseType || 'Installment'}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Clock3 className="w-4 h-4 text-gray-400" />
                    <span>{selectedCard.daysWaiting} day{selectedCard.daysWaiting === 1 ? '' : 's'} in queue</span>
                  </div>
                </div>

                {selectedCard.nextStatus && (
                  <button
                    type="button"
                    onClick={() => void applyStatusUpdate(selectedCard, selectedCard.nextStatus)}
                    disabled={updatingCardId === selectedCard.id}
                    className={`mt-5 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium text-white rounded-lg transition-colors disabled:opacity-60 ${statusButtonStyle(selectedCard.nextStatus)}`}
                  >
                    {updatingCardId === selectedCard.id ? <LoaderCircle className="w-4 h-4 animate-spin" /> : <Truck className="w-4 h-4" />}
                    Move to {selectedCard.nextStatusLabel}
                  </button>
                )}
              </div>

              <div className="p-5 space-y-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <InfoCard label="Paid" value={<PriceMask value={selectedCard.amountPaid} />} />
                  <InfoCard label="Balance" value={<PriceMask value={selectedCard.amountLeft} />} />
                  <InfoCard label="Branch" value={selectedCard.branch || '-'} />
                  <InfoCard label="Handled By" value={selectedCard.agentName || '-'} />
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-gray-900 mb-3">Status History</h3>
                  <div className="space-y-3">
                    {selectedCard.history.length === 0 ? (
                      <div className="text-sm text-gray-500">No status events logged yet.</div>
                    ) : (
                      selectedCard.history.map((entry, index) => (
                        <div key={`${entry.status}-${entry.timestamp}-${index}`} className="flex gap-3">
                          <div className="mt-1 w-2.5 h-2.5 rounded-full bg-indigo-500 flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-sm font-medium text-gray-900">{entry.label}</span>
                              <span className="text-xs text-gray-400">{formatDateTime(entry.timestamp)}</span>
                            </div>
                            <div className="text-sm text-gray-600">{entry.actorName || 'System'}{entry.actorRole ? ` · ${entry.actorRole}` : ''}</div>
                            {entry.notes && <div className="text-sm text-gray-500 mt-1">{entry.notes}</div>}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function InfoCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/70 p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-2 text-sm font-semibold text-gray-900">{value}</div>
    </div>
  );
}
