import { useEffect, useMemo, useState } from 'react';
import {
  CheckCircle,
  Clock,
  Eye,
  LoaderCircle,
  Package,
  RefreshCw,
  Search,
  Truck,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

type FulfillmentStatus = 'pending' | 'packaging' | 'delivering' | 'delivered';

type FulfillmentCard = {
  id: string;
  customerName: string;
  customerPhone: string;
  productName: string;
  productImage: string;
  quantity: number;
  branch: string;
  agentName: string;
  status: FulfillmentStatus;
  statusLabel: string;
  nextStatus: FulfillmentStatus | null;
  nextStatusLabel: string;
  submittedAt: string;
  updatedAt: string;
  daysWaiting: number;
  history: {
    status: string;
    label: string;
    actorName: string;
    actorRole: string;
    timestamp: string;
    notes: string;
  }[];
};

type BootstrapResponse = {
  ok: boolean;
  cards?: FulfillmentCard[];
  counts?: {
    total: number;
    open: number;
    pending: number;
    packaging: number;
    delivering: number;
    delivered: number;
  };
  error?: string;
};

const STATUS_TONE: Record<FulfillmentStatus, string> = {
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  packaging: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  delivering: 'bg-blue-50 text-blue-700 border-blue-200',
  delivered: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

function formatDateTime(value: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 180) || `HTTP ${response.status}`);
  }
}

