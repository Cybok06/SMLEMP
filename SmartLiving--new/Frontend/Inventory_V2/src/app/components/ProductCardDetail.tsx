import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Boxes,
  Building2,
  CalendarDays,
  CheckCircle2,
  Edit3,
  History,
  Layers3,
  LoaderCircle,
  Package,
  Save,
  Search,
  ShoppingBag,
  Users,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { PriceMask } from './PriceGuard';
import { ImageWithFallback } from './figma/ImageWithFallback';

export type ProductCardManager = {
  id: string;
  name: string;
  branch: string;
  price: number;
  cashPrice: number;
  paymentRate: number;
  costPrice: number;
  productDocumentId: string;
  updatedAt: string;
};

export type ProductCardComponent = {
  id: string;
  key: string;
  name: string;
  description: string;
  imageUrl: string;
  unitPrice: number;
  quantity: number;
  availableQty: number;
  sourceCollection: string;
};

export type ProductCardCustomer = {
  id: string;
  customerId: string;
  name: string;
  phone: string;
  location: string;
  branch: string;
  dateRegistered: string;
  purchaseDate: string;
  amountPaid: number;
  completion: number;
};

export type ProductCardRecord = {
  id: string;
  name: string;
  description: string;
  image: string;
  price: number;
  cashPrice: number;
  paymentRate: number;
  costPrice: number;
  profitMarginPrice: number;
  itemsCount: number;
  componentTypes: number;
  customers: number;
  completion70: number;
  completion80: number;
  completion90: number;
  stockReady: number;
  status: string;
  profitability: string;
  productType: string;
  category: string;
  packageName: string;
  defaultTermMonths: number;
  managerCount: number;
  branchCount: number;
  branches: string[];
  managers: ProductCardManager[];
  sourceProductIds: string[];
  components: ProductCardComponent[];
  customerRows: ProductCardCustomer[];
  totalSalesValue: number;
  purchaseCount: number;
  lastPurchaseDate: string;
  createdAt: string;
  updatedAt: string;
  changeHistory: Array<{
    id: string;
    changedAt: string;
    changedBy: { name?: string; username?: string; role?: string };
    changes: Array<{ field: string; label: string; before: unknown; after: unknown }>;
  }>;
};

type InventoryItemOption = {
  key: string;
  id?: string;
  name: string;
  price: number;
  description: string;
  imageUrl: string;
  availableQty?: number;
  category?: string;
  brand?: string;
};

interface ProductCardDetailProps {
  card: ProductCardRecord;
  inventoryItems: InventoryItemOption[];
  onBack: () => void;
  onCardUpdated: (card: ProductCardRecord) => void;
  onEdit: () => void;
}

type DetailTab = 'overview' | 'components' | 'managers' | 'customers';

function statusBadgeClass(stockReady: number): string {
  if (stockReady >= 85) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (stockReady >= 70) return 'text-amber-700 bg-amber-50 border-amber-200';
  return 'text-rose-700 bg-rose-50 border-rose-200';
}

function profitabilityBadgeClass(value: string): string {
  if (value === 'high') return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (value === 'medium') return 'text-blue-700 bg-blue-50 border-blue-200';
  return 'text-slate-700 bg-slate-100 border-slate-200';
}

function componentStockClass(component: ProductCardComponent): string {
  if (component.availableQty >= component.quantity) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (component.availableQty > 0) return 'text-amber-700 bg-amber-50 border-amber-200';
  return 'text-rose-700 bg-rose-50 border-rose-200';
}

