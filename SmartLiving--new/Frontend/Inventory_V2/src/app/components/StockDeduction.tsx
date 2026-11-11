import { PackagingStockLedger } from './PackagingStockLedger';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  Loader2,
  MapPin,
  PackageCheck,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

type LocationOption = { id: string; name: string; code: string; branch: string };
type ComponentRow = {
  inventoryProductId: string;
  name: string;
  sku: string;
  locationId: string;
  locationName: string;
  locations: LocationOption[];
  quantityPerCard: number;
  cardQuantity: number;
  requiredQuantity: number;
  deductedQuantity: number;
  remainingQuantity: number;
  componentStatus: string;
  availableQuantity: number;
  afterQuantity: number;
  shortage: number;
  unitCost: number | null;
  totalCost: number | null;
  affectedOrders?: number;
  orderIds?: string[];
};
type DeductionOrder = {
  id: string;
  packageReference: string;
  customerName: string;
  productIndex: number;
  productCard: string;
  cardQuantity: number;
  submittedAt: string;
  deliveryStatus: string;
  branch: string;
  locations: LocationOption[];
  locationId: string;
  locationName: string;
  componentLocationMappings: Record<string, string>;
  deductionStatus: string;
  eligibilityStatus: string;
  selectable: boolean;
  legacyFailedAttempt: boolean;
  recipeSource: string;
  recipeReviewRequired: boolean;
  componentCount: number;
  requiredUnits: number;
  totalCost: number;
  shortageUnits: number;
  exceptions: string[];
  components: ComponentRow[];
  deductionReference: string;
  deductionId: string;
  statusHistory: Array<Record<string, unknown>>;
};
type Summary = {
  totalSubmittedOrders: number;
  awaitingDeduction: number;
  readyToDeduct: number;
  alreadyDeducted: number;
  partiallyDeducted: number;
  distinctProducts: number;
  totalComponentUnits: number;
  readyCostValue: number;
  insufficientProducts: number;
  shortageUnits: number;
  exceptions: number;
};
type PreviewResponse = {
  ok: boolean;
  error?: string;
  summary?: Summary;
  orders?: DeductionOrder[];
  components?: ComponentRow[];
  branches?: string[];
  allReadyOrderIds?: string[];
  canConfirm?: boolean;
  pagination?: { page: number; perPage: number; total: number; pages: number };
};

const EMPTY_SUMMARY: Summary = {
  totalSubmittedOrders: 0, awaitingDeduction: 0, readyToDeduct: 0, alreadyDeducted: 0,
  partiallyDeducted: 0, distinctProducts: 0, totalComponentUnits: 0, readyCostValue: 0,
  insufficientProducts: 0, shortageUnits: 0, exceptions: 0,
};

function localDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function money(value: number | null | undefined): string {
  return value == null ? 'Cost unavailable' : `GHS ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function json<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try { return JSON.parse(raw) as T; } catch { throw new Error(raw.slice(0, 180) || `HTTP ${response.status}`); }
}

function tone(status: string): string {
  const value = status.toLowerCase();
  if (value.includes('deducted') && !value.includes('not deducted') && !value.includes('ready')) return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (value.includes('ready')) return 'border-blue-200 bg-blue-50 text-blue-700';
  if (value.includes('insufficient') || value.includes('failed') || value.includes('duplicate') || value.includes('blocked')) return 'border-rose-200 bg-rose-50 text-rose-700';
  return 'border-amber-200 bg-amber-50 text-amber-700';
}

export function StockDeduction() {
  const [legacy, setLegacy] = useState(false);
  return <div className="space-y-4"><button type="button" onClick={() => setLegacy(!legacy)} className="text-sm font-medium text-indigo-700">{legacy ? 'Back to Packaging deductions' : 'Legacy records, recipe review and exports'}</button>{legacy ? <LegacyStockDeduction /> : <PackagingStockLedger />}</div>;
}

function LegacyStockDeduction() {
  const now = new Date();
  const [fromDate, setFromDate] = useState(localDate(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [toDate, setToDate] = useState(localDate(now));
  const [appliedRange, setAppliedRange] = useState<{ from: string; to: string } | null>(null);
  const [orders, setOrders] = useState<DeductionOrder[]>([]);
  const [components, setComponents] = useState<ComponentRow[]>([]);
  const [summary, setSummary] = useState<Summary>(EMPTY_SUMMARY);
  const [branches, setBranches] = useState<string[]>([]);
  const [branch, setBranch] = useState('');
  const [deliveryStatus, setDeliveryStatus] = useState('');
  const [deductionStatus, setDeductionStatus] = useState('');
  const [search, setSearch] = useState('');
  const [locationMappings, setLocationMappings] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [canConfirm, setCanConfirm] = useState(false);
  const [activeView, setActiveView] = useState<'orders' | 'components' | 'undeducted'>('orders');
  const [detail, setDetail] = useState<DeductionOrder | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [expandedComponent, setExpandedComponent] = useState<string>('');
  const [expandedOrders, setExpandedOrders] = useState<Set<string>>(new Set());
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async (range = appliedRange, overrides?: { branch?: string; deliveryStatus?: string; deductionStatus?: string; locationMappings?: Record<string, string> }) => {
    if (!range) return;
    controller.current?.abort();
    controller.current = new AbortController();
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/audit/stock-deductions/preview', {
        method: 'POST',
        credentials: 'same-origin',
        signal: controller.current.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          fromDate: range.from,
          toDate: range.to,
          branch: overrides?.branch ?? branch,
          deliveryStatus: overrides?.deliveryStatus ?? deliveryStatus,
          deductionStatus: overrides?.deductionStatus ?? deductionStatus,
          customer: search,
          locationMappings: overrides?.locationMappings ?? locationMappings,
          page: 1,
          perPage: 100,
        }),
      });
      const payload = await json<PreviewResponse>(response);
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'Unable to load stock deductions.');
      setOrders(payload.orders || []);
      setComponents(payload.components || []);
      setSummary(payload.summary || EMPTY_SUMMARY);
      setBranches(payload.branches || []);
      setCanConfirm(Boolean(payload.canConfirm));
      setSelected((current) => new Set([...current].filter((id) => (payload.orders || []).some((row) => row.id === id && row.selectable))));
    } catch (loadError) {
      if ((loadError as Error).name === 'AbortError') return;
      setError(loadError instanceof Error ? loadError.message : 'Unable to load stock deductions.');
    } finally {
      setLoading(false);
    }
  }, [appliedRange, branch, deliveryStatus, deductionStatus, locationMappings, search]);

  useEffect(() => () => controller.current?.abort(), []);

  const applyRange = () => {
    if (!fromDate || !toDate) return toast.error('Choose both From Date and To Date.');
    if (fromDate > toDate) return toast.error('From Date cannot be later than To Date.');
    const range = { from: fromDate, to: toDate };
    setAppliedRange(range);
    setSelected(new Set());
    void load(range);
  };

  const reset = () => {
    setAppliedRange(null);
    setOrders([]);
    setComponents([]);
    setSummary(EMPTY_SUMMARY);
    setSelected(new Set());
    setBranch('');
    setDeliveryStatus('');
    setDeductionStatus('');
    setSearch('');
    setLocationMappings({});
    setError('');
  };

  const selectedOrders = useMemo(() => orders.filter((row) => selected.has(row.id)), [orders, selected]);
  const readyOrderIds = useMemo(() => orders.filter((row) => row.selectable).map((row) => row.id), [orders]);
  const allReadySelected = readyOrderIds.length > 0 && readyOrderIds.every((id) => selected.has(id));
  const toggleAllReady = () => {
    setSelected(allReadySelected ? new Set() : new Set(readyOrderIds));
  };
  const selection = useMemo(() => ({
    orders: selectedOrders.length,
    units: selectedOrders.reduce((sum, row) => sum + row.requiredUnits, 0),
    value: selectedOrders.reduce((sum, row) => sum + row.totalCost, 0),
    locations: [...new Set(selectedOrders.flatMap((row) =>
      row.components
        .filter((component) => component.locationName)
        .map((component) => `${row.branch}: ${component.locationName}`),
    ))],
  }), [selectedOrders]);

  const mapComponentLocation = (orderId: string, inventoryProductId: string, locationId: string) => {
    const next = { ...locationMappings, [`${orderId}:${inventoryProductId}`]: locationId };
    setLocationMappings(next);
    setSelected((current) => { const copy = new Set(current); copy.delete(orderId); return copy; });
    if (appliedRange) void load(appliedRange, { locationMappings: next });
  };

  const freezeRecipe = async (order: DeductionOrder) => {
    const reason = window.prompt('Why is the current product-card recipe appropriate for this historical package?');
    if (!reason?.trim()) return;
    const response = await fetch(`/api/inventory/audit/stock-deductions/${order.id}/freeze-recipe`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ reason: reason.trim() }),
    });
    const payload = await json<{ ok: boolean; error?: string }>(response);
    if (!response.ok || !payload.ok) return toast.error(payload.error || 'Unable to freeze recipe.');
    toast.success('Recipe reviewed and frozen.');
    setDetail(null);
    await load();
  };

  const confirm = async () => {
    if (!appliedRange || !selectedOrders.length) return;
    setConfirming(true);
    try {
      const response = await fetch('/api/inventory/audit/stock-deductions/confirm', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          fromDate: appliedRange.from, toDate: appliedRange.to,
          orders: selectedOrders.map((row) => ({
            packageId: row.id,
            componentLocations: row.componentLocationMappings,
          })),
        }),
      });
      const payload = await json<{ ok: boolean; error?: string; deducted?: number; partiallyDeducted?: number; existing?: number; blocked?: number }>(response);
      if (!payload.ok) throw new Error(payload.error || 'Stock deduction failed.');
      toast.success(`${payload.deducted || 0} fully deducted; ${payload.partiallyDeducted || 0} partially deducted; ${payload.existing || 0} already complete; ${payload.blocked || 0} blocked.`);
      setConfirmOpen(false);
      setSelected(new Set());
      await load();
    } catch (confirmError) {
      toast.error(confirmError instanceof Error ? confirmError.message : 'Stock deduction failed.');
    } finally {
      setConfirming(false);
    }
  };

  const exportReport = async (format: 'csv' | 'xlsx' | 'pdf') => {
    if (!appliedRange) return;
    const response = await fetch(`/api/inventory/audit/stock-deductions/export.${format}`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fromDate: appliedRange.from, toDate: appliedRange.to, branch, deliveryStatus, deductionStatus, locationMappings }),
    });
    if (!response.ok) {
      const payload = await json<{ error?: string }>(response);
      return toast.error(payload.error || 'Export failed.');
    }
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href; anchor.download = `stock-deductions-${appliedRange.from}-${appliedRange.to}.${format}`; anchor.click();
    URL.revokeObjectURL(href);
  };

  const cards = [
    ['Delivery records', summary.totalSubmittedOrders],
    ['Legacy pending', summary.awaitingDeduction],
    ['Legacy recoverable', summary.readyToDeduct],
    ['Delivered & deducted', summary.alreadyDeducted],
    ['Partially deducted', summary.partiallyDeducted || 0],
    ['Distinct products', summary.distinctProducts],
    ['Component units', summary.totalComponentUnits],
    ['Ready cost value', money(summary.readyCostValue)],
    ['Insufficient products', summary.insufficientProducts],
    ['Shortage units', summary.shortageUnits],
    ['Exceptions', summary.exceptions],
  ];

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          Stock is deducted when a card moves to Packaging. Use the Packaging deduction history to settle outstanding items. This view retains historical records, recipe review and exports.
        </div>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs font-semibold text-slate-600">From Date
              <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
            </label>
            <label className="text-xs font-semibold text-slate-600">To Date
              <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
            </label>
            <button type="button" onClick={applyRange} className="self-end rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">Apply</button>
            <button type="button" onClick={reset} className="self-end rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Clear / Reset</button>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={!appliedRange || loading} onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:opacity-50"><RefreshCw className="h-4 w-4" />Refresh</button>
            {(['csv', 'xlsx', 'pdf'] as const).map((format) => <button key={format} type="button" disabled={!appliedRange} onClick={() => void exportReport(format)} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm uppercase disabled:opacity-50"><Download className="h-4 w-4" />{format}</button>)}
          </div>
        </div>
      </div>

      {!appliedRange ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
          <PackageCheck className="mx-auto h-10 w-10 text-indigo-300" />
          <h3 className="mt-3 font-semibold text-slate-800">Choose an inclusive date range</h3>
          <p className="mt-1 text-sm text-slate-500">Orders are loaded server-side using their package submission timestamp.</p>
        </div>
      ) : loading && !orders.length ? (
        <div className="grid gap-3 md:grid-cols-3"><div className="h-28 animate-pulse rounded-xl bg-slate-100" /><div className="h-28 animate-pulse rounded-xl bg-slate-100" /><div className="h-28 animate-pulse rounded-xl bg-slate-100" /></div>
      ) : error ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-center text-rose-700">
          <AlertTriangle className="mx-auto mb-2 h-6 w-6" />{error}
          <button type="button" onClick={() => void load()} className="ml-3 rounded bg-rose-700 px-3 py-1.5 text-sm text-white">Retry</button>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {cards.map(([label, value]) => <div key={String(label)} className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xl font-bold text-slate-900">{value}</div><div className="mt-1 text-xs text-slate-500">{label}</div></div>)}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <div className="relative xl:col-span-2"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" /><input value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void load()} placeholder="Customer or package search" className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm" /></div>
              <select value={branch} onChange={(e) => { setBranch(e.target.value); void load(appliedRange, { branch: e.target.value }); }} className="rounded-lg border border-slate-300 px-3 py-2 text-sm"><option value="">All branches</option>{branches.map((value) => <option key={value}>{value}</option>)}</select>
              <select value={deliveryStatus} onChange={(e) => { setDeliveryStatus(e.target.value); void load(appliedRange, { deliveryStatus: e.target.value }); }} className="rounded-lg border border-slate-300 px-3 py-2 text-sm"><option value="">All delivery statuses</option>{['pending', 'packaging', 'delivering', 'delivered'].map((value) => <option key={value}>{value}</option>)}</select>
              <select value={deductionStatus} onChange={(e) => { setDeductionStatus(e.target.value); void load(appliedRange, { deductionStatus: e.target.value }); }} className="rounded-lg border border-slate-300 px-3 py-2 text-sm"><option value="">All deduction statuses</option>{['Ready to deduct', 'Insufficient stock', 'Already deducted', 'Location required', 'Recipe review required', 'Missing inventory link', 'Cost unavailable', 'Duplicate submission'].map((value) => <option key={value}>{value}</option>)}</select>
            </div>
          </div>

          <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="text-sm text-indigo-950"><strong>{selection.orders}</strong> selected · <strong>{selection.units}</strong> units · <strong>{money(selection.value)}</strong><div className="mt-1 text-xs text-indigo-700">{selection.locations.join(' · ') || 'No stock locations selected'}</div></div>
              <div className="flex flex-wrap gap-2">
                <button type="button" disabled={!readyOrderIds.length} onClick={toggleAllReady} className="rounded-lg border border-indigo-300 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">{allReadySelected ? 'Clear all selected' : `Select all deductible orders (${readyOrderIds.length})`}</button>
                <button type="button" onClick={() => setSelected(new Set())} className="rounded-lg border border-indigo-300 bg-white px-3 py-2 text-sm font-semibold text-indigo-700">Clear selection</button>
                <button type="button" disabled={!selection.orders || !canConfirm} onClick={() => setConfirmOpen(true)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Review legacy deduction</button>
              </div>
            </div>
            {!canConfirm && <p className="mt-2 text-xs text-indigo-700">View-only access: Audit approval permission is required to confirm deductions.</p>}
          </div>

          <div className="flex gap-2 border-b border-slate-200">
            <button type="button" onClick={() => setActiveView('orders')} className={`border-b-2 px-4 py-2 text-sm font-semibold ${activeView === 'orders' ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500'}`}>Orders</button>
            <button type="button" onClick={() => setActiveView('components')} className={`border-b-2 px-4 py-2 text-sm font-semibold ${activeView === 'components' ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500'}`}>Component summary</button>
            <button type="button" onClick={() => setActiveView('undeducted')} className={`border-b-2 px-4 py-2 text-sm font-semibold ${activeView === 'undeducted' ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500'}`}>Undeducted Items</button>
          </div>

          {activeView === 'orders' ? (
            <div className="overflow-x-auto rounded-xl border border-slate-200">
              <table className="min-w-[1500px] w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr><th className="px-3 py-3"><label className="flex items-center gap-2 whitespace-nowrap"><input type="checkbox" aria-label="Select all deductible orders" disabled={!readyOrderIds.length} checked={allReadySelected} onChange={toggleAllReady} />Select all</label></th>{['Deduction status', 'Customer / package', 'Product card', 'Qty', 'Submitted', 'Delivery', 'Branch', 'Stock location', 'Components', 'Units', 'Cost value', 'Exception', 'Batch', ''].map((label, index) => <th key={`${label}-${index}`} className="px-3 py-3">{label}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {orders.map((order) => (
                    <Fragment key={order.id}>
                    <tr className="align-top hover:bg-slate-50">
                      <td className="px-3 py-3"><div className="flex items-center gap-2"><button type="button" onClick={() => setExpandedOrders((current) => { const next = new Set(current); next.has(order.id) ? next.delete(order.id) : next.add(order.id); return next; })} aria-label={`Expand ${order.packageReference}`}>{expandedOrders.has(order.id) ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</button><input aria-label={`Select ${order.packageReference}`} type="checkbox" disabled={!order.selectable} checked={selected.has(order.id)} onChange={(e) => setSelected((current) => { const copy = new Set(current); e.target.checked ? copy.add(order.id) : copy.delete(order.id); return copy; })} /></div></td>
                      <td className="px-3 py-3"><span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${tone(order.deductionStatus)}`}>{order.deductionStatus}</span>{order.legacyFailedAttempt && <div className="mt-1 text-xs text-rose-600">Legacy attempt failed</div>}</td>
                      <td className="px-3 py-3"><div className="font-medium text-slate-900">{order.customerName}</div><div className="font-mono text-xs text-slate-500">{order.packageReference}</div></td>
                      <td className="px-3 py-3 font-medium text-slate-800">{order.productCard}</td><td className="px-3 py-3">{order.cardQuantity}</td>
                      <td className="px-3 py-3 whitespace-nowrap">{order.submittedAt ? new Date(order.submittedAt).toLocaleString() : 'Invalid date'}</td><td className="px-3 py-3 capitalize">{order.deliveryStatus}</td><td className="px-3 py-3">{order.branch || 'Missing'}</td>
                      <td className="px-3 py-3 text-xs">{order.locationName || 'Expand to select warehouses'}</td>
                      <td className="px-3 py-3">{order.componentCount}</td><td className="px-3 py-3">{order.requiredUnits}</td><td className="px-3 py-3 whitespace-nowrap">{money(order.totalCost)}</td>
                      <td className="max-w-60 px-3 py-3 text-xs text-rose-600">{order.exceptions[0] || '—'}</td><td className="px-3 py-3 font-mono text-xs">{order.deductionReference || '—'}</td>
                      <td className="px-3 py-3"><button type="button" onClick={() => setDetail(order)} className="rounded p-2 text-indigo-600 hover:bg-indigo-50" aria-label={`Review ${order.packageReference}`}><Eye className="h-4 w-4" /></button></td>
                    </tr>
                    {expandedOrders.has(order.id) && <tr className="bg-slate-50/80"><td colSpan={15} className="px-10 py-4">
                      <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Individual products and deduction warehouses — restricted to {order.branch || 'the customer branch'}</div>
                      <div className="grid gap-3 lg:grid-cols-2">{order.components.map((component) => <div key={component.inventoryProductId} className="rounded-lg border border-slate-200 bg-white p-4">
                        <div className="flex items-start justify-between gap-3"><div><div className="font-semibold text-slate-900">{component.name}</div><div className="font-mono text-xs text-slate-500">{component.sku}</div><div className={`mt-1 text-xs font-semibold ${component.remainingQuantity ? 'text-amber-700' : 'text-emerald-700'}`}>{component.componentStatus}</div></div><div className="text-right text-xs text-slate-600">Required <strong>{component.requiredQuantity}</strong><div>Remaining <strong>{component.remainingQuantity}</strong></div></div></div>
                        <label className="mt-3 block text-xs font-semibold text-slate-600">Warehouse
                          <select value={component.locationId || ''} onChange={(event) => mapComponentLocation(order.id, component.inventoryProductId, event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                            <option value="">Select warehouse</option>
                            {(component.locations || order.locations).map((location) => <option key={location.id} value={location.id}>{location.code ? `${location.code} — ${location.name}` : location.name}</option>)}
                          </select>
                        </label>
                        <div className="mt-2 text-xs text-slate-600">Available {component.availableQuantity} → after {component.afterQuantity} · shortage {component.shortage} · {money(component.totalCost)}</div>
                      </div>)}</div>
                    </td></tr>}
                    </Fragment>
                  ))}
                </tbody>
              </table>
              {!orders.length && <div className="p-12 text-center text-sm text-slate-500">No submitted packages match this range and filters.</div>}
            </div>
          ) : activeView === 'components' ? (
            <div className="overflow-x-auto rounded-xl border border-slate-200">
              <table className="min-w-[1100px] w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{['', 'Inventory product', 'SKU', 'Location', 'Required', 'Available', 'After', 'Shortage', 'Unit cost', 'Total cost', 'Orders'].map((value) => <th key={value} className="px-4 py-3">{value}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">{components.map((row) => { const key = `${row.inventoryProductId}:${row.locationId}`; return (
                  <>
                    <tr key={key}><td className="px-4 py-3"><button type="button" onClick={() => setExpandedComponent(expandedComponent === key ? '' : key)}>{expandedComponent === key ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</button></td><td className="px-4 py-3 font-medium">{row.name}</td><td className="px-4 py-3 font-mono text-xs">{row.sku}</td><td className="px-4 py-3">{row.locationId || 'Location required'}</td><td className="px-4 py-3">{row.requiredQuantity}</td><td className="px-4 py-3">{row.availableQuantity}</td><td className="px-4 py-3">{row.afterQuantity}</td><td className={`px-4 py-3 font-semibold ${row.shortage ? 'text-rose-600' : 'text-emerald-600'}`}>{row.shortage}</td><td className="px-4 py-3">{money(row.unitCost)}</td><td className="px-4 py-3">{money(row.totalCost)}</td><td className="px-4 py-3">{row.affectedOrders}</td></tr>
                    {expandedComponent === key && <tr key={`${key}:orders`}><td colSpan={11} className="bg-slate-50 px-12 py-3 text-xs text-slate-600">Contributing orders: {(row.orderIds || []).map((id) => orders.find((order) => order.id === id)?.packageReference || id).join(', ') || 'None on this page'}</td></tr>}
                  </>
                ); })}</tbody>
              </table>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200">
              <table className="min-w-[1100px] w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{['Customer / package', 'Product card', 'Inventory component', 'Branch', 'Warehouse', 'Required', 'Previously deducted', 'Remaining', 'Available', 'Reason'].map((value) => <th key={value} className="px-4 py-3">{value}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {orders.flatMap((order) => order.components.filter((component) => component.remainingQuantity > 0).map((component) => (
                    <tr key={`${order.id}:${component.inventoryProductId}`}>
                      <td className="px-4 py-3"><div className="font-medium">{order.customerName}</div><div className="font-mono text-xs text-slate-500">{order.packageReference}</div></td>
                      <td className="px-4 py-3">{order.productCard}</td><td className="px-4 py-3"><div className="font-medium">{component.name}</div><div className="font-mono text-xs text-slate-500">{component.sku}</div></td>
                      <td className="px-4 py-3">{order.branch}</td><td className="px-4 py-3">{component.locationName || 'Warehouse required'}</td>
                      <td className="px-4 py-3">{component.requiredQuantity}</td><td className="px-4 py-3">{component.deductedQuantity || 0}</td><td className="px-4 py-3 font-semibold">{component.remainingQuantity}</td><td className="px-4 py-3">{component.availableQuantity}</td><td className="px-4 py-3 text-amber-700">{component.componentStatus}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
              {!orders.some((order) => order.components.some((component) => component.remainingQuantity > 0)) && <div className="p-12 text-center text-sm text-slate-500">There are no undeducted component items in this range.</div>}
            </div>
          )}
        </>
      )}

      {detail && <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/40" role="dialog" aria-modal="true"><div className="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between"><div><h2 className="text-xl font-bold text-slate-900">{detail.packageReference}</h2><p className="text-sm text-slate-500">{detail.customerName} · purchase index {detail.productIndex}</p></div><button type="button" onClick={() => setDetail(null)} className="rounded p-2 hover:bg-slate-100"><X className="h-5 w-5" /></button></div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2"><Info label="Product card" value={detail.productCard} /><Info label="Recipe source" value={detail.recipeSource || 'Unavailable'} /><Info label="Customer agent branch" value={detail.branch || 'Missing'} /><Info label="Status" value={detail.deductionStatus} /></div>
        {detail.exceptions.length > 0 && <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-4"><div className="font-semibold text-amber-800">Exceptions</div><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-700">{detail.exceptions.map((value) => <li key={value}>{value}</li>)}</ul></div>}
        {detail.recipeReviewRequired && canConfirm && <button type="button" onClick={() => void freezeRecipe(detail)} className="mt-4 rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white">Approve & freeze reviewed recipe</button>}
        <h3 className="mt-6 font-semibold text-slate-900">Components and stock calculation</h3>
        <div className="mt-2 space-y-3">{detail.components.map((row) => <div key={row.inventoryProductId} className="rounded-lg border border-slate-200 p-4"><div className="flex justify-between"><div className="font-medium">{row.name}<div className="font-mono text-xs text-slate-500">{row.sku}</div></div><div className="text-right text-sm"><strong>{row.cardQuantity} × {row.quantityPerCard} = {row.requiredQuantity}</strong><div>{money(row.requiredQuantity && row.unitCost != null ? row.requiredQuantity * row.unitCost : null)}</div></div></div><div className="mt-3 text-xs text-slate-600">Current {row.availableQuantity} → after {row.afterQuantity} · shortage {row.shortage}</div></div>)}</div>
        <h3 className="mt-6 font-semibold text-slate-900">Package status history</h3><pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{JSON.stringify(detail.statusHistory, null, 2)}</pre>
      </div></div>}

      {confirmOpen && appliedRange && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4" role="dialog" aria-modal="true"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-center gap-3"><div className="rounded-full bg-indigo-100 p-3"><ShieldCheck className="h-6 w-6 text-indigo-700" /></div><div><h2 className="text-lg font-bold">Confirm Legacy Deduction</h2><p className="text-sm text-slate-500">Use only to recover packages submitted before automatic delivery deductions.</p></div></div>
        <dl className="mt-5 grid grid-cols-2 gap-3 text-sm"><Info label="Date range" value={`${appliedRange.from} to ${appliedRange.to}`} /><Info label="Selected orders" value={String(selection.orders)} /><Info label="Component units" value={String(selection.units)} /><Info label="Stock cost value" value={money(selection.value)} /></dl>
        <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">Locations: {selection.locations.join(' · ')}<br />Exception count: 0. Only complete ready orders are included.</div>
        <div className="mt-6 flex justify-end gap-3"><button type="button" disabled={confirming} onClick={() => setConfirmOpen(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold">Cancel</button><button type="button" disabled={confirming} onClick={() => void confirm()} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">{confirming ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}Confirm Legacy Deduction</button></div>
      </div></div>}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-slate-50 p-3"><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt><dd className="mt-1 text-sm font-medium text-slate-900">{value}</dd></div>;
}