export function FulfillmentDelivery() {
  const [cards, setCards] = useState<FulfillmentCard[]>([]);
  const [counts, setCounts] = useState({ total: 0, open: 0, pending: 0, packaging: 0, delivering: 0, delivered: 0 });
  const [activeTab, setActiveTab] = useState<'all' | FulfillmentStatus>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCard, setSelectedCard] = useState<FulfillmentCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/inventory/submitted-cards/bootstrap?page=1&perPage=500', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await parseJsonResponse<BootstrapResponse>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to load fulfillment workflow.');
      }
      setCards(Array.isArray(payload.cards) ? payload.cards : []);
      if (payload.counts) {
        setCounts(payload.counts);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load fulfillment workflow.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const filteredCards = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return cards.filter((card) => {
      const statusMatch = activeTab === 'all' || card.status === activeTab;
      const searchMatch =
        !query ||
        card.customerName.toLowerCase().includes(query) ||
        card.productName.toLowerCase().includes(query) ||
        card.branch.toLowerCase().includes(query) ||
        card.agentName.toLowerCase().includes(query);
      return statusMatch && searchMatch;
    });
  }, [activeTab, cards, searchQuery]);

  const tabs = [
    { id: 'all', label: 'All Orders' },
    { id: 'pending', label: 'Awaiting Packaging' },
    { id: 'packaging', label: 'Packaging' },
    { id: 'delivering', label: 'In Delivery' },
    { id: 'delivered', label: 'Delivered' },
  ] as const;

  const summaryCards = [
    { label: 'Open Queue', value: counts.open, icon: Clock, tone: 'bg-amber-50 text-amber-600' },
    { label: 'Packaging', value: counts.packaging, icon: Package, tone: 'bg-indigo-50 text-indigo-600' },
    { label: 'Delivering', value: counts.delivering, icon: Truck, tone: 'bg-blue-50 text-blue-600' },
    { label: 'Delivered', value: counts.delivered, icon: CheckCircle, tone: 'bg-emerald-50 text-emerald-600' },
  ];

  const updateCardStatus = async (card: FulfillmentCard) => {
    if (!card.nextStatus) return;
    setUpdatingId(card.id);
    try {
      const response = await fetch(`/api/inventory/submitted-cards/${encodeURIComponent(card.id)}/status`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ status: card.nextStatus }),
      });
      const payload = await parseJsonResponse<{ ok: boolean; card?: FulfillmentCard; error?: string }>(response);
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || 'Unable to update fulfillment status.');
      }
      toast.success(`${card.customerName} moved to ${card.nextStatusLabel}.`);
      await loadData();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to update fulfillment status.');
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Fulfillment & Delivery</h1>
          <p className="text-gray-600 mt-1">Track customer fulfillment from submitted packaging queue through delivery completion.</p>
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
              {tab.label}
              <span className={`px-1.5 py-0.5 rounded-md text-xs ${activeTab === tab.id ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                {tab.id === 'all' ? cards.length : cards.filter((card) => card.status === tab.id).length}
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
              placeholder="Search customer, product, branch, or agent..."
              className="w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        <div className="p-4">
          {loading ? (
            <div className="rounded-lg border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              Loading fulfillment queue...
            </div>
          ) : filteredCards.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                    <th className="px-4 py-3">Customer</th>
                    <th className="px-4 py-3">Product</th>
                    <th className="px-4 py-3">Branch</th>
                    <th className="px-4 py-3">Agent</th>
                    <th className="px-4 py-3">Waiting</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {filteredCards.map((card) => (
                    <tr key={card.id} className="hover:bg-gray-50/60">
                      <td className="px-4 py-4 align-top">
                        <div className="font-medium text-gray-900">{card.customerName}</div>
                        <div className="text-sm text-gray-500">{card.customerPhone || 'No phone'}</div>
                      </td>
                      <td className="px-4 py-4 align-top">
                        <div className="font-medium text-gray-900">{card.productName}</div>
                        <div className="text-xs text-gray-500 mt-1">Qty {card.quantity} · Submitted {formatDateTime(card.submittedAt)}</div>
                      </td>
                      <td className="px-4 py-4 align-top text-sm text-gray-700">{card.branch || '-'}</td>
                      <td className="px-4 py-4 align-top text-sm text-gray-700">{card.agentName || '-'}</td>
                      <td className="px-4 py-4 align-top text-sm text-gray-700">{card.daysWaiting} day{card.daysWaiting === 1 ? '' : 's'}</td>
                      <td className="px-4 py-4 align-top">
                        <span className={`inline-flex px-2.5 py-1 rounded-full border text-xs font-medium ${STATUS_TONE[card.status]}`}>{card.statusLabel}</span>
                      </td>
                      <td className="px-4 py-4 align-top">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setSelectedCard(card)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
                          >
                            <Eye className="w-4 h-4" />
                            View
                          </button>
                          {card.nextStatus && (
                            <button
                              type="button"
                              onClick={() => void updateCardStatus(card)}
                              disabled={updatingId === card.id}
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
                            >
                              {updatingId === card.id ? <LoaderCircle className="w-4 h-4 animate-spin" /> : <Truck className="w-4 h-4" />}
                              {card.nextStatusLabel}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {selectedCard && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl max-h-[85vh] bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-200">
              <div>
                <h2 className="text-xl font-semibold text-gray-900">{selectedCard.customerName}</h2>
                <p className="text-sm text-gray-500 mt-1">{selectedCard.productName} · {selectedCard.branch || '-'} · {selectedCard.statusLabel}</p>
              </div>
              <button onClick={() => setSelectedCard(null)} className="p-2 rounded-lg hover:bg-gray-100 text-gray-500">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 overflow-y-auto min-h-0 space-y-5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <InfoCard label="Agent" value={selectedCard.agentName || '-'} />
                <InfoCard label="Waiting" value={`${selectedCard.daysWaiting} day${selectedCard.daysWaiting === 1 ? '' : 's'}`} />
                <InfoCard label="Submitted" value={formatDateTime(selectedCard.submittedAt)} />
                <InfoCard label="Updated" value={formatDateTime(selectedCard.updatedAt)} />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-900 mb-3">Status History</h3>
                <div className="space-y-3">
                  {selectedCard.history.map((entry, index) => (
                    <div key={`${entry.timestamp}-${index}`} className="flex gap-3">
                      <div className="mt-1 h-2.5 w-2.5 rounded-full bg-indigo-500 flex-shrink-0" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-gray-900">{entry.label}</div>
                        <div className="text-xs text-gray-500 mt-1">{entry.actorName || 'System'} · {entry.actorRole || '-'} · {formatDateTime(entry.timestamp)}</div>
                        {entry.notes && <div className="text-sm text-gray-600 mt-1">{entry.notes}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/70 p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-2 text-sm font-semibold text-gray-900">{value}</div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center">
      <Truck className="mx-auto mb-3 h-8 w-8 text-gray-300" />
      <p className="text-sm font-medium text-gray-700">No fulfillment records found</p>
      <p className="mt-1 text-sm text-gray-500">Submitted customer cards will appear here as they move through packaging and delivery.</p>
    </div>
  );
}