export function ProductCardDetail({ card, inventoryItems, onBack, onCardUpdated, onEdit }: ProductCardDetailProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('overview');
  const [editingComponents, setEditingComponents] = useState(false);
  const [componentSearch, setComponentSearch] = useState('');
  const [customerSearch, setCustomerSearch] = useState('');
  const [draftComponents, setDraftComponents] = useState<Record<string, number>>({});
  const [savingComponents, setSavingComponents] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const displayHistoryValue = (value: unknown) => {
    if (value === null || value === undefined || value === '') return 'Not set';
    if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return String(value);
  };

  useEffect(() => {
    setDraftComponents(
      Object.fromEntries(
        (card.components || [])
          .filter((component) => component.key)
          .map((component) => [component.key, component.quantity])
      )
    );
    setEditingComponents(false);
    setComponentSearch('');
    setCustomerSearch('');
  }, [card]);

  const summary = useMemo(
    () => [
      {
        label: 'Installment Price',
        value: <PriceMask value={card.price} className="text-2xl font-semibold text-slate-900" />,
        icon: ShoppingBag,
        tone: 'bg-indigo-50 border-indigo-200 text-indigo-700',
      },
      {
        label: 'Customers',
        value: <span className="text-2xl font-semibold text-slate-900">{card.customers}</span>,
        icon: Users,
        tone: 'bg-sky-50 border-sky-200 text-sky-700',
      },
      {
        label: 'Stock Ready',
        value: <span className="text-2xl font-semibold text-slate-900">{card.stockReady}%</span>,
        icon: CheckCircle2,
        tone: 'bg-emerald-50 border-emerald-200 text-emerald-700',
      },
      {
        label: 'Managers',
        value: <span className="text-2xl font-semibold text-slate-900">{card.managerCount}</span>,
        icon: Building2,
        tone: 'bg-violet-50 border-violet-200 text-violet-700',
      },
    ],
    [card]
  );

  const filteredInventoryItems = useMemo(
    () =>
      inventoryItems.filter((item) =>
        [item.name, item.description, item.category, item.brand].join(' ').toLowerCase().includes(componentSearch.toLowerCase())
      ),
    [componentSearch, inventoryItems]
  );

  const draftUnitCount = useMemo(
    () => Object.values(draftComponents).reduce((sum, quantity) => sum + quantity, 0),
    [draftComponents]
  );

  const tabs: { id: DetailTab; label: string; icon: typeof Package }[] = [
    { id: 'overview', label: 'Overview', icon: Layers3 },
    { id: 'components', label: 'Items Inside Card', icon: Boxes },
    { id: 'managers', label: 'Managers & Branches', icon: Building2 },
    { id: 'customers', label: 'Customers', icon: Users },
  ];

  const filteredCustomers = useMemo(
    () =>
      (card.customerRows || []).filter((customer) =>
        [customer.name, customer.phone, customer.location, customer.branch]
          .join(' ')
          .toLowerCase()
          .includes(customerSearch.toLowerCase())
      ),
    [card.customerRows, customerSearch]
  );

  const adjustDraftQuantity = (key: string, delta: number) => {
    setDraftComponents((current) => {
      const next = { ...current };
      const nextValue = (next[key] || 0) + delta;
      if (nextValue <= 0) delete next[key];
      else next[key] = nextValue;
      return next;
    });
  };

  const saveComponentChanges = async () => {
    const components = Object.entries(draftComponents)
      .filter(([, quantity]) => quantity > 0)
      .map(([key, quantity]) => ({ key, quantity }));

    if (components.length === 0) {
      toast.error('Select at least one inventory product');
      return;
    }

    setSavingComponents(true);
    try {
      const response = await fetch(`/api/inventory/product-cards/${encodeURIComponent(card.id)}/components`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ components }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok || !payload.card) {
        throw new Error(payload.error || 'Failed to update items inside the card.');
      }
      onCardUpdated(payload.card);
      setEditingComponents(false);
      toast.success('Items on card updated', {
        description: `Updated ${payload.updatedCount || 0} manager copy/copies and ${payload.updatedPackages || 0} undelivered card(s). Settle any remaining components in Stock Deduction before delivery.`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to update items inside the card.';
      toast.error('Update failed', { description: message });
    } finally {
      setSavingComponents(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-4">
          <button
            onClick={onBack}
            className="mt-1 inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex items-start gap-4">
            <div className="h-24 w-24 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
              {card.image ? (
                <ImageWithFallback src={card.image} alt={card.name} className="h-full w-full object-contain p-2" />
              ) : (
                <div className="flex h-full w-full items-center justify-center">
                  <Package className="h-8 w-8 text-slate-300" />
                </div>
              )}
            </div>
            <div className="space-y-2">
              <div>
                <h1 className="text-2xl font-semibold text-slate-900">{card.name}</h1>
                <p className="mt-1 max-w-3xl text-sm text-slate-600">
                  {card.description || 'This product card does not have a description yet.'}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {card.productType && (
                  <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 font-medium text-indigo-700">
                    {card.productType}
                  </span>
                )}
                {card.category && (
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 font-medium text-slate-700">
                    {card.category}
                  </span>
                )}
                {card.packageName && (
                  <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 font-medium text-violet-700">
                    {card.packageName}
                  </span>
                )}
                <span className={`rounded-full border px-3 py-1 font-medium ${statusBadgeClass(card.stockReady)}`}>
                  {card.status === 'active' ? 'Healthy stock' : 'Needs attention'}
                </span>
                <span className={`rounded-full border px-3 py-1 font-medium capitalize ${profitabilityBadgeClass(card.profitability)}`}>
                  {card.profitability} margin
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-3 lg:w-[420px]">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              type="button"
              onClick={onEdit}
              className="inline-flex items-center justify-center gap-2 rounded-2xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-indigo-700"
            >
              <Edit3 className="h-4 w-4" /> Edit Product
            </button>
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
            >
              <History className="h-4 w-4" /> Change History
            </button>
          </div>
          <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
          {summary.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className={`rounded-2xl border p-4 ${item.tone}`}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-[0.16em]">{item.label}</span>
                  <Icon className="h-4 w-4" />
                </div>
                {item.value}
              </div>
            );
          })}
          </div>
        </div>
      </div>

      {historyOpen && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-slate-950/60 p-3 backdrop-blur-sm" onClick={() => setHistoryOpen(false)}>
          <div className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Change History</h2>
                <p className="mt-1 text-sm text-slate-500">{card.name} · newest changes first</p>
              </div>
              <button onClick={() => setHistoryOpen(false)} className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 text-slate-500 hover:bg-slate-50">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
              {(card.changeHistory || []).map((entry) => (
                <article key={entry.id} className="rounded-2xl border border-slate-200 p-4">
                  <div className="flex flex-col gap-1 border-b border-slate-100 pb-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="font-semibold text-slate-900">{entry.changedBy?.name || entry.changedBy?.username || 'Inventory User'}</div>
                    <div className="text-xs text-slate-500">
                      {entry.changedAt ? new Date(entry.changedAt).toLocaleString() : 'Time unavailable'}
                      {entry.changedBy?.role ? ` · ${entry.changedBy.role}` : ''}
                    </div>
                  </div>
                  <div className="mt-3 space-y-3">
                    {entry.changes.map((change, index) => (
                      <div key={`${change.field}-${index}`} className="grid gap-1 text-sm sm:grid-cols-[150px_1fr]">
                        <span className="font-medium text-slate-700">{change.label}</span>
                        <span className="break-words text-slate-600">
                          <span className="line-through decoration-rose-400">{displayHistoryValue(change.before)}</span>
                          <span className="mx-2 text-slate-400">→</span>
                          <span className="font-medium text-emerald-700">{displayHistoryValue(change.after)}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </article>
              ))}
              {(card.changeHistory || []).length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-300 px-5 py-12 text-center">
                  <History className="mx-auto h-8 w-8 text-slate-300" />
                  <p className="mt-3 text-sm font-medium text-slate-700">No recorded changes yet</p>
                  <p className="mt-1 text-xs text-slate-500">Edits made from now on will appear here with the editor and exact changes.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="rounded-3xl border border-slate-200 bg-white shadow-sm">
        <div className="flex overflow-x-auto border-b border-slate-200">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-6 py-4 text-sm font-medium transition ${
                  isActive ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-600 hover:text-slate-900'
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="p-6">
          {activeTab === 'overview' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Cash Price</div>
                  <div className="mt-2 text-xl font-semibold text-slate-900">
                    <PriceMask value={card.cashPrice} />
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Cost Price</div>
                  <div className="mt-2 text-xl font-semibold text-slate-900"><PriceMask value={card.costPrice} /></div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Sales Value</div>
                  <div className="mt-2 text-xl font-semibold text-slate-900">
                    <PriceMask value={card.totalSalesValue} />
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">70 / 80 / 90%</div>
                  <div className="mt-2 text-xl font-semibold text-slate-900">
                    {card.completion70} / {card.completion80} / {card.completion90}
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Items</div>
                  <div className="mt-2 text-xl font-semibold text-slate-900">
                    {card.itemsCount} units / {card.componentTypes} types
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.2fr_0.8fr]">
                <div className="rounded-2xl border border-slate-200 p-5">
                  <h3 className="text-base font-semibold text-slate-900">Commercial profile</h3>
                  <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Category</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">{card.category || '-'}</div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Package</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">{card.packageName || '-'}</div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Installment term</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">
                        {card.defaultTermMonths ? `${card.defaultTermMonths} months` : '-'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Payment rate</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">
                        {card.paymentRate > 0 ? <><PriceMask value={card.paymentRate} /> daily</> : '-'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Margin band</div>
                      <div className="mt-1 text-sm font-medium capitalize text-slate-900">{card.profitability}</div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Created</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">{card.createdAt || '-'}</div>
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Updated</div>
                      <div className="mt-1 text-sm font-medium text-slate-900">{card.updatedAt || '-'}</div>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 p-5">
                  <h3 className="text-base font-semibold text-slate-900">Operational footprint</h3>
                  <div className="mt-4 space-y-4">
                    <div className="flex items-start gap-3">
                      <Building2 className="mt-0.5 h-4 w-4 text-indigo-600" />
                      <div>
                        <div className="text-sm font-medium text-slate-900">{card.managerCount} manager copies</div>
                        <div className="text-xs text-slate-500">Saved across {card.branchCount} branches</div>
                      </div>
                    </div>
                    <div className="flex items-start gap-3">
                      <Layers3 className="mt-0.5 h-4 w-4 text-violet-600" />
                      <div>
                        <div className="text-sm font-medium text-slate-900">{card.componentTypes} component lines</div>
                        <div className="text-xs text-slate-500">{card.itemsCount} total units bundled inside the card</div>
                      </div>
                    </div>
                    <div className="flex items-start gap-3">
                      <CalendarDays className="mt-0.5 h-4 w-4 text-emerald-600" />
                      <div>
                        <div className="text-sm font-medium text-slate-900">{card.purchaseCount} purchase records linked</div>
                        <div className="text-xs text-slate-500">Latest purchase date: {card.lastPurchaseDate || '-'}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'components' && (
            <div className="space-y-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">Items inside card</h3>
                  <p className="text-sm text-slate-500">
                    This editor now uses the new `inventory_products` collection as the card item source.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {editingComponents && (
                    <button
                      onClick={() => {
                        setEditingComponents(false);
                        setDraftComponents(
                          Object.fromEntries((card.components || []).filter((component) => component.key).map((component) => [component.key, component.quantity]))
                        );
                      }}
                      className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                    >
                      <X className="h-4 w-4" />
                      Cancel
                    </button>
                  )}
                  <button
                    onClick={() => (editingComponents ? saveComponentChanges() : setEditingComponents(true))}
                    disabled={savingComponents}
                    className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-70"
                  >
                    {savingComponents ? <LoaderCircle className="h-4 w-4 animate-spin" /> : editingComponents ? <Save className="h-4 w-4" /> : <Edit3 className="h-4 w-4" />}
                    {editingComponents ? 'Save items' : 'Update items'}
                  </button>
                </div>
              </div>

              {editingComponents ? (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
                  <div className="rounded-2xl border border-slate-200 p-4">
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                      <input
                        value={componentSearch}
                        onChange={(event) => setComponentSearch(event.target.value)}
                        placeholder="Search inventory products..."
                        className="w-full rounded-2xl border border-slate-200 px-4 py-3 pl-10 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                      />
                    </div>
                    <div className="mt-4 max-h-[420px] space-y-3 overflow-y-auto pr-1">
                      {filteredInventoryItems.map((item) => {
                        const quantity = draftComponents[item.key] || 0;
                        return (
                          <div key={item.key} className={`flex items-center gap-3 rounded-2xl border p-3 ${quantity > 0 ? 'border-indigo-300 bg-indigo-50/50' : 'border-slate-200 bg-white'}`}>
                            <div className="h-14 w-14 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                              {item.imageUrl ? (
                                <ImageWithFallback src={item.imageUrl} alt={item.name} className="h-full w-full object-contain p-2" />
                              ) : (
                                <div className="flex h-full w-full items-center justify-center">
                                  <Package className="h-5 w-5 text-slate-300" />
                                </div>
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-3">
                                <div className="truncate text-sm font-semibold text-slate-900">{item.name}</div>
                                <div className="text-sm font-semibold text-slate-900">GHS {item.price.toFixed(2)}</div>
                              </div>
                              <div className="mt-1 text-xs text-slate-500">
                                {(item.availableQty || 0)} in stock {item.category ? `• ${item.category}` : ''}
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => adjustDraftQuantity(item.key, -1)}
                                disabled={quantity === 0}
                                className="inline-flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 text-slate-700 transition hover:bg-slate-50 disabled:opacity-40"
                              >
                                -
                              </button>
                              <div className="w-8 text-center text-sm font-semibold text-slate-900">{quantity}</div>
                              <button
                                type="button"
                                onClick={() => adjustDraftQuantity(item.key, 1)}
                                className="inline-flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 text-slate-700 transition hover:bg-slate-50"
                              >
                                +
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-200 p-5">
                    <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Draft summary</h4>
                    <div className="mt-4 grid grid-cols-2 gap-3">
                      <div className="rounded-2xl bg-slate-50 p-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Component types</div>
                        <div className="mt-2 text-xl font-semibold text-slate-900">
                          {Object.keys(draftComponents).filter((key) => draftComponents[key] > 0).length}
                        </div>
                      </div>
                      <div className="rounded-2xl bg-slate-50 p-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Units</div>
                        <div className="mt-2 text-xl font-semibold text-slate-900">{draftUnitCount}</div>
                      </div>
                    </div>
                    <div className="mt-4 space-y-3">
                      {Object.entries(draftComponents)
                        .filter(([, quantity]) => quantity > 0)
                        .map(([key, quantity]) => {
                          const item = inventoryItems.find((candidate) => candidate.key === key);
                          return (
                            <div key={key} className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                              <div>
                                <div className="text-sm font-medium text-slate-900">{item?.name || 'Inventory product'}</div>
                                <div className="text-xs text-slate-500">{item?.availableQty || 0} available</div>
                              </div>
                              <div className="text-sm font-semibold text-slate-900">x{quantity}</div>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3">
                  {card.components.map((component) => (
                    <div key={`${component.sourceCollection}-${component.id}-${component.name}`} className="rounded-2xl border border-slate-200 p-4">
                      <div className="flex flex-col gap-4 md:flex-row md:items-center">
                        <div className="flex items-center gap-4">
                          <div className="h-16 w-16 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                            {component.imageUrl ? (
                              <ImageWithFallback src={component.imageUrl} alt={component.name} className="h-full w-full object-contain p-2" />
                            ) : (
                              <div className="flex h-full w-full items-center justify-center">
                                <Package className="h-5 w-5 text-slate-300" />
                              </div>
                            )}
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-slate-900">{component.name}</div>
                            <div className="mt-1 text-xs text-slate-500">{component.description || 'No component description available.'}</div>
                            <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.14em] text-indigo-600">
                              {component.sourceCollection === 'inventory_products' ? 'Inventory Products' : 'Legacy Inventory'}
                            </div>
                          </div>
                        </div>

                        <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-4">
                          <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Required</div>
                            <div className="mt-1 text-lg font-semibold text-slate-900">{component.quantity}</div>
                          </div>
                          <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Available</div>
                            <div className="mt-1 text-lg font-semibold text-slate-900">{component.availableQty}</div>
                          </div>
                          <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Unit price</div>
                            <div className="mt-1 text-lg font-semibold text-slate-900">
                              <PriceMask value={component.unitPrice} />
                            </div>
                          </div>
                          <div className="flex items-center">
                            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${componentStockClass(component)}`}>
                              {component.availableQty >= component.quantity ? 'Stock ready' : component.availableQty > 0 ? 'Partial' : 'Out of stock'}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === 'managers' && (
            <div className="space-y-4">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">Managers and branches</h3>
                  <p className="text-sm text-slate-500">
                    Each manager keeps a separate product document in `products`, and this table shows the manager-level prices for the card.
                  </p>
                </div>
                <div className="text-sm text-slate-500">{card.managers.length} manager copy/copies</div>
              </div>

              <div className="overflow-hidden rounded-2xl border border-slate-200">
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-slate-200">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Manager</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Branch</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Installment Price</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Cash Price</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Cost Price</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Updated</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 bg-white">
                      {card.managers.map((manager) => (
                        <tr key={`${manager.id}-${manager.productDocumentId}`}>
                          <td className="px-4 py-4">
                            <div className="font-medium text-slate-900">{manager.name}</div>
                            <div className="mt-1 text-xs text-slate-500">{manager.productDocumentId}</div>
                          </td>
                          <td className="px-4 py-4 text-sm text-slate-700">{manager.branch || '-'}</td>
                          <td className="px-4 py-4 text-sm font-medium text-slate-900">
                            <PriceMask value={manager.price} />
                          </td>
                          <td className="px-4 py-4 text-sm font-medium text-slate-900">
                            <PriceMask value={manager.cashPrice} />
                          </td>
                          <td className="px-4 py-4 text-sm font-medium text-slate-900">
                            <PriceMask value={manager.costPrice} />
                          </td>
                          <td className="px-4 py-4 text-sm text-slate-700">{manager.updatedAt || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'customers' && (
            <div className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">Customers</h3>
                  <p className="text-sm text-slate-500">
                    Customers currently linked to this card across all manager copies.
                  </p>
                </div>
                <div className="text-sm text-slate-500">{filteredCustomers.length} customer record(s)</div>
              </div>

              <div className="relative max-w-md">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  value={customerSearch}
                  onChange={(event) => setCustomerSearch(event.target.value)}
                  placeholder="Search customer, phone, branch, or location..."
                  className="w-full rounded-2xl border border-slate-200 py-3 pl-10 pr-4 text-sm text-slate-700 outline-none transition focus:border-indigo-300 focus:ring-4 focus:ring-indigo-50"
                />
              </div>

              {filteredCustomers.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                  <Users className="mx-auto mb-3 h-8 w-8 text-slate-300" />
                  <p className="text-sm font-medium text-slate-700">No customer records found</p>
                  <p className="mt-1 text-sm text-slate-500">Try a different search or check whether this card has active purchases.</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-2xl border border-slate-200">
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200">
                      <thead className="bg-slate-50">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Customer</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Phone</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Branch</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Location</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Registered</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Amount Paid</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Completion</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 bg-white">
                        {filteredCustomers.map((customer) => (
                          <tr key={customer.id}>
                            <td className="px-4 py-4">
                              <div className="font-medium text-slate-900">{customer.name || 'Customer'}</div>
                              <div className="mt-1 text-xs text-slate-500">{customer.purchaseDate || '-'}</div>
                            </td>
                            <td className="px-4 py-4 text-sm text-slate-700">{customer.phone || '-'}</td>
                            <td className="px-4 py-4 text-sm text-slate-700">{customer.branch || '-'}</td>
                            <td className="px-4 py-4 text-sm text-slate-700">{customer.location || '-'}</td>
                            <td className="px-4 py-4 text-sm text-slate-700">{customer.dateRegistered || '-'}</td>
                            <td className="px-4 py-4 text-sm font-medium text-slate-900">
                              <PriceMask value={customer.amountPaid} />
                            </td>
                            <td className="px-4 py-4 text-sm text-slate-700">{customer.completion}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
