import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Check,
  ChevronDown,
  Clock,
  ImageIcon,
  Package,
  RefreshCw,
  Search,
  Trash2,
  Truck,
  Warehouse,
} from 'lucide-react';
import { toast } from 'sonner';

type BranchRequestItem = {
  lineId: string;
  productId: string;
  name: string;
  sku: string;
  imageUrl: string;
  destinationLocationId: string;
  destinationLocationName: string;
  destinationLocationCode: string;
  destinationBranch: string;
  sourceOptions: {
    id: string;
    branch: string;
    name: string;
    code: string;
    label: string;
    availableQty: number;
    type: string;
  }[];
  requestedQty: number;
  deliveredQty: number;
  remainingQty: number;
  rejectedQty: number;
  status: string;
  notes: string;
  decisionNote: string;
};

type BranchRequest = {
  id: string;
  branch: string;
  requestedBy: string;
  managerId: string;
  requestDate: string;
  updatedAt: string;
  status: string;
  priority: string;
  reason: string;
  itemsCount: number;
  totalQuantity: number;
  requestedQuantity: number;
  items: BranchRequestItem[];
};

type InventoryProduct = {
  id: string;
  name: string;
  entries?: {
    branch: string;
    locationName: string;
    locationCode: string;
    quantity: number;
  }[];
};

type ProductsResponse = {
  ok: boolean;
  products?: InventoryProduct[];
  error?: string;
};

type WarehouseTab =
  | 'branch-requests'
  | 'postponed-items'
  | 'rejected-items'
  | 'transfers'
  | 'dispatches'
  | 'pending-confirmations'
  | 'discrepancies'
  | 'stock-distribution';

function formatDateTime(value: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function statusTone(status: string): string {
  const key = String(status || '').toLowerCase();
  if (key === 'closed') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (key === 'rejected' || key === 'cancelled') return 'bg-rose-50 text-rose-700 border-rose-200';
  if (key === 'partially_delivered') return 'bg-amber-50 text-amber-700 border-amber-200';
  if (key === 'approved') return 'bg-indigo-50 text-indigo-700 border-indigo-200';
  if (key === 'pending' || key === 'open') return 'bg-blue-50 text-blue-700 border-blue-200';
  return 'bg-gray-50 text-gray-700 border-gray-200';
}

function priorityTone(priority: string): string {
  const key = String(priority || '').toLowerCase();
  if (key === 'high') return 'bg-rose-50 text-rose-700 border-rose-200';
  if (key === 'medium') return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-blue-50 text-blue-700 border-blue-200';
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

type BranchStockSummary = {
  branch: string;
  locations: number;
  products: number;
  units: number;
};

export function WarehouseOperations() {
  const [activeTab, setActiveTab] = useState<WarehouseTab>('branch-requests');
  const [searchQuery, setSearchQuery] = useState('');
  const [branchRequests, setBranchRequests] = useState<BranchRequest[]>([]);
  const [products, setProducts] = useState<InventoryProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [approvingRequestId, setApprovingRequestId] = useState<string | null>(null);
  const [lineSourceSelections, setLineSourceSelections] = useState<Record<string, string>>({});
  const [lineApprovalQty, setLineApprovalQty] = useState<Record<string, number>>({});
  const [lineRemainingAction, setLineRemainingAction] = useState<Record<string, 'postponed' | 'rejected'>>({});
  const [lineDecisionNotes, setLineDecisionNotes] = useState<Record<string, string>>({});
  const [expandedRequestIds, setExpandedRequestIds] = useState<Set<string>>(new Set());

  const loadData = async () => {
    setLoading(true);
    try {
      const [requestsResponse, productsResponse] = await Promise.all([
        fetch('/api/inventory/branch-requests', { credentials: 'same-origin' }),
        fetch('/api/inventory/products', { credentials: 'same-origin' }),
      ]);
      const requestsPayload = await parseJsonResponse<{ ok: boolean; requests?: BranchRequest[]; error?: string }>(requestsResponse);
      const productsPayload = await parseJsonResponse<ProductsResponse>(productsResponse);
      if (!requestsResponse.ok || !requestsPayload.ok) {
        throw new Error(requestsPayload.error || 'Unable to load branch requests.');
      }
      if (!productsResponse.ok || !productsPayload.ok) {
        throw new Error(productsPayload.error || 'Unable to load stock distribution.');
      }
      const requests = Array.isArray(requestsPayload.requests) ? requestsPayload.requests : [];
      setBranchRequests(requests);
      setProducts(Array.isArray(productsPayload.products) ? productsPayload.products : []);
      setLineSourceSelections((current) => {
        const next = { ...current };
        for (const request of requests) {
          for (const item of request.items) {
            const key = `${request.id}:${item.lineId}`;
            if (!next[key] && item.sourceOptions.length === 1) {
              next[key] = item.sourceOptions[0].id;
            }
          }
        }
        return next;
      });
      setLineApprovalQty((current) => {
        const next = { ...current };
        for (const request of requests) {
          for (const item of request.items) {
            const key = `${request.id}:${item.lineId}`;
            if (!next[key]) {
              next[key] = Math.max(0, Number(item.remainingQty || 0));
            }
          }
        }
        return next;
      });
      setLineRemainingAction((current) => {
        const next = { ...current };
        for (const request of requests) {
          for (const item of request.items) {
            const key = `${request.id}:${item.lineId}`;
            if (!next[key]) {
              next[key] = String(item.status || '').toLowerCase() === 'rejected' ? 'rejected' : 'postponed';
            }
          }
        }
        return next;
      });
      setLineDecisionNotes((current) => {
        const next = { ...current };
        for (const request of requests) {
          for (const item of request.items) {
            const key = `${request.id}:${item.lineId}`;
            if (next[key] === undefined) {
              next[key] = item.decisionNote || '';
            }
          }
        }
        return next;
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load warehouse data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const pendingRequests = useMemo(
    () => branchRequests.filter((request) => ['pending', 'open'].includes(String(request.status || '').toLowerCase())),
    [branchRequests]
  );

  const transferRequests = useMemo(
    () => branchRequests.filter((request) => !['pending', 'open'].includes(String(request.status || '').toLowerCase())),
    [branchRequests]
  );

  const postponedRequests = useMemo(
    () =>
      branchRequests
        .map((request) => ({
          ...request,
          items: request.items.filter(
            (item) =>
              item.remainingQty > 0 &&
              (item.deliveredQty > 0 || String(item.status || '').toLowerCase() === 'postponed')
          ),
        }))
        .filter((request) => request.items.length > 0),
    [branchRequests]
  );

  const rejectedRequests = useMemo(
    () =>
      branchRequests
        .map((request) => ({
          ...request,
          items: request.items.filter(
            (item) => String(item.status || '').toLowerCase() === 'rejected' || Number(item.rejectedQty || 0) > 0
          ),
        }))
        .filter((request) => request.items.length > 0),
    [branchRequests]
  );

  const dispatchRows = useMemo(
    () => branchRequests.filter((request) => ['partially_delivered', 'closed', 'approved'].includes(String(request.status || '').toLowerCase())),
    [branchRequests]
  );

  const pendingConfirmations = useMemo(
    () =>
      branchRequests.filter(
        (request) =>
          request.items.some((item) => item.deliveredQty > 0 && item.remainingQty > 0) ||
          String(request.status || '').toLowerCase() === 'partially_delivered'
      ),
    [branchRequests]
  );

  const discrepancyRows = useMemo(
    () =>
      branchRequests.filter((request) =>
        request.items.some((item) => item.deliveredQty > 0 && item.remainingQty > 0)
      ),
    [branchRequests]
  );

  const stockDistribution = useMemo<BranchStockSummary[]>(() => {
    const branchMap = new Map<string, { units: number; products: Set<string>; locations: Set<string> }>();
    for (const product of products) {
      for (const entry of product.entries || []) {
        const branch = String(entry.branch || '').trim();
        if (!branch) continue;
        const current = branchMap.get(branch) || { units: 0, products: new Set<string>(), locations: new Set<string>() };
        current.units += Number(entry.quantity || 0);
        current.products.add(product.id);
        current.locations.add(`${entry.locationName || ''}:${entry.locationCode || ''}`);
        branchMap.set(branch, current);
      }
    }
    return Array.from(branchMap.entries())
      .map(([branch, info]) => ({
        branch,
        units: info.units,
        products: info.products.size,
        locations: info.locations.size,
      }))
      .sort((a, b) => b.units - a.units || a.branch.localeCompare(b.branch));
  }, [products]);

  const filteredBranchRequests = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return pendingRequests.filter((request) => {
      if (!query) return true;
      return (
        request.id.toLowerCase().includes(query) ||
        request.branch.toLowerCase().includes(query) ||
        request.requestedBy.toLowerCase().includes(query) ||
        request.reason.toLowerCase().includes(query)
      );
    });
  }, [pendingRequests, searchQuery]);

  const filteredTransfers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return transferRequests.filter((request) => {
      if (!query) return true;
      return (
        request.id.toLowerCase().includes(query) ||
        request.branch.toLowerCase().includes(query) ||
        request.requestedBy.toLowerCase().includes(query)
      );
    });
  }, [searchQuery, transferRequests]);

  const filteredPostponedRequests = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return postponedRequests.filter((request) => {
      if (!query) return true;
      return (
        request.id.toLowerCase().includes(query) ||
        request.branch.toLowerCase().includes(query) ||
        request.requestedBy.toLowerCase().includes(query) ||
        request.items.some((item) => item.name.toLowerCase().includes(query) || String(item.sku || '').toLowerCase().includes(query))
      );
    });
  }, [postponedRequests, searchQuery]);

  const filteredRejectedRequests = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return rejectedRequests.filter((request) => {
      if (!query) return true;
      return (
        request.id.toLowerCase().includes(query) ||
        request.branch.toLowerCase().includes(query) ||
        request.requestedBy.toLowerCase().includes(query) ||
        request.items.some((item) => item.name.toLowerCase().includes(query) || String(item.sku || '').toLowerCase().includes(query))
      );
    });
  }, [rejectedRequests, searchQuery]);

  const tabs = [
    { id: 'branch-requests', label: 'Branch Requests', count: pendingRequests.length },
    { id: 'postponed-items', label: 'Postponed Items', count: postponedRequests.reduce((sum, request) => sum + request.items.length, 0) },
    { id: 'rejected-items', label: 'Rejected Items', count: rejectedRequests.reduce((sum, request) => sum + request.items.length, 0) },
    { id: 'transfers', label: 'Transfers', count: transferRequests.length },
    { id: 'dispatches', label: 'Dispatches', count: dispatchRows.length },
    { id: 'pending-confirmations', label: 'Pending Confirmations', count: pendingConfirmations.length },
    { id: 'discrepancies', label: 'Discrepancies', count: discrepancyRows.length },
    { id: 'stock-distribution', label: 'Stock Distribution', count: stockDistribution.length },
  ] as const;

  const toggleRequestDetails = (requestId: string) => {
    setExpandedRequestIds((current) => {
      const next = new Set(current);
      if (next.has(requestId)) {
        next.delete(requestId);
      } else {
        next.add(requestId);
      }
      return next;
    });
  };

  const approveBranchRequest = async (requestId: string, lineIds?: string[]) => {
    const request = branchRequests.find((row) => row.id === requestId);
    if (!request) return;
    const allowedLineIds = lineIds && lineIds.length ? new Set(lineIds) : null;

    const lineSources: Record<string, string> = {};
    const lineApprovals: Record<string, { sourceLocationId: string; approvedQty: number; remainingAction: 'postponed' | 'rejected'; note: string }> = {};
    for (const item of request.items) {
      if (allowedLineIds && !allowedLineIds.has(item.lineId)) continue;
      if (item.remainingQty <= 0) continue;
      if (!Array.isArray(item.sourceOptions) || item.sourceOptions.length === 0) {
        toast.error('No source warehouse available', {
          description: `${item.name} does not currently have a source location with enough stock.`,
        });
        return;
      }
      const key = `${request.id}:${item.lineId}`;
      const selected = lineSourceSelections[key];
      if (!selected) {
        toast.error('Source warehouse required', {
          description: `Select a source warehouse for ${item.name} before approval.`,
        });
        return;
      }
      const approvedQty = Math.max(0, Math.floor(Number(lineApprovalQty[key] || 0)));
      if (approvedQty <= 0) {
        toast.error('Approved quantity required', {
          description: `Enter a quantity to approve for ${item.name}.`,
        });
        return;
      }
      if (approvedQty > item.remainingQty) {
        toast.error('Approved quantity is too high', {
          description: `${item.name} has only ${item.remainingQty} unit(s) remaining.`,
        });
        return;
      }
      const selectedSource = item.sourceOptions.find((option) => option.id === selected);
      if (selectedSource && approvedQty > selectedSource.availableQty) {
        toast.error('Source warehouse stock is too low', {
          description: `${selectedSource.label} has ${selectedSource.availableQty} unit(s) available for ${item.name}.`,
        });
        return;
      }
      lineSources[item.lineId] = selected;
      lineApprovals[item.lineId] = {
        sourceLocationId: selected,
        approvedQty,
        remainingAction: lineRemainingAction[key] || 'postponed',
        note: lineDecisionNotes[key] || '',
      };
    }
    if (Object.keys(lineApprovals).length === 0) {
      toast.error('No quantities to approve', {
        description: 'Enter at least one approval quantity before saving.',
      });
      return;
    }

    setApprovingRequestId(requestId);
    try {
      const response = await fetch(`/api/inventory/branch-requests/${encodeURIComponent(requestId)}/approve`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ lineSources, lineApprovals }),
      });
      const payload = await parseJsonResponse<{ ok: boolean; error?: string; approvedCount?: number }>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to approve branch request.');
      }
      toast.success('Branch request fulfilled', {
        description: `${payload.approvedCount || 0} line item(s) were moved into the destination branch.`,
      });
      await loadData();
    } catch (error) {
      toast.error('Approval failed', {
        description: error instanceof Error ? error.message : 'Unable to approve branch request.',
      });
    } finally {
      setApprovingRequestId(null);
    }
  };

  const deleteRequest = async (requestId: string) => {
    if (!window.confirm('Delete this whole branch request? This cannot be undone.')) return;
    try {
      const response = await fetch(`/api/inventory/branch-requests/${encodeURIComponent(requestId)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await parseJsonResponse<{ ok: boolean; error?: string }>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to delete branch request.');
      }
      toast.success('Branch request deleted');
      await loadData();
    } catch (error) {
      toast.error('Delete failed', {
        description: error instanceof Error ? error.message : 'Unable to delete branch request.',
      });
    }
  };

  const deleteRequestLine = async (requestId: string, lineId: string, productName: string) => {
    if (!window.confirm(`Delete ${productName || 'this product'} from this request? This cannot be undone.`)) return;
    try {
      const response = await fetch(
        `/api/inventory/branch-requests/${encodeURIComponent(requestId)}/lines/${encodeURIComponent(lineId)}`,
        {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        }
      );
      const payload = await parseJsonResponse<{ ok: boolean; error?: string }>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to delete product from request.');
      }
      toast.success('Product removed from request');
      await loadData();
    } catch (error) {
      toast.error('Delete failed', {
        description: error instanceof Error ? error.message : 'Unable to delete product from request.',
      });
    }
  };

  const summaryCards = [
    { label: 'Open Requests', value: pendingRequests.length, icon: Clock, tone: 'bg-blue-50 text-blue-600' },
    { label: 'Postponed Items', value: postponedRequests.reduce((sum, request) => sum + request.items.length, 0), icon: AlertTriangle, tone: 'bg-amber-50 text-amber-600' },
    { label: 'Rejected Items', value: rejectedRequests.reduce((sum, request) => sum + request.items.length, 0), icon: Package, tone: 'bg-rose-50 text-rose-600' },
    { label: 'Transfers Completed', value: transferRequests.filter((request) => request.status === 'closed').length, icon: ArrowRight, tone: 'bg-emerald-50 text-emerald-600' },
  ];
  const activeApprovalRows =
    activeTab === 'rejected-items'
      ? filteredRejectedRequests
      : activeTab === 'postponed-items'
        ? filteredPostponedRequests
        : filteredBranchRequests;
  const approvalEmpty = activeTab === 'rejected-items'
    ? {
        title: 'No rejected items',
        description: 'Rejected balances will appear here after warehouse approval decisions.',
      }
    : activeTab === 'postponed-items'
    ? {
        title: 'No postponed items',
        description: 'Partial approvals will save the balance here for later delivery.',
      }
    : {
        title: 'No open branch requests',
        description: 'Manager requests from inventory products will appear here for warehouse approval.',
      };

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Warehouse Operations</h1>
          <p className="text-gray-600 mt-1">Track branch stock requests, warehouse movements, delivery confirmations, and branch stock distribution.</p>
        </div>
        <button
          onClick={() => void loadData()}
          className="inline-flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          <RefreshCw className="w-4 h-4 text-gray-600" />
          <span className="text-sm font-medium text-gray-700">Refresh</span>
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {summaryCards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="bg-white rounded-xl border border-gray-200 p-4">
              <div className={`inline-flex p-2 rounded-lg ${card.tone} mb-3`}>
                <Icon className="w-4 h-4" />
              </div>
              <div className="text-xl font-semibold text-gray-900">{card.value}</div>
              <div className="text-xs text-gray-600 mt-1">{card.label}</div>
            </div>
          );
        })}
      </div>

      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex items-center gap-1 px-2 pt-2 border-b border-gray-200 overflow-x-auto">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors whitespace-nowrap border-b-2 -mb-px ${
                activeTab === tab.id ? 'text-indigo-600 border-indigo-600 bg-indigo-50/40' : 'text-gray-600 border-transparent hover:text-gray-900 hover:bg-gray-50'
              }`}
            >
              <span>{tab.label}</span>
              <span className={`px-1.5 py-0.5 rounded-md text-xs ${activeTab === tab.id ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                {tab.count}
              </span>
            </button>
          ))}
        </div>

        <div className="p-4 border-b border-gray-200">
          <div className="relative max-w-lg">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search request, branch, manager..."
              className="w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        <div className="p-4">
          {loading ? (
            <div className="rounded-lg border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              Loading warehouse workflows...
            </div>
          ) : (
            <>
              {(activeTab === 'branch-requests' || activeTab === 'postponed-items' || activeTab === 'rejected-items') && (
                <div className="space-y-4">
                  {activeApprovalRows.length === 0 ? (
                    <EmptyState icon={Package} title={approvalEmpty.title} description={approvalEmpty.description} />
                  ) : (
                    activeApprovalRows.map((request) => {
                      const expanded = expandedRequestIds.has(request.id);
                      const totalNeed = request.requestedQuantity || request.totalQuantity || request.items.reduce((sum, item) => sum + Number(item.requestedQty || 0), 0);
                      return (
                      <div key={request.id} className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                          <div className="min-w-0 p-4 lg:p-5">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-semibold text-gray-900">{request.id}</h3>
                              <span className={`inline-flex px-2 py-1 rounded border text-xs font-medium ${statusTone(request.status)}`}>{request.status}</span>
                              <span className={`inline-flex px-2 py-1 rounded border text-xs font-medium ${priorityTone(request.priority)}`}>{request.priority}</span>
                            </div>
                            <div className="text-sm text-gray-700 mt-1">{request.branch}</div>
                            <div className="text-xs text-gray-500 mt-1">Requested by {request.requestedBy} · {formatDateTime(request.requestDate)}</div>
                            <div className="text-xs text-gray-500 mt-1">{request.reason || 'No note added.'}</div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              <span className="inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">{request.itemsCount || request.items.length} items requested</span>
                              <span className="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">Total need: {totalNeed} units</span>
                            </div>
                          </div>
                          <div className="flex items-center gap-2 px-4 pb-4 lg:p-5 lg:pl-0">
                          {activeTab !== 'rejected-items' && (
                            <button
                              onClick={() => void approveBranchRequest(request.id, request.items.map((item) => item.lineId))}
                              disabled={approvingRequestId === request.id}
                              className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
                            >
                              <Check className="w-4 h-4" />
                              <span className="text-sm font-medium">
                                {approvingRequestId === request.id ? 'Approving...' : activeTab === 'postponed-items' ? 'Mark Delivered' : 'Approve Transfer'}
                              </span>
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void deleteRequest(request.id)}
                            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-600 transition-colors hover:bg-rose-50"
                            title="Delete whole request"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => toggleRequestDetails(request.id)}
                            aria-expanded={expanded}
                            aria-controls={`request-details-${request.id}`}
                            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-600 transition-colors hover:bg-gray-50"
                            title={expanded ? 'Hide requested products' : 'Show requested products'}
                          >
                            <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                          </button>
                          </div>
                        </div>

                        <div
                          id={`request-details-${request.id}`}
                          className={`grid transition-[grid-template-rows] duration-200 ease-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}
                        >
                          <div className="min-h-0 overflow-hidden">
                        <div className="space-y-3 border-t border-gray-200 bg-gray-50/70 p-4 lg:p-5">
                          {request.items.map((item) => {
                            const key = `${request.id}:${item.lineId}`;
                            const selectedSource = item.sourceOptions.find((option) => option.id === lineSourceSelections[key]);
                            const availableQty = selectedSource?.availableQty ?? item.sourceOptions[0]?.availableQty;
                            const approvedQty = lineApprovalQty[key] ?? item.remainingQty;
                            const remainingAfterApproval = Math.max(0, item.remainingQty - approvedQty);
                            const selectedAction = lineRemainingAction[key] || 'postponed';
                            return (
                              <div key={item.lineId} className="rounded-lg border border-gray-200 bg-white p-4">
                                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                  <div className="flex items-center gap-3">
                                    {item.imageUrl ? (
                                      <img src={item.imageUrl} alt={item.name} className="h-14 w-14 rounded-lg border border-gray-200 object-cover" />
                                    ) : (
                                      <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-gray-200 bg-gray-100 text-gray-400">
                                        <ImageIcon className="h-5 w-5" />
                                      </div>
                                    )}
                                    <div>
                                    <div className="font-medium text-gray-900">{item.name}</div>
                                    <div className="text-xs text-gray-500 mt-1">{item.sku || 'No SKU'} · Need {item.requestedQty} · Delivered {item.deliveredQty}</div>
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <div className="text-sm text-gray-700">{item.destinationBranch || request.branch}</div>
                                    <button
                                      type="button"
                                      onClick={() => void deleteRequestLine(request.id, item.lineId, item.name)}
                                      className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-rose-200 text-rose-600 transition-colors hover:bg-rose-50"
                                      title="Delete product from request"
                                    >
                                      <Trash2 className="h-4 w-4" />
                                    </button>
                                  </div>
                                </div>
                                <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-[1fr,150px,260px,220px] lg:items-start">
                                  <div className="text-xs text-gray-500">
                                    Destination: {item.destinationLocationName || 'Branch warehouse'} {item.destinationLocationCode ? `(${item.destinationLocationCode})` : ''}
                                    {item.decisionNote && (
                                      <div className="mt-2 rounded-lg bg-gray-50 p-2 text-gray-600">Note: {item.decisionNote}</div>
                                    )}
                                  </div>
                                  {activeTab !== 'rejected-items' ? (
                                    <>
                                  <div>
                                    <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Approve qty</label>
                                    <input
                                      type="number"
                                      min={1}
                                      max={Math.min(item.remainingQty, availableQty ?? item.remainingQty)}
                                      value={approvedQty}
                                      onChange={(event) => {
                                        const raw = Math.floor(Number(event.target.value || 0));
                                        const maxQty = Math.min(item.remainingQty, availableQty ?? item.remainingQty);
                                        const nextQty = Math.max(0, Math.min(raw, maxQty));
                                        setLineApprovalQty((current) => ({ ...current, [key]: nextQty }));
                                      }}
                                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                    />
                                    {remainingAfterApproval > 0 && (
                                      <div className={`mt-1 text-xs ${selectedAction === 'rejected' ? 'text-rose-600' : 'text-amber-600'}`}>
                                        {remainingAfterApproval} will be {selectedAction === 'rejected' ? 'rejected' : 'postponed'}
                                      </div>
                                    )}
                                  </div>
                                  <select
                                    value={lineSourceSelections[key] || ''}
                                    onChange={(event) => {
                                      const sourceId = event.target.value;
                                      const nextSource = item.sourceOptions.find((option) => option.id === sourceId);
                                      setLineSourceSelections((current) => ({ ...current, [key]: sourceId }));
                                      setLineApprovalQty((current) => {
                                        const maxQty = Math.min(item.remainingQty, nextSource?.availableQty ?? item.remainingQty);
                                        const currentQty = current[key] ?? item.remainingQty;
                                        return { ...current, [key]: Math.max(0, Math.min(currentQty, maxQty)) };
                                      });
                                    }}
                                    className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                  >
                                    <option value="">Select source warehouse...</option>
                                    {item.sourceOptions.map((option) => (
                                      <option key={option.id} value={option.id}>
                                        {option.branch} · {option.label} · {option.availableQty} available
                                      </option>
                                    ))}
                                  </select>
                                  {availableQty !== undefined && (
                                    <div className="text-xs text-gray-500">Available: {availableQty}</div>
                                  )}
                                  <div className="space-y-2">
                                    {remainingAfterApproval > 0 && (
                                      <div className="rounded-lg border border-gray-200 bg-gray-50 p-2">
                                        <label className="flex items-center gap-2 text-xs text-gray-700">
                                          <input
                                            type="radio"
                                            name={`remaining-${key}`}
                                            checked={selectedAction === 'postponed'}
                                            onChange={() => setLineRemainingAction((current) => ({ ...current, [key]: 'postponed' }))}
                                          />
                                          Postpone remaining
                                        </label>
                                        <label className="mt-1 flex items-center gap-2 text-xs text-gray-700">
                                          <input
                                            type="radio"
                                            name={`remaining-${key}`}
                                            checked={selectedAction === 'rejected'}
                                            onChange={() => setLineRemainingAction((current) => ({ ...current, [key]: 'rejected' }))}
                                          />
                                          Reject remaining
                                        </label>
                                      </div>
                                    )}
                                    <input
                                      type="text"
                                      value={lineDecisionNotes[key] || ''}
                                      onChange={(event) => setLineDecisionNotes((current) => ({ ...current, [key]: event.target.value }))}
                                      placeholder="Optional note for manager"
                                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                    />
                                  </div>
                                    </>
                                  ) : (
                                    <div className="rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700 lg:col-span-3">
                                      Rejected: {item.rejectedQty || Math.max(0, item.requestedQty - item.deliveredQty)} unit(s)
                                    </div>
                                  )}
                                </div>
                                {activeTab !== 'rejected-items' && item.sourceOptions.length === 0 && (
                                  <div className="mt-2 text-xs text-red-600">No active source warehouse currently has enough stock for this line.</div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                          </div>
                        </div>
                      </div>
                    );
                    })
                  )}
                </div>
              )}

              {activeTab === 'transfers' && (
                <RequestTable
                  rows={filteredTransfers}
                  emptyTitle="No processed transfers"
                  emptyDescription="Approved or fulfilled branch requests will appear here once warehouse action starts."
                  mode="transfers"
                />
              )}

              {activeTab === 'dispatches' && (
                <RequestTable
                  rows={dispatchRows}
                  emptyTitle="No dispatches tracked yet"
                  emptyDescription="Completed and partially fulfilled branch movements will appear here."
                  mode="dispatches"
                />
              )}

              {activeTab === 'pending-confirmations' && (
                <RequestTable
                  rows={pendingConfirmations}
                  emptyTitle="No pending confirmations"
                  emptyDescription="Requests with partial fulfillment or remaining quantities will appear here for follow-up."
                  mode="pending"
                />
              )}

              {activeTab === 'discrepancies' && (
                <div className="space-y-4">
                  {discrepancyRows.length === 0 ? (
                    <EmptyState icon={AlertTriangle} title="No discrepancies" description="Partial or short branch deliveries will appear here." />
                  ) : (
                    discrepancyRows.map((request) => (
                      <div key={request.id} className="rounded-xl border border-amber-200 bg-amber-50/60 p-5">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="font-semibold text-gray-900">{request.id}</h3>
                          <span className={`inline-flex px-2 py-1 rounded border text-xs font-medium ${statusTone(request.status)}`}>{request.status}</span>
                        </div>
                        <div className="text-sm text-gray-700 mt-1">{request.branch} · {request.requestedBy}</div>
                        <div className="mt-3 space-y-2">
                          {request.items.filter((item) => item.deliveredQty > 0 && item.remainingQty > 0).map((item) => (
                            <div key={item.lineId} className="rounded-lg border border-amber-200 bg-white p-3 text-sm text-gray-700">
                              <div className="font-medium text-gray-900">{item.name}</div>
                              <div className="text-xs text-gray-500 mt-1">Requested {item.requestedQty} · Delivered {item.deliveredQty} · Remaining {item.remainingQty}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {activeTab === 'stock-distribution' && (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                        <th className="px-4 py-3">Branch</th>
                        <th className="px-4 py-3">Locations</th>
                        <th className="px-4 py-3">Products</th>
                        <th className="px-4 py-3">Stock Units</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 bg-white">
                      {stockDistribution.map((row) => (
                        <tr key={row.branch} className="hover:bg-gray-50/60">
                          <td className="px-4 py-3 font-medium text-gray-900">{row.branch}</td>
                          <td className="px-4 py-3 text-sm text-gray-700">{row.locations}</td>
                          <td className="px-4 py-3 text-sm text-gray-700">{row.products}</td>
                          <td className="px-4 py-3 text-sm text-gray-700">{row.units.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {stockDistribution.length === 0 && (
                    <div className="pt-6">
                      <EmptyState icon={Warehouse} title="No branch stock distribution yet" description="Inventory branch stock positions will appear once products are stocked into locations." />
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RequestTable({
  rows,
  emptyTitle,
  emptyDescription,
  mode,
}: {
  rows: BranchRequest[];
  emptyTitle: string;
  emptyDescription: string;
  mode: 'transfers' | 'dispatches' | 'pending';
}) {
  if (rows.length === 0) {
    return <EmptyState icon={Truck} title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
            <th className="px-4 py-3">Request</th>
            <th className="px-4 py-3">Branch</th>
            <th className="px-4 py-3">Requested By</th>
            <th className="px-4 py-3">Requested Qty</th>
            <th className="px-4 py-3">Remaining</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">{mode === 'dispatches' ? 'Updated' : 'Created'}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {rows.map((request) => (
            <tr key={request.id} className="hover:bg-gray-50/60">
              <td className="px-4 py-3">
                <div className="font-medium text-gray-900">{request.id}</div>
                <div className="text-xs text-gray-500 mt-1">{request.items.length} line(s)</div>
              </td>
              <td className="px-4 py-3 text-sm text-gray-700">{request.branch}</td>
              <td className="px-4 py-3 text-sm text-gray-700">{request.requestedBy}</td>
              <td className="px-4 py-3 text-sm text-gray-700">{request.requestedQuantity}</td>
              <td className="px-4 py-3 text-sm text-gray-700">{request.totalQuantity}</td>
              <td className="px-4 py-3">
                <span className={`inline-flex px-2 py-1 rounded border text-xs font-medium ${statusTone(request.status)}`}>{request.status}</span>
              </td>
              <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(mode === 'dispatches' ? request.updatedAt : request.requestDate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Package;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center">
      <Icon className="mx-auto mb-3 h-8 w-8 text-gray-300" />
      <p className="text-sm font-medium text-gray-700">{title}</p>
      <p className="mt-1 text-sm text-gray-500">{description}</p>
    </div>
  );
}
