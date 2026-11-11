import type { FormEvent, ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Building2,
  Calendar,
  CheckCircle,
  ClipboardList,
  Clock,
  DollarSign,
  Download,
  Eye,
  FileText,
  Mail,
  MapPin,
  Package,
  Pencil,
  Plus,
  Search,
  ChevronDown,
  Send,
  ShieldAlert,
  Truck,
  User,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import type { Role } from './Settings';
import { AccessBanner, FinanceOnly, PriceCell, PriceHeader } from './PriceGuard';
import { ImageWithFallback } from './figma/ImageWithFallback';

type TabId =
  | 'suppliers'
  | 'procurement-requests'
  | 'purchase-orders'
  | 'supplier-deliveries'
  | 'pending-deliveries'
  | 'cost-updates';

type Supplier = {
  id: string;
  name: string;
  contact: string;
  phone: string;
  email: string;
  location: string;
  totalDeliveries: number;
  lastDelivery: string;
  totalSupplied: number;
  avgCostTrend: number;
  status: 'active' | 'inactive';
  notes?: string;
  recentDeliveries: { id: string; item: string; qty: number; date: string }[];
};

type InventoryProduct = {
  id: string;
  sku: string;
  name: string;
  category: string;
  brand: string;
  image: string;
  unitCost: number;
  available: number;
};

type ProductLine = {
  productId: string;
  product: string;
  sku: string;
  quantity: number;
  unitCost: number;
};

type ProcurementRequest = {
  id: string;
  requestNumber: string;
  supplierId: string;
  supplier: string;
  requestedBy: string;
  purpose: string;
  notes: string;
  status: 'pending' | 'approved' | 'rejected' | 'converted-to-po';
  createdAt: string;
  approvedBy?: string;
  rejectedBy?: string;
  purchaseOrderNumber?: string;
  items: ProductLine[];
};

type PurchaseOrderLine = {
  productId: string;
  product: string;
  sku: string;
  quantityOrdered: number;
  quantityReceived: number;
  quantityRejected: number;
  unitCost: number;
  lineTotal: number;
};

type PurchaseOrderReceipt = {
  receivedAt: string;
  receivedBy: string;
  deliveryNoteNo: string;
  locationName: string;
  locations?: { id: string; name: string; code: string; branch: string }[];
  comment: string;
  items: {
    product_id: string;
    product_name: string;
    sku: string;
    qty_delivered: number;
    qty_rejected: number;
    unit_cost: number;
    discrepancy_reason?: string;
    discrepancy_notes?: string;
    location_id?: string;
    location_name?: string;
    location_code?: string;
    branch?: string;
  }[];
};

type PurchaseOrder = {
  funding?: { type: string; label: string; amount: number; billId?: string; withdrawalId?: string };
  id: string;
  poNumber: string;
  supplierId: string;
  supplier: string;
  expectedDelivery: string;
  status: 'draft' | 'approved' | 'sent' | 'partial' | 'completed' | 'cancelled';
  trigger: string;
  notes: string;
  createdAt: string;
  createdBy: string;
  approvedBy?: string;
  sentBy?: string;
  receivedQty: number;
  itemsCount: number;
  totalQuantity: number;
  expectedCost: number;
  branch: string;
  locationId: string;
  procurementRequestNumber?: string;
  items: PurchaseOrderLine[];
  receipts: PurchaseOrderReceipt[];
};

type ReceiptLine = {
  productId: string;
  locationId: string;
  receivedQty: number;
  rejectedQty: number;
  discrepancyReason: string;
  discrepancyNotes: string;
};

type GRNRecord = {
  id: string;
  poId: string;
  linkedType: 'po' | 'pr';
  linkedRef: string;
  supplier: string;
  receivedBy: string;
  receivedDate: string;
  status: 'complete' | 'partial' | 'discrepancy';
  lineItems: {
    productId: string;
    product: string;
    sku: string;
    expectedQty: number;
    receivedQty: number;
    damagedQty: number;
    variance: number;
    unitCost: number;
    status: string;
  }[];
  auditTriggered: boolean;
  auditRef?: string | null;
  notes?: string;
  receipts: PurchaseOrderReceipt[];
  createdAt: string;
};

type PendingDelivery = {
  id: string;
  poNumber: string;
  supplier: string;
  itemsPending: number;
  expected: number;
  received: number;
  pending: number;
  expectedDate: string;
  delayDays: number;
  status: 'on-track' | 'partial' | 'delayed';
};

type CostUpdate = {
  id: string;
  updateNumber: string;
  productId: string;
  product: string;
  sku: string;
  supplierId: string;
  supplier: string;
  oldCost: number;
  newCost: number;
  reason: string;
  effectiveDate: string;
  changedBy: string;
  createdAt: string;
};

type Branch = {
  id: string;
  name: string;
  code: string;
};

type Location = {
  id: string;
  branchId: string;
  name: string;
  code: string;
  status: string;
};

type BootstrapResponse = {
  ok: boolean;
  supplierOptions: Supplier[];
  receivablePurchaseOrders: PurchaseOrder[];
  inventoryProducts: InventoryProduct[];
  branches: Branch[];
  locations: Record<string, Location[]>;
  counts: Record<TabId, number>;
  error?: string;
};

type TabPageResponse = { ok: boolean; rows: unknown[]; page: number; perPage: number; total: number; hasMore: boolean; error?: string };

interface SuppliersPurchasesProps {
  currentRole?: Role;
}

function fmt(value: string): string {
  return value
    .split('-')
    .map((piece) => piece.charAt(0).toUpperCase() + piece.slice(1))
    .join(' ');
}

function parseDate(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function statusBadge(status: string): string {
  const map: Record<string, string> = {
    active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    inactive: 'bg-gray-100 text-gray-600 border-gray-200',
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    approved: 'bg-blue-50 text-blue-700 border-blue-200',
    rejected: 'bg-rose-50 text-rose-700 border-rose-200',
    'converted-to-po': 'bg-violet-50 text-violet-700 border-violet-200',
    draft: 'bg-gray-100 text-gray-700 border-gray-200',
    sent: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    partial: 'bg-amber-50 text-amber-700 border-amber-200',
    complete: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    discrepancy: 'bg-rose-50 text-rose-700 border-rose-200',
    cancelled: 'bg-gray-100 text-gray-600 border-gray-200',
    'on-track': 'bg-blue-50 text-blue-700 border-blue-200',
    delayed: 'bg-rose-50 text-rose-700 border-rose-200',
  };
  return `inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${map[status] || 'bg-gray-50 text-gray-700 border-gray-200'}`;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(raw.trim().slice(0, 160) || `HTTP ${response.status}`);
  }
}

function todayValue(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysFromToday(offset: number): string {
  const base = new Date();
  base.setDate(base.getDate() + offset);
  return base.toISOString().slice(0, 10);
}

export function SuppliersPurchases({ currentRole: _currentRole }: SuppliersPurchasesProps) {
  const [activeTab, setActiveTab] = useState<TabId>('suppliers');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [procurementRequests, setProcurementRequests] = useState<ProcurementRequest[]>([]);
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [grnRecords, setGrnRecords] = useState<GRNRecord[]>([]);
  const [pendingDeliveries, setPendingDeliveries] = useState<PendingDelivery[]>([]);
  const [costUpdates, setCostUpdates] = useState<CostUpdate[]>([]);
  const [supplierOptions, setSupplierOptions] = useState<Supplier[]>([]);
  const [receivablePurchaseOrders, setReceivablePurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [tabCounts, setTabCounts] = useState<Record<TabId, number>>({suppliers:0,'procurement-requests':0,'purchase-orders':0,'supplier-deliveries':0,'pending-deliveries':0,'cost-updates':0});
  const [tabPage, setTabPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [inventoryProducts, setInventoryProducts] = useState<InventoryProduct[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [locations, setLocations] = useState<Record<string, Location[]>>({});

  const [selectedSupplierId, setSelectedSupplierId] = useState<string | null>(null);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [selectedPOId, setSelectedPOId] = useState<string | null>(null);
  const [selectedGRNId, setSelectedGRNId] = useState<string | null>(null);

  const [showSupplierModal, setShowSupplierModal] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [showPurchaseOrderModal, setShowPurchaseOrderModal] = useState(false);
  const [showCostUpdateModal, setShowCostUpdateModal] = useState(false);
  const [receiptPOId, setReceiptPOId] = useState<string | null>(null);
  const [convertRequestId, setConvertRequestId] = useState<string | null>(null);

  const setRowsForTab = (tab: TabId, rows: unknown[], append: boolean) => {
    if (tab === 'suppliers') setSuppliers((current) => append ? [...current, ...(rows as Supplier[])] : rows as Supplier[]);
    else if (tab === 'procurement-requests') setProcurementRequests((current) => append ? [...current, ...(rows as ProcurementRequest[])] : rows as ProcurementRequest[]);
    else if (tab === 'purchase-orders') setPurchaseOrders((current) => append ? [...current, ...(rows as PurchaseOrder[])] : rows as PurchaseOrder[]);
    else if (tab === 'supplier-deliveries') setGrnRecords((current) => append ? [...current, ...(rows as GRNRecord[])] : rows as GRNRecord[]);
    else if (tab === 'pending-deliveries') setPendingDeliveries((current) => append ? [...current, ...(rows as PendingDelivery[])] : rows as PendingDelivery[]);
    else setCostUpdates((current) => append ? [...current, ...(rows as CostUpdate[])] : rows as CostUpdate[]);
  };

  const loadTabData = async (reset = true, tab: TabId = activeTab) => {
    setLoading(true);
    setError('');
    try {
      const page = reset ? 1 : tabPage + 1;
      const params = new URLSearchParams({page:String(page), q:searchQuery.trim(), status:statusFilter});
      const response = await fetch(`/api/inventory/suppliers/tab/${tab}?${params.toString()}`, {
        credentials: 'same-origin',
      });
      const data = await parseJsonResponse<TabPageResponse>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to load supplier workflows.');
      }
      setRowsForTab(tab, Array.isArray(data.rows) ? data.rows : [], !reset);
      setTabPage(page);
      setHasMore(Boolean(data.hasMore));
      setTabCounts((current) => ({...current, [tab]:data.total || 0}));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load supplier workflows.');
    } finally {
      setLoading(false);
    }
  };

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/suppliers/bootstrap', {credentials:'same-origin'});
      const data = await parseJsonResponse<BootstrapResponse>(response);
      if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load supplier workflows.');
      setSupplierOptions(Array.isArray(data.supplierOptions) ? data.supplierOptions : []);
      setReceivablePurchaseOrders(Array.isArray(data.receivablePurchaseOrders) ? data.receivablePurchaseOrders : []);
      setInventoryProducts(Array.isArray(data.inventoryProducts) ? data.inventoryProducts : []);
      setBranches(Array.isArray(data.branches) ? data.branches : []);
      setLocations(data.locations || {});
      setTabCounts(data.counts || tabCounts);
      await loadTabData(true, activeTab);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load supplier workflows.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadTabData(true, activeTab); }, 300);
    return () => window.clearTimeout(timer);
  }, [activeTab, searchQuery, statusFilter]);

  const supplierMap = useMemo(() => Object.fromEntries(supplierOptions.map((supplier) => [supplier.id, supplier])), [supplierOptions]);

  const selectedSupplier = suppliers.find((supplier) => supplier.id === selectedSupplierId) || null;
  const selectedRequest = procurementRequests.find((request) => request.id === selectedRequestId) || null;
  const selectedPurchaseOrder = purchaseOrders.find((po) => po.id === selectedPOId) || null;
  const selectedGRN = grnRecords.find((grn) => grn.id === selectedGRNId) || null;
  const receiptPurchaseOrder = [...purchaseOrders, ...receivablePurchaseOrders].find((po) => po.id === receiptPOId) || null;
  const convertRequest = procurementRequests.find((request) => request.id === convertRequestId) || null;

  const search = searchQuery.trim().toLowerCase();

  const filteredSuppliers = useMemo(() => {
    return suppliers.filter((supplier) => {
      const statusMatch = statusFilter === 'all' || supplier.status === statusFilter;
      const searchMatch =
        !search ||
        supplier.name.toLowerCase().includes(search) ||
        supplier.contact.toLowerCase().includes(search) ||
        supplier.location.toLowerCase().includes(search) ||
        supplier.email.toLowerCase().includes(search) ||
        supplier.phone.toLowerCase().includes(search);
      return statusMatch && searchMatch;
    });
  }, [suppliers, search, statusFilter]);

  const filteredRequests = useMemo(() => {
    return procurementRequests.filter((request) => {
      const statusMatch = statusFilter === 'all' || request.status === statusFilter;
      const searchMatch =
        !search ||
        request.requestNumber.toLowerCase().includes(search) ||
        request.supplier.toLowerCase().includes(search) ||
        request.requestedBy.toLowerCase().includes(search) ||
        request.purpose.toLowerCase().includes(search);
      return statusMatch && searchMatch;
    });
  }, [procurementRequests, search, statusFilter]);

  const filteredPurchaseOrders = useMemo(() => {
    return purchaseOrders.filter((po) => {
      const statusMatch = statusFilter === 'all' || po.status === statusFilter;
      const searchMatch =
        !search ||
        po.poNumber.toLowerCase().includes(search) ||
        po.supplier.toLowerCase().includes(search) ||
        po.trigger.toLowerCase().includes(search);
      return statusMatch && searchMatch;
    });
  }, [purchaseOrders, search, statusFilter]);

  const filteredGRNs = useMemo(() => {
    return grnRecords.filter((grn) => {
      const statusMatch = statusFilter === 'all' || grn.status === statusFilter;
      const searchMatch =
        !search ||
        grn.id.toLowerCase().includes(search) ||
        grn.supplier.toLowerCase().includes(search) ||
        grn.linkedRef.toLowerCase().includes(search) ||
        grn.receivedBy.toLowerCase().includes(search);
      return statusMatch && searchMatch;
    });
  }, [grnRecords, search, statusFilter]);

  const filteredPendingDeliveries = useMemo(() => {
    return pendingDeliveries.filter((delivery) => {
      const statusMatch = statusFilter === 'all' || delivery.status === statusFilter;
      const searchMatch =
        !search ||
        delivery.poNumber.toLowerCase().includes(search) ||
        delivery.supplier.toLowerCase().includes(search);
      return statusMatch && searchMatch;
    });
  }, [pendingDeliveries, search, statusFilter]);

  const filteredCostUpdates = useMemo(() => {
    return costUpdates.filter((update) => {
      const searchMatch =
        !search ||
        update.updateNumber.toLowerCase().includes(search) ||
        update.product.toLowerCase().includes(search) ||
        update.supplier.toLowerCase().includes(search) ||
        update.reason.toLowerCase().includes(search);
      return searchMatch;
    });
  }, [costUpdates, search]);

  const totalReceivedThisMonth = grnRecords
    .filter((grn) => String(grn.receivedDate).startsWith(todayValue().slice(0, 7)))
    .reduce((sum, grn) => sum + grn.lineItems.reduce((lineSum, line) => lineSum + line.receivedQty, 0), 0);

  const totalSuppliedValue = suppliers.reduce((sum, supplier) => sum + supplier.totalSupplied, 0);

  const summaryCards = [
    { label: 'Total Suppliers', value: tabCounts.suppliers, sub: 'All supplier records', icon: Building2, tone: 'indigo' },
    { label: 'Requests', value: tabCounts['procurement-requests'], sub: 'All procurement requests', icon: ClipboardList, tone: 'blue' },
    { label: 'Purchase Orders', value: tabCounts['purchase-orders'], sub: `${tabCounts['pending-deliveries']} pending delivery`, icon: FileText, tone: 'amber' },
    { label: 'Goods Received', value: totalReceivedThisMonth.toLocaleString(), sub: 'Units this month', icon: Truck, tone: 'emerald' },
    { label: 'Loaded Supplier Value', value: `GHS ${totalSuppliedValue.toLocaleString()}`, sub: `${tabCounts['cost-updates']} cost updates logged`, icon: DollarSign, tone: 'violet' },
  ];

  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: 'suppliers', label: 'Suppliers', count: tabCounts.suppliers },
    { id: 'procurement-requests', label: 'Procurement Requests', count: tabCounts['procurement-requests'] },
    { id: 'purchase-orders', label: 'Purchase Orders', count: tabCounts['purchase-orders'] },
    { id: 'supplier-deliveries', label: 'Goods Receiving', count: tabCounts['supplier-deliveries'] },
    { id: 'pending-deliveries', label: 'Pending Deliveries', count: tabCounts['pending-deliveries'] },
    { id: 'cost-updates', label: 'Cost Updates', count: tabCounts['cost-updates'] },
  ];

  const toneMap: Record<string, { bg: string; fg: string }> = {
    indigo: { bg: 'bg-indigo-50', fg: 'text-indigo-600' },
    blue: { bg: 'bg-blue-50', fg: 'text-blue-600' },
    amber: { bg: 'bg-amber-50', fg: 'text-amber-600' },
    emerald: { bg: 'bg-emerald-50', fg: 'text-emerald-600' },
    violet: { bg: 'bg-violet-50', fg: 'text-violet-600' },
  };

  const submitAction = async (url: string, body: Record<string, unknown>, successMessage: string) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    const data = await parseJsonResponse<{ ok: boolean; error?: string }>(response);
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'Action failed.');
    }
    toast.success(successMessage);
    await loadData();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Suppliers & Purchases</h1>
          <p className="text-gray-600 mt-1">Record supplier requests, issue purchase orders, receive goods, and track cost changes in one flow.</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => void loadData()} className="flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
            <Clock className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Refresh</span>
          </button>
          {activeTab === 'suppliers' && (
            <PrimaryButton onClick={() => setShowSupplierModal(true)} icon={Plus} label="Add Supplier" />
          )}
          {activeTab === 'procurement-requests' && (
            <PrimaryButton onClick={() => setShowRequestModal(true)} icon={Plus} label="New Request" />
          )}
          {activeTab === 'purchase-orders' && (
            <PrimaryButton onClick={() => setShowPurchaseOrderModal(true)} icon={Plus} label="Create PO" />
          )}
          {activeTab === 'supplier-deliveries' && (
            <PrimaryButton onClick={() => setReceiptPOId(receivablePurchaseOrders[0]?.id || null)} icon={Truck} label="Record Receipt" />
          )}
          {activeTab === 'cost-updates' && (
            <PrimaryButton onClick={() => setShowCostUpdateModal(true)} icon={Plus} label="Log Cost Update" />
          )}
        </div>
      </div>

      <AccessBanner />

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {summaryCards.map((card) => {
          const Icon = card.icon;
          const tone = toneMap[card.tone];
          return (
            <div key={card.label} className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-sm transition-shadow">
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
        <div className="flex items-center gap-1 px-2 pt-2 border-b border-gray-200 overflow-x-auto">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => {
                setActiveTab(tab.id);
                setSearchQuery('');
                setStatusFilter('all');
              }}
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

        <div className="flex flex-col md:flex-row md:items-center gap-3 p-4 border-b border-gray-200">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search current tab..."
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="all">All statuses</option>
              {activeTab === 'suppliers' && ['active', 'inactive'].map((value) => <option key={value} value={value}>{fmt(value)}</option>)}
              {activeTab === 'procurement-requests' && ['pending', 'approved', 'rejected', 'converted-to-po'].map((value) => <option key={value} value={value}>{fmt(value)}</option>)}
              {activeTab === 'purchase-orders' && ['draft', 'approved', 'sent', 'partial', 'completed', 'cancelled'].map((value) => <option key={value} value={value}>{fmt(value)}</option>)}
              {activeTab === 'supplier-deliveries' && ['complete', 'partial', 'discrepancy'].map((value) => <option key={value} value={value}>{fmt(value)}</option>)}
              {activeTab === 'pending-deliveries' && ['on-track', 'partial', 'delayed'].map((value) => <option key={value} value={value}>{fmt(value)}</option>)}
            </select>
            <button className="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white hover:bg-gray-50">
              <Calendar className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">Date</span>
            </button>
            <button className="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white hover:bg-gray-50">
              <Download className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">Export</span>
            </button>
          </div>
        </div>

        {error ? (
          <div className="mx-4 mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
        ) : null}

        {activeTab === 'suppliers' && (
          <TableShell loading={loading} empty={filteredSuppliers.length === 0} emptyText="No suppliers found.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['Supplier', 'Contact', 'Phone', 'Email', 'Location', 'Deliveries', 'Last Delivery', 'Status', 'Actions'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredSuppliers.map((supplier) => (
                  <tr key={supplier.id} className="hover:bg-gray-50/60">
                    <Cell>
                      <div className="font-medium text-gray-900">{supplier.name}</div>
                      <div className="text-xs text-gray-500">{supplier.id}</div>
                    </Cell>
                    <Cell>{supplier.contact || '-'}</Cell>
                    <Cell>{supplier.phone || '-'}</Cell>
                    <Cell>{supplier.email || '-'}</Cell>
                    <Cell>{supplier.location || '-'}</Cell>
                    <Cell>{supplier.totalDeliveries}</Cell>
                    <Cell>{supplier.lastDelivery}</Cell>
                    <Cell><span className={statusBadge(supplier.status)}>{fmt(supplier.status)}</span></Cell>
                    <Cell>
                      <div className="flex flex-wrap gap-2">
                        <RowButton onClick={() => setSelectedSupplierId(supplier.id)} icon={Eye} label="View" />
                        <RowButton onClick={() => setEditingSupplier(supplier)} icon={Pencil} label="Edit" />
                      </div>
                    </Cell>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {activeTab === 'procurement-requests' && (
          <TableShell loading={loading} empty={filteredRequests.length === 0} emptyText="No procurement requests yet.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['Request', 'Supplier', 'Requested By', 'Items', 'Purpose', 'Created', 'Status', 'Actions'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredRequests.map((request) => (
                  <tr key={request.id} className="hover:bg-gray-50/60">
                    <Cell>
                      <div className="font-medium text-gray-900">{request.requestNumber}</div>
                      {request.purchaseOrderNumber ? <div className="text-xs text-gray-500">PO: {request.purchaseOrderNumber}</div> : null}
                    </Cell>
                    <Cell>{request.supplier}</Cell>
                    <Cell>{request.requestedBy}</Cell>
                    <Cell>{request.items.length}</Cell>
                    <Cell>{request.purpose}</Cell>
                    <Cell>{request.createdAt}</Cell>
                    <Cell><span className={statusBadge(request.status)}>{fmt(request.status)}</span></Cell>
                    <Cell>
                      <div className="flex items-center gap-2 flex-wrap">
                        <RowButton onClick={() => setSelectedRequestId(request.id)} icon={Eye} label="View" />
                        {request.status === 'pending' && (
                          <>
                            <RowButton
                              onClick={() => void submitAction(`/api/inventory/suppliers/procurement-requests/${request.id}/action`, { action: 'approve' }, `${request.requestNumber} approved.`)}
                              icon={CheckCircle}
                              label="Approve"
                            />
                            <RowButton
                              onClick={() => void submitAction(`/api/inventory/suppliers/procurement-requests/${request.id}/action`, { action: 'reject' }, `${request.requestNumber} rejected.`)}
                              icon={AlertTriangle}
                              label="Reject"
                            />
                          </>
                        )}
                        {(request.status === 'approved' || request.status === 'pending') && (
                          <RowButton onClick={() => setConvertRequestId(request.id)} icon={Send} label="Convert" />
                        )}
                      </div>
                    </Cell>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {activeTab === 'purchase-orders' && (
          <TableShell loading={loading} empty={filteredPurchaseOrders.length === 0} emptyText="No purchase orders yet.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['PO', 'Supplier', 'Items', 'Qty', 'Expected Delivery'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                  <PriceHeader align="left" className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Expected Cost</PriceHeader>
                  {['Status', 'Actions'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredPurchaseOrders.map((po) => (
                  <tr key={po.id} className="hover:bg-gray-50/60">
                    <Cell>
                      <div className="font-medium text-gray-900">{po.poNumber}</div>
                      <div className="text-xs text-gray-500">{po.trigger || 'Manual PO'}</div>
                    </Cell>
                    <Cell>{po.supplier}</Cell>
                    <Cell>{po.itemsCount}</Cell>
                    <Cell>{po.totalQuantity}</Cell>
                    <Cell>{po.expectedDelivery}</Cell>
                    <PriceCell align="left"><span className="text-sm text-gray-900">GHS {po.expectedCost.toLocaleString()}</span></PriceCell>
                    <Cell><span className={statusBadge(po.status)}>{fmt(po.status)}</span></Cell>
                    <Cell>
                      <div className="flex items-center gap-2 flex-wrap">
                        <RowButton onClick={() => setSelectedPOId(po.id)} icon={Eye} label="View" />
                        {po.status === 'draft' && (
                          <RowButton onClick={() => void submitAction(`/api/inventory/suppliers/purchase-orders/${po.id}/action`, { action: 'approve' }, `${po.poNumber} approved.`)} icon={CheckCircle} label="Approve" />
                        )}
                        {(po.status === 'approved' || po.status === 'draft') && (
                          <RowButton onClick={() => void submitAction(`/api/inventory/suppliers/purchase-orders/${po.id}/action`, { action: 'send' }, `${po.poNumber} marked as sent.`)} icon={Send} label="Send" />
                        )}
                        {['approved', 'sent', 'partial'].includes(po.status) && (
                          <RowButton onClick={() => setReceiptPOId(po.id)} icon={Truck} label="Receive" />
                        )}
                      </div>
                    </Cell>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {activeTab === 'supplier-deliveries' && (
          <TableShell loading={loading} empty={filteredGRNs.length === 0} emptyText="No goods receipts recorded yet.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['GRN', 'Supplier', 'Received By', 'Date', 'Expected', 'Received', 'Variance', 'Damaged'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                  <PriceHeader align="left" className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Discrepancy Value</PriceHeader>
                  {['Audit', 'Status', 'Actions'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredGRNs.map((grn) => {
                  const expected = grn.lineItems.reduce((sum, item) => sum + item.expectedQty, 0);
                  const received = grn.lineItems.reduce((sum, item) => sum + item.receivedQty, 0);
                  const variance = grn.lineItems.reduce((sum, item) => sum + item.variance, 0);
                  const damaged = grn.lineItems.reduce((sum, item) => sum + item.damagedQty, 0);
                  const discrepancyValue = grn.lineItems.reduce((sum, item) => sum + Math.abs(item.variance) * item.unitCost, 0);
                  return (
                    <tr key={grn.id} className="hover:bg-gray-50/60">
                      <Cell>{grn.id}</Cell>
                      <Cell>{grn.supplier}</Cell>
                      <Cell>{grn.receivedBy}</Cell>
                      <Cell>{grn.receivedDate}</Cell>
                      <Cell>{expected}</Cell>
                      <Cell>{received}</Cell>
                      <Cell>{variance}</Cell>
                      <Cell>{damaged || '-'}</Cell>
                      <PriceCell align="left"><span className="text-sm text-gray-900">GHS {discrepancyValue.toLocaleString()}</span></PriceCell>
                      <Cell>{grn.auditTriggered ? <span className="inline-flex items-center gap-1 text-xs text-rose-600 font-medium"><ShieldAlert className="w-3.5 h-3.5" />{grn.auditRef || 'Triggered'}</span> : '-'}</Cell>
                      <Cell><span className={statusBadge(grn.status)}>{fmt(grn.status)}</span></Cell>
                      <Cell><RowButton onClick={() => setSelectedGRNId(grn.id)} icon={Eye} label="View" /></Cell>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableShell>
        )}

        {activeTab === 'pending-deliveries' && (
          <TableShell loading={loading} empty={filteredPendingDeliveries.length === 0} emptyText="No pending deliveries.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['PO', 'Supplier', 'Expected', 'Received', 'Pending', 'Expected Date', 'Delay', 'Status', 'Actions'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredPendingDeliveries.map((delivery) => (
                  <tr key={delivery.id} className="hover:bg-gray-50/60">
                    <Cell>{delivery.poNumber}</Cell>
                    <Cell>{delivery.supplier}</Cell>
                    <Cell>{delivery.expected}</Cell>
                    <Cell>{delivery.received}</Cell>
                    <Cell>{delivery.pending}</Cell>
                    <Cell>{delivery.expectedDate}</Cell>
                    <Cell>{delivery.delayDays > 0 ? `${delivery.delayDays} day(s)` : '-'}</Cell>
                    <Cell><span className={statusBadge(delivery.status)}>{fmt(delivery.status)}</span></Cell>
                    <Cell><RowButton onClick={() => setReceiptPOId(delivery.id)} icon={Truck} label="Receive" /></Cell>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {activeTab === 'cost-updates' && (
          <TableShell loading={loading} empty={filteredCostUpdates.length === 0} emptyText="No cost updates logged.">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['Update', 'Product', 'Supplier'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                  <PriceHeader align="left" className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Old Cost</PriceHeader>
                  <PriceHeader align="left" className="text-xs font-semibold text-gray-600 uppercase tracking-wide">New Cost</PriceHeader>
                  {['Reason', 'Effective Date', 'Changed By'].map((header) => (
                    <HeaderCell key={header}>{header}</HeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredCostUpdates.map((update) => (
                  <tr key={update.id} className="hover:bg-gray-50/60">
                    <Cell>{update.updateNumber}</Cell>
                    <Cell>
                      <div className="font-medium text-gray-900">{update.product}</div>
                      <div className="text-xs text-gray-500">{update.sku}</div>
                    </Cell>
                    <Cell>{update.supplier}</Cell>
                    <PriceCell align="left"><span className="text-sm text-gray-900">GHS {update.oldCost.toLocaleString()}</span></PriceCell>
                    <PriceCell align="left"><span className="text-sm text-gray-900">GHS {update.newCost.toLocaleString()}</span></PriceCell>
                    <Cell>{update.reason}</Cell>
                    <Cell>{update.effectiveDate}</Cell>
                    <Cell>{update.changedBy}</Cell>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}
        <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3">
          <span className="text-xs text-gray-500">Showing up to {Math.min(tabPage * 10, tabCounts[activeTab])} of {tabCounts[activeTab]} records</span>
          {hasMore ? <button type="button" disabled={loading} onClick={() => void loadTabData(false, activeTab)} className="rounded-lg border border-indigo-200 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">{loading ? 'Loading...' : 'View more'}</button> : null}
        </div>
      </div>

      {selectedSupplier ? (
        <DetailDrawer title={selectedSupplier.name} onClose={() => setSelectedSupplierId(null)}>
          <div className="space-y-5">
            <SummaryPanel
              title={selectedSupplier.name}
              subtitle={selectedSupplier.id}
              rightLabel="Total supplied"
              rightValue={`GHS ${selectedSupplier.totalSupplied.toLocaleString()}`}
              badge={selectedSupplier.status}
            >
              <InfoRow icon={User} label={selectedSupplier.contact || '-'} />
              <InfoRow icon={Mail} label={selectedSupplier.email || '-'} />
              <InfoRow icon={MapPin} label={selectedSupplier.location || '-'} />
              <InfoRow icon={Package} label={`${selectedSupplier.totalDeliveries} deliveries`} />
            </SummaryPanel>
            <div className="grid grid-cols-3 gap-3">
              <StatCard label="Total Deliveries" value={String(selectedSupplier.totalDeliveries)} />
              <StatCard label="Last Delivery" value={selectedSupplier.lastDelivery} />
              <StatCard label="Avg Cost Trend" value={`${selectedSupplier.avgCostTrend > 0 ? '+' : ''}${selectedSupplier.avgCostTrend}%`} />
            </div>
            <SectionTitle title="Recent Deliveries" />
            <SimpleTable headers={['ID', 'Item', 'Qty', 'Date']} rows={selectedSupplier.recentDeliveries.map((delivery) => [delivery.id, delivery.item, String(delivery.qty), delivery.date])} />
          </div>
        </DetailDrawer>
      ) : null}

      {selectedRequest ? (
        <DetailDrawer title={selectedRequest.requestNumber} onClose={() => setSelectedRequestId(null)}>
          <div className="space-y-5">
            <SummaryPanel
              title={selectedRequest.supplier}
              subtitle={selectedRequest.requestNumber}
              rightLabel="Status"
              rightValue={fmt(selectedRequest.status)}
              badge={selectedRequest.status}
            >
              <InfoRow icon={User} label={selectedRequest.requestedBy} />
              <InfoRow icon={Calendar} label={selectedRequest.createdAt} />
              <InfoRow icon={ClipboardList} label={selectedRequest.purpose} />
            </SummaryPanel>
            <SectionTitle title="Items" />
            <SimpleTable headers={['Product', 'SKU', 'Qty', 'Unit Cost']} rows={selectedRequest.items.map((item) => [item.product, item.sku, String(item.quantity), `GHS ${item.unitCost.toLocaleString()}`])} />
          </div>
        </DetailDrawer>
      ) : null}

      {selectedPurchaseOrder ? (
        <DetailDrawer title={selectedPurchaseOrder.poNumber} onClose={() => setSelectedPOId(null)}>
          <div className="space-y-5">
            <SummaryPanel
              title={selectedPurchaseOrder.supplier}
              subtitle={selectedPurchaseOrder.poNumber}
              rightLabel="Expected Cost"
              rightValue={`GHS ${selectedPurchaseOrder.expectedCost.toLocaleString()}`}
              badge={selectedPurchaseOrder.status}
            >
              <InfoRow icon={Calendar} label={`Expected: ${selectedPurchaseOrder.expectedDelivery}`} />
              <InfoRow icon={Package} label={`${selectedPurchaseOrder.totalQuantity} units`} />
              <InfoRow icon={FileText} label={selectedPurchaseOrder.trigger || 'Manual purchase order'} />
            </SummaryPanel>
            <SectionTitle title="Line Items" />
            {selectedPurchaseOrder.funding && <div className="rounded-lg bg-indigo-50 p-3 text-sm">
              <strong>Purchase payment:</strong> GHS {selectedPurchaseOrder.funding.amount.toLocaleString()} ? {selectedPurchaseOrder.funding.label}
              <div>{selectedPurchaseOrder.funding.type === 'credit' ? 'Recorded as an unpaid creditor bill in AP Bills.' : 'Deducted from the selected account.'}</div>
            </div>}
            <SimpleTable headers={['Product', 'SKU', 'Ordered', 'Received', 'Rejected', 'Unit Cost']} rows={selectedPurchaseOrder.items.map((item) => [item.product, item.sku, String(item.quantityOrdered), String(item.quantityReceived), String(item.quantityRejected), `GHS ${item.unitCost.toLocaleString()}`])} />
            <SectionTitle title="Receipts" />
            <SimpleTable
              headers={['Received At', 'Received By', 'Location', 'Delivery Note']}
              rows={selectedPurchaseOrder.receipts.length > 0 ? selectedPurchaseOrder.receipts.map((receipt) => [receipt.receivedAt || '-', receipt.receivedBy || '-', receipt.locations?.map((location) => `${location.branch} - ${location.name}`).join(', ') || receipt.locationName || '-', receipt.deliveryNoteNo || '-']) : [['-', '-', '-', '-']]}
            />
          </div>
        </DetailDrawer>
      ) : null}

      {selectedGRN ? (
        <DetailDrawer title={selectedGRN.id} onClose={() => setSelectedGRNId(null)}>
          <div className="space-y-5">
            <SummaryPanel
              title={selectedGRN.supplier}
              subtitle={selectedGRN.linkedRef}
              rightLabel="Status"
              rightValue={fmt(selectedGRN.status)}
              badge={selectedGRN.status}
            >
              <InfoRow icon={User} label={selectedGRN.receivedBy} />
              <InfoRow icon={Calendar} label={selectedGRN.receivedDate} />
              <InfoRow icon={Truck} label={selectedGRN.auditTriggered ? selectedGRN.auditRef || 'Audit triggered' : 'No audit flag'} />
            </SummaryPanel>
            <SectionTitle title="Receipt Lines" />
            <SimpleTable headers={['Product', 'SKU', 'Expected', 'Received', 'Damaged', 'Variance']} rows={selectedGRN.lineItems.map((item) => [item.product, item.sku, String(item.expectedQty), String(item.receivedQty), String(item.damagedQty), String(item.variance)])} />
            <SectionTitle title="Receipt Events" />
            <SimpleTable
              headers={['Received At', 'Received By', 'Location', 'Delivery Note']}
              rows={selectedGRN.receipts.length > 0 ? selectedGRN.receipts.map((receipt) => [receipt.receivedAt || '-', receipt.receivedBy || '-', receipt.locations?.map((location) => `${location.branch} - ${location.name}`).join(', ') || receipt.locationName || '-', receipt.deliveryNoteNo || '-']) : [['-', '-', '-', '-']]}
            />
          </div>
        </DetailDrawer>
      ) : null}

      {showSupplierModal ? (
        <Modal title="Add Supplier" onClose={() => setShowSupplierModal(false)}>
          <SupplierForm
            onCancel={() => setShowSupplierModal(false)}
            onSubmit={async (payload) => {
              await submitAction('/api/inventory/suppliers', payload, `Supplier ${payload.name} created.`);
              setShowSupplierModal(false);
            }}
          />
        </Modal>
      ) : null}

      {editingSupplier ? (
        <Modal title="Edit Supplier Name" onClose={() => setEditingSupplier(null)}>
          <SupplierNameEditForm
            supplier={editingSupplier}
            onCancel={() => setEditingSupplier(null)}
            onSubmit={async (name) => {
              const response = await fetch(`/api/inventory/suppliers/${encodeURIComponent(editingSupplier.id)}`, {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: {
                  Accept: 'application/json',
                  'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                  name,
                  currentName: editingSupplier.name,
                  contact: editingSupplier.contact,
                  phone: editingSupplier.phone,
                  email: editingSupplier.email,
                  location: editingSupplier.location,
                  notes: editingSupplier.notes || '',
                }),
              });
              const data = await parseJsonResponse<{ ok: boolean; error?: string }>(response);
              if (!response.ok || !data.ok) {
                throw new Error(data.error || 'Unable to update supplier.');
              }
              toast.success('Supplier name updated.');
              setEditingSupplier(null);
              await loadData();
            }}
          />
        </Modal>
      ) : null}

      {showRequestModal ? (
        <Modal title="New Procurement Request" onClose={() => setShowRequestModal(false)}>
          <ProcurementRequestForm
            suppliers={supplierOptions}
            products={inventoryProducts}
            onCancel={() => setShowRequestModal(false)}
            onSubmit={async (payload) => {
              await submitAction('/api/inventory/suppliers/procurement-requests', payload, 'Procurement request created.');
              setShowRequestModal(false);
            }}
          />
        </Modal>
      ) : null}

      {showPurchaseOrderModal ? (
        <Modal title="Create Purchase Order" onClose={() => setShowPurchaseOrderModal(false)}>
          <PurchaseOrderForm
            suppliers={supplierOptions}
            products={inventoryProducts}
            onCancel={() => setShowPurchaseOrderModal(false)}
            onSubmit={async (payload) => {
              await submitAction('/api/inventory/suppliers/purchase-orders', payload, 'Purchase order created.');
              setShowPurchaseOrderModal(false);
            }}
          />
        </Modal>
      ) : null}

      {showCostUpdateModal ? (
        <Modal title="Log Cost Update" onClose={() => setShowCostUpdateModal(false)}>
          <CostUpdateForm
            suppliers={supplierOptions}
            products={inventoryProducts}
            onCancel={() => setShowCostUpdateModal(false)}
            onSubmit={async (payload) => {
              await submitAction('/api/inventory/suppliers/cost-updates', payload, 'Cost update logged.');
              setShowCostUpdateModal(false);
            }}
          />
        </Modal>
      ) : null}

      {receiptPurchaseOrder ? (
        <Modal title={`Receive Goods for ${receiptPurchaseOrder.poNumber}`} onClose={() => setReceiptPOId(null)}>
          <ReceiptForm
            purchaseOrder={receiptPurchaseOrder}
            locations={locations}
            onCancel={() => setReceiptPOId(null)}
            onSubmit={async (payload) => {
              await submitAction(`/api/inventory/suppliers/purchase-orders/${receiptPurchaseOrder.id}/receive`, payload, `Receipt recorded for ${receiptPurchaseOrder.poNumber}.`);
              setReceiptPOId(null);
            }}
          />
        </Modal>
      ) : null}

      {convertRequest ? (
        <Modal title={`Convert ${convertRequest.requestNumber} to PO`} onClose={() => setConvertRequestId(null)}>
          <ConvertRequestForm
            requestNumber={convertRequest.requestNumber}
            onCancel={() => setConvertRequestId(null)}
            onSubmit={async (payload) => {
              await submitAction(`/api/inventory/suppliers/procurement-requests/${convertRequest.id}/action`, { action: 'convert', ...payload }, `${convertRequest.requestNumber} converted to a purchase order.`);
              setConvertRequestId(null);
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

function PrimaryButton({ onClick, icon: Icon, label }: { onClick: () => void; icon: typeof Plus; label: string }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2 px-3.5 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors shadow-sm">
      <Icon className="w-4 h-4" />
      <span className="text-sm font-medium">{label}</span>
    </button>
  );
}

function HeaderCell({ children }: { children: ReactNode }) {
  return <th className="text-left px-4 py-3 text-xs font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap">{children}</th>;
}

function Cell({ children }: { children: ReactNode }) {
  return <td className="px-4 py-3 text-sm text-gray-700 align-top">{children}</td>;
}

function RowButton({ onClick, icon: Icon, label }: { onClick: () => void; icon: typeof Eye; label: string }) {
  return (
    <button onClick={onClick} className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 text-sm text-gray-700">
      <Icon className="w-4 h-4" />
      {label}
    </button>
  );
}

function TableShell({ loading, empty, emptyText, children }: { loading: boolean; empty: boolean; emptyText: string; children: ReactNode }) {
  if (loading) {
    return <div className="px-4 py-10 text-center text-sm text-gray-500">Loading...</div>;
  }
  if (empty) {
    return <div className="px-4 py-10 text-center text-sm text-gray-500">{emptyText}</div>;
  }
  return <div className="overflow-x-auto">{children}</div>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl border border-gray-200 max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="p-2 rounded-md hover:bg-gray-100">
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>
        <div className="p-5 overflow-y-auto max-h-[calc(90vh-70px)]">{children}</div>
      </div>
    </div>
  );
}

function DetailDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40">
      <div className="absolute inset-y-0 right-0 w-full max-w-2xl bg-white shadow-2xl border-l border-gray-200 overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 sticky top-0 bg-white z-10">
          <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="p-2 rounded-md hover:bg-gray-100">
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function SummaryPanel({
  title,
  subtitle,
  rightLabel,
  rightValue,
  badge,
  children,
}: {
  title: string;
  subtitle: string;
  rightLabel: string;
  rightValue: string;
  badge: string;
  children: ReactNode;
}) {
  return (
    <div className="bg-gradient-to-br from-indigo-50 to-violet-50 rounded-xl p-5 border border-indigo-100">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-wide text-indigo-700 font-semibold">{subtitle}</div>
          <div className="text-lg font-semibold text-gray-900 mt-1">{title}</div>
          <span className={`mt-2 ${statusBadge(badge)}`}>{fmt(badge)}</span>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-600">{rightLabel}</div>
          <div className="text-xl font-semibold text-gray-900">{rightValue}</div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 mt-4 text-sm">{children}</div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 p-4 bg-white">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-semibold mt-1 text-gray-900">{value}</div>
    </div>
  );
}

function InfoRow({ icon: Icon, label }: { icon: typeof User; label: string }) {
  return (
    <div className="flex items-center gap-2 text-gray-700">
      <Icon className="w-4 h-4 text-indigo-500" />
      <span>{label}</span>
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return <h4 className="text-sm font-semibold text-gray-900">{title}</h4>;
}

function SimpleTable({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50">
          <tr>{headers.map((header) => <th key={header} className="text-left px-3 py-2 text-xs font-semibold text-gray-600">{header}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row, rowIndex) => (
            <tr key={`${headers.join('-')}-${rowIndex}`}>
              {row.map((value, colIndex) => <td key={`${rowIndex}-${colIndex}`} className="px-3 py-2 text-gray-700">{value}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SupplierForm({
  onCancel,
  onSubmit,
}: {
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [location, setLocation] = useState('');
  const [notes, setNotes] = useState('');
  const [status, setStatus] = useState<'active' | 'inactive'>('active');
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      toast.error('Supplier name is required.');
      return;
    }
    setSaving(true);
    try {
      await onSubmit({ name, contact, phone, email, location, notes, status });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to save supplier.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Supplier Name"><input value={name} onChange={(event) => setName(event.target.value)} className={inputCls()} /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Contact Person"><input value={contact} onChange={(event) => setContact(event.target.value)} className={inputCls()} /></Field>
        <Field label="Phone"><input value={phone} onChange={(event) => setPhone(event.target.value)} className={inputCls()} /></Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Email"><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} className={inputCls()} /></Field>
        <Field label="Location"><input value={location} onChange={(event) => setLocation(event.target.value)} className={inputCls()} /></Field>
      </div>
      <Field label="Notes"><textarea value={notes} onChange={(event) => setNotes(event.target.value)} className={`${inputCls()} min-h-24`} /></Field>
      <Field label="Status">
        <select value={status} onChange={(event) => setStatus(event.target.value as 'active' | 'inactive')} className={inputCls()}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
      </Field>
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Save Supplier" />
    </form>
  );
}

function SupplierNameEditForm({
  supplier,
  onCancel,
  onSubmit,
}: {
  supplier: Supplier;
  onCancel: () => void;
  onSubmit: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(supplier.name);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) {
      toast.error('Supplier name is required.');
      return;
    }
    setSaving(true);
    try {
      await onSubmit(cleanName);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to update supplier.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Supplier Name">
        <input value={name} onChange={(event) => setName(event.target.value)} className={inputCls()} autoFocus />
      </Field>
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        This will rename the supplier on saved supplier records and matching purchase/receiving history.
      </div>
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Update Name" />
    </form>
  );
}

function ProcurementRequestForm({
  suppliers,
  products,
  onCancel,
  onSubmit,
}: {
  suppliers: Supplier[];
  products: InventoryProduct[];
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [supplierId, setSupplierId] = useState(suppliers[0]?.id || '');
  const [purpose, setPurpose] = useState('');
  const [notes, setNotes] = useState('');
  const [items, setItems] = useState<EditableLine[]>([{ productId: products[0]?.id || '', quantity: 1, unitCost: products[0]?.unitCost || 0 }]);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const supplier = suppliers.find((row) => row.id === supplierId);
    if (!supplier) {
      toast.error('Select a supplier.');
      return;
    }
    if (!purpose.trim()) {
      toast.error('Purpose is required.');
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        supplierId: supplier.id,
        supplier: supplier.name,
        purpose,
        notes,
        items: items.filter((item) => item.productId && item.quantity > 0),
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to save request.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Supplier">
        <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} className={inputCls()}>
          {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
        </select>
      </Field>
      <Field label="Purpose"><textarea value={purpose} onChange={(event) => setPurpose(event.target.value)} className={`${inputCls()} min-h-24`} /></Field>
      <Field label="Notes"><textarea value={notes} onChange={(event) => setNotes(event.target.value)} className={`${inputCls()} min-h-24`} /></Field>
      <ItemLinesEditor products={products} lines={items} onChange={setItems} />
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Create Request" />
    </form>
  );
}

function PurchaseOrderForm({
  suppliers,
  products,
  onCancel,
  onSubmit,
}: {
  suppliers: Supplier[];
  products: InventoryProduct[];
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [supplierId, setSupplierId] = useState(suppliers[0]?.id || '');
  const [expectedDelivery, setExpectedDelivery] = useState(daysFromToday(7));
  const [trigger, setTrigger] = useState('');
  const [notes, setNotes] = useState('');
  const [status, setStatus] = useState<'draft' | 'approved' | 'sent'>('draft');
  const [items, setItems] = useState<EditableLine[]>([{ productId: products[0]?.id || '', quantity: 1, unitCost: products[0]?.unitCost || 0 }]);
  const [saving, setSaving] = useState(false);
  const [requestId] = useState(() => crypto.randomUUID());
  const [totalAmount, setTotalAmount] = useState('');
  const [paymentType, setPaymentType] = useState('bank');
  const [paymentSourceId, setPaymentSourceId] = useState('');
  const [paymentDueDate, setPaymentDueDate] = useState(daysFromToday(30));
  type FundingOption = { id: string; label: string; type?: string; currency: string; balance: number };
  const [fundingOptions, setFundingOptions] = useState<{accounts: FundingOption[]; creditors: FundingOption[]}>({accounts: [], creditors: []});
  const [fundingLoading, setFundingLoading] = useState(true);
  const [fundingError, setFundingError] = useState('');
  const loadFunding = async () => {
    setFundingLoading(true);
    setFundingError('');
    try {
      const response = await fetch('/api/inventory/suppliers/funding-options', {credentials: 'same-origin'});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load payment sources.');
      setFundingOptions(data);
    } catch (error) {
      setFundingError(error instanceof Error ? error.message : 'Unable to load payment sources.');
    } finally { setFundingLoading(false); }
  };
  useEffect(() => { void loadFunding(); }, []);
  const lineTotal = items.reduce((sum, item) => sum + item.quantity * item.unitCost, 0);
  const amount = totalAmount === '' ? lineTotal : Number(totalAmount);
  const sources = (paymentType === 'credit' ? fundingOptions.creditors : fundingOptions.accounts.filter(row => row.type === paymentType)).filter(row => row.currency === 'GHS');
  const selectedSource = sources.find(row => row.id === paymentSourceId);


  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const supplier = suppliers.find((row) => row.id === supplierId);
    if (!supplier) {
      toast.error('Select a supplier.');
      return;
    }
    if (!expectedDelivery) {
      toast.error('Expected delivery date is required.');
      return;
    }
    if (!selectedSource || !Number.isFinite(amount) || amount <= 0) {
      toast.error('Enter a purchase total and select a payment source.');
      return;
    }
    if (paymentType !== 'credit' && amount > selectedSource.balance) {
      toast.error('The selected account has insufficient funds.');
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        requestId, totalAmount: amount, paymentType, paymentSourceId, paymentDueDate,
        supplierId: supplier.id,
        supplier: supplier.name,
        expectedDelivery,
        trigger,
        notes,
        status,
        items: items.filter((item) => item.productId && item.quantity > 0),
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to create purchase order.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Supplier">
          <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} className={inputCls()}>
            {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
          </select>
        </Field>
        <Field label="Expected Delivery"><input type="date" value={expectedDelivery} onChange={(event) => setExpectedDelivery(event.target.value)} className={inputCls()} /></Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Trigger"><input value={trigger} onChange={(event) => setTrigger(event.target.value)} className={inputCls()} placeholder="Low stock, approved request, seasonal restock" /></Field>
        <Field label="Initial Status">
          <select value={status} onChange={(event) => setStatus(event.target.value as 'draft' | 'approved' | 'sent')} className={inputCls()}>
            <option value="draft">Draft</option>
            <option value="approved">Approved</option>
            <option value="sent">Sent</option>
          </select>
        </Field>
      </div>
      <Field label="Notes"><textarea value={notes} onChange={(event) => setNotes(event.target.value)} className={`${inputCls()} min-h-24`} /></Field>
      <ItemLinesEditor products={products} lines={items} onChange={setItems} />
      <div className="space-y-3 rounded-xl border border-gray-200 p-4">
        <h3 className="font-semibold">Purchase payment</h3>
        <Field label="Total purchase amount (GHS)">
          <input type="number" min="0.01" step="0.01" required value={totalAmount === '' ? lineTotal : totalAmount} onChange={event => setTotalAmount(event.target.value)} className={inputCls()} />
        </Field>
        <p className="text-xs text-gray-500">Product line total: GHS {lineTotal.toLocaleString(undefined, {minimumFractionDigits: 2})}. Enter the full agreed purchase amount.</p>
        <Field label="Payment source">
          <select value={paymentType} onChange={event => { setPaymentType(event.target.value); setPaymentSourceId(''); }} className={inputCls()}>
            <option value="bank">Bank account</option><option value="mobile_money">MoMo account</option><option value="cash">Cash account</option><option value="credit">Creditor / AP Bills</option>
          </select>
        </Field>
        <Field label={paymentType === 'credit' ? 'Creditor ? outstanding balance' : 'Account ? current balance'}>
          <select required disabled={fundingLoading} value={paymentSourceId} onChange={event => setPaymentSourceId(event.target.value)} className={inputCls()}>
            <option value="">{fundingLoading ? 'Loading balances?' : 'Select a source'}</option>
            {sources.map(row => <option key={row.id} value={row.id}>{row.label} ? GHS {row.balance.toLocaleString(undefined, {minimumFractionDigits: 2})}</option>)}
          </select>
        </Field>
        {fundingError && <p role="alert" className="text-sm text-red-600">{fundingError}</p>}
        {!fundingLoading && !fundingError && sources.length === 0 && <p className="text-sm text-amber-700">{paymentType === 'credit' ? 'No GHS creditors found. Create a creditor bill in AP Bills first.' : 'No GHS accounts of this type found. Add one in Accounting ? Bank Accounts.'}</p>}
        <button type="button" disabled={fundingLoading} className="text-sm text-indigo-600" onClick={() => void loadFunding()}>Refresh balances</button>
        {paymentType === 'credit' && <Field label="Bill payment due date"><input required type="date" value={paymentDueDate} onChange={event => setPaymentDueDate(event.target.value)} className={inputCls()} /></Field>}
        {selectedSource && <p className="text-sm">{paymentType === 'credit' ? 'Outstanding after purchase' : 'Account balance after purchase'}: GHS {(selectedSource.balance + (paymentType === 'credit' ? amount : -amount)).toLocaleString(undefined, {minimumFractionDigits: 2})}</p>}
        <p className="text-sm text-gray-600">Saving this order immediately {paymentType === 'credit' ? 'creates an unpaid AP bill for the selected creditor' : 'deducts the full purchase amount from the selected account'}, including when the order is a draft.</p>
      </div>
      <FooterActions onCancel={onCancel} saving={saving || fundingLoading || !!fundingError} saveLabel="Create Purchase Order & Post Amount" />
    </form>
  );
}

function ReceiptForm({
  purchaseOrder,
  locations,
  onCancel,
  onSubmit,
}: {
  purchaseOrder: PurchaseOrder;
  locations: Record<string, Location[]>;
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const flattenedLocations = useMemo(
    () =>
      Object.values(locations)
        .flat()
        .filter((location) => location.status === 'active'),
    [locations],
  );
  const [receivedAt, setReceivedAt] = useState(todayValue());
  const [deliveryNoteNo, setDeliveryNoteNo] = useState('');
  const [comment, setComment] = useState('');
  const [lines, setLines] = useState<ReceiptLine[]>(
    purchaseOrder.items.map((item) => ({
      productId: item.productId,
      locationId: '',
      receivedQty: Math.max(item.quantityOrdered - item.quantityReceived - item.quantityRejected, 0),
      rejectedQty: 0,
      discrepancyReason: '',
      discrepancyNotes: '',
    })),
  );
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const missingLocationIndex = lines.findIndex((line) => Number(line.receivedQty || 0) > 0 && !line.locationId);
    if (missingLocationIndex >= 0) {
      toast.error(`Select a receiving warehouse for ${purchaseOrder.items[missingLocationIndex]?.product || 'each received product'}.`);
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        receivedAt,
        deliveryNoteNo,
        comment,
        items: lines,
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to record receipt.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Receiving Date"><input type="date" value={receivedAt} onChange={(event) => setReceivedAt(event.target.value)} className={inputCls()} /></Field>
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-sm text-indigo-800">Choose the destination warehouse separately beside every product being received.</div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Delivery Note Number"><input value={deliveryNoteNo} onChange={(event) => setDeliveryNoteNo(event.target.value)} className={inputCls()} /></Field>
        <Field label="Comment"><input value={comment} onChange={(event) => setComment(event.target.value)} className={inputCls()} /></Field>
      </div>
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['Product', 'Receiving Warehouse', 'Remaining', 'Received', 'Rejected', 'Reason', 'Notes'].map((header) => (
                <th key={header} className="text-left px-3 py-2 text-xs font-semibold text-gray-600">{header}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {purchaseOrder.items.map((item, index) => {
              const remaining = Math.max(item.quantityOrdered - item.quantityReceived - item.quantityRejected, 0);
              const line = lines[index];
              return (
                <tr key={item.productId}>
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-900">{item.product}</div>
                    <div className="text-xs text-gray-500">{item.sku}</div>
                  </td>
                  <td className="px-3 py-2 min-w-52">
                    <select value={line.locationId} onChange={(event) => updateReceiptLine(lines, setLines, index, 'locationId', event.target.value)} className={smallInputCls()} required={line.receivedQty > 0}>
                      <option value="">Select warehouse...</option>
                      {flattenedLocations.map((location) => <option key={location.id} value={location.id}>{location.branchId} - {location.name}{location.code ? ` (${location.code})` : ''}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-2">{remaining}</td>
                  <td className="px-3 py-2">
                    <input type="number" min={0} max={remaining} value={line.receivedQty} onChange={(event) => updateReceiptLine(lines, setLines, index, 'receivedQty', Number(event.target.value))} className={smallInputCls()} />
                  </td>
                  <td className="px-3 py-2">
                    <input type="number" min={0} max={remaining} value={line.rejectedQty} onChange={(event) => updateReceiptLine(lines, setLines, index, 'rejectedQty', Number(event.target.value))} className={smallInputCls()} />
                  </td>
                  <td className="px-3 py-2">
                    <select value={line.discrepancyReason} onChange={(event) => updateReceiptLine(lines, setLines, index, 'discrepancyReason', event.target.value)} className={smallInputCls()}>
                      <option value="">-</option>
                      <option value="damage">Damage</option>
                      <option value="shortage">Shortage</option>
                      <option value="theft">Theft</option>
                      <option value="error">Error</option>
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <input value={line.discrepancyNotes} onChange={(event) => updateReceiptLine(lines, setLines, index, 'discrepancyNotes', event.target.value)} className={smallInputCls()} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Record Receipt" />
    </form>
  );
}

function CostUpdateForm({
  suppliers,
  products,
  onCancel,
  onSubmit,
}: {
  suppliers: Supplier[];
  products: InventoryProduct[];
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [productId, setProductId] = useState(products[0]?.id || '');
  const [supplierId, setSupplierId] = useState(suppliers[0]?.id || '');
  const selectedProduct = products.find((product) => product.id === productId);
  const [oldCost, setOldCost] = useState(selectedProduct?.unitCost || 0);
  const [newCost, setNewCost] = useState(selectedProduct?.unitCost || 0);
  const [reason, setReason] = useState('');
  const [effectiveDate, setEffectiveDate] = useState(todayValue());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const product = products.find((row) => row.id === productId);
    setOldCost(product?.unitCost || 0);
    setNewCost(product?.unitCost || 0);
  }, [productId, products]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const supplier = suppliers.find((row) => row.id === supplierId);
    const product = products.find((row) => row.id === productId);
    if (!supplier || !product) {
      toast.error('Select a supplier and product.');
      return;
    }
    if (!reason.trim()) {
      toast.error('Reason is required.');
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        productId: product.id,
        supplierId: supplier.id,
        supplier: supplier.name,
        oldCost,
        newCost,
        reason,
        effectiveDate,
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to log cost update.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Product">
          <select value={productId} onChange={(event) => setProductId(event.target.value)} className={inputCls()}>
            {products.map((product) => <option key={product.id} value={product.id}>{product.name} ({product.sku})</option>)}
          </select>
        </Field>
        <Field label="Supplier">
          <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} className={inputCls()}>
            {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
          </select>
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Old Cost"><input type="number" min={0} step="0.01" value={oldCost} onChange={(event) => setOldCost(Number(event.target.value))} className={inputCls()} /></Field>
        <Field label="New Cost"><input type="number" min={0} step="0.01" value={newCost} onChange={(event) => setNewCost(Number(event.target.value))} className={inputCls()} /></Field>
        <Field label="Effective Date"><input type="date" value={effectiveDate} onChange={(event) => setEffectiveDate(event.target.value)} className={inputCls()} /></Field>
      </div>
      <Field label="Reason"><textarea value={reason} onChange={(event) => setReason(event.target.value)} className={`${inputCls()} min-h-24`} /></Field>
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Log Cost Update" />
    </form>
  );
}

function ConvertRequestForm({
  requestNumber,
  onCancel,
  onSubmit,
}: {
  requestNumber: string;
  onCancel: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [expectedDelivery, setExpectedDelivery] = useState(daysFromToday(7));
  const [trigger, setTrigger] = useState(`Converted from ${requestNumber}`);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSubmit({ expectedDelivery, trigger });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to convert request.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Expected Delivery"><input type="date" value={expectedDelivery} onChange={(event) => setExpectedDelivery(event.target.value)} className={inputCls()} /></Field>
      <Field label="Trigger"><input value={trigger} onChange={(event) => setTrigger(event.target.value)} className={inputCls()} /></Field>
      <FooterActions onCancel={onCancel} saving={saving} saveLabel="Convert to Purchase Order" />
    </form>
  );
}

type EditableLine = {
  productId: string;
  quantity: number;
  unitCost: number;
};

function ItemLinesEditor({
  products,
  lines,
  onChange,
}: {
  products: InventoryProduct[];
  lines: EditableLine[];
  onChange: (lines: EditableLine[]) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-gray-900">Items</h4>
        <button
          type="button"
          onClick={() => onChange([...lines, { productId: products[0]?.id || '', quantity: 1, unitCost: products[0]?.unitCost || 0 }])}
          className="text-sm text-indigo-600 hover:text-indigo-700"
        >
          Add Item
        </button>
      </div>
      <div className="space-y-3">
        {lines.map((line, index) => {
          const selectedProduct = products.find((product) => product.id === line.productId);
          return (
            <div key={`${line.productId}-${index}`} className="grid grid-cols-[1.6fr_0.6fr_0.8fr_auto] gap-3">
              <ProductPicker
                products={products}
                value={line.productId}
                onChange={(productId) => {
                  const product = products.find((row) => row.id === productId);
                  onChange(lines.map((current, currentIndex) => currentIndex === index ? { ...current, productId, unitCost: product?.unitCost || 0 } : current));
                }}
              />
              <input
                type="number"
                min={1}
                value={line.quantity}
                onChange={(event) => onChange(lines.map((current, currentIndex) => currentIndex === index ? { ...current, quantity: Number(event.target.value) } : current))}
                className={inputCls()}
                placeholder="Qty"
              />
              <input
                type="number"
                min={0}
                step="0.01"
                value={line.unitCost}
                onChange={(event) => onChange(lines.map((current, currentIndex) => currentIndex === index ? { ...current, unitCost: Number(event.target.value) } : current))}
                className={inputCls()}
                placeholder="Unit Cost"
              />
              <button
                type="button"
                onClick={() => onChange(lines.filter((_, currentIndex) => currentIndex !== index))}
                className="px-3 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 text-gray-600"
                disabled={lines.length === 1}
              >
                Remove
              </button>
              {selectedProduct ? (
                <div className="col-span-4 text-xs text-gray-500">
                  Available stock: {selectedProduct.available} | Category: {selectedProduct.category || '-'} | Brand: {selectedProduct.brand || '-'}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProductPicker({
  products,
  value,
  onChange,
}: {
  products: InventoryProduct[];
  value: string;
  onChange: (productId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const selectedProduct = products.find((product) => product.id === value);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredProducts = normalizedQuery
    ? products.filter((product) => [product.name, product.sku, product.category, product.brand].some((field) => field.toLowerCase().includes(normalizedQuery)))
    : products;

  const chooseProduct = (product: InventoryProduct) => {
    onChange(product.id);
    setQuery('');
    setOpen(false);
  };

  return (
    <div className="relative min-w-0" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={`${inputCls()} flex h-[42px] items-center gap-2 text-left`}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selectedProduct ? (
          <>
            <ProductThumbnail product={selectedProduct} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-gray-900">{selectedProduct.name}</span>
              <span className="block truncate text-xs text-gray-500">{selectedProduct.sku || 'No SKU'}</span>
            </span>
          </>
        ) : (
          <span className="flex-1 text-gray-500">Select a product</span>
        )}
        <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" />
      </button>

      {open ? (
        <div className="absolute z-50 mt-1 w-full min-w-[320px] overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl">
          <div className="border-b border-gray-100 p-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="w-full rounded-md border border-gray-200 py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                placeholder="Search name, SKU, category or brand..."
                autoFocus
              />
            </div>
          </div>
          <div className="max-h-64 overflow-y-auto p-1" role="listbox">
            {filteredProducts.length ? filteredProducts.map((product) => (
              <button
                key={product.id}
                type="button"
                role="option"
                aria-selected={product.id === value}
                onClick={() => chooseProduct(product)}
                className={`flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-indigo-50 ${product.id === value ? 'bg-indigo-50' : ''}`}
              >
                <ProductThumbnail product={product} large />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">{product.name}</span>
                  <span className="block truncate text-xs text-gray-500">{product.sku || 'No SKU'} · {product.category || 'Uncategorized'}</span>
                </span>
                <span className="shrink-0 text-xs text-gray-500">{product.available} available</span>
              </button>
            )) : (
              <div className="px-3 py-6 text-center text-sm text-gray-500">No products match “{query}”.</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ProductThumbnail({ product, large = false }: { product: InventoryProduct; large?: boolean }) {
  const size = large ? 'h-11 w-11' : 'h-8 w-8';
  return (
    <span className={`${size} flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-gray-200 bg-gray-50`}>
      {product.image ? (
        <ImageWithFallback src={product.image} alt="" className="h-full w-full object-contain" />
      ) : (
        <Package className="h-4 w-4 text-gray-400" aria-hidden="true" />
      )}
    </span>
  );
}

function updateReceiptLine<T extends keyof ReceiptLine>(
  lines: ReceiptLine[],
  setLines: (lines: ReceiptLine[]) => void,
  index: number,
  key: T,
  value: ReceiptLine[T],
) {
  setLines(lines.map((line, currentIndex) => currentIndex === index ? { ...line, [key]: value } : line));
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-gray-700">{label}</span>
      {children}
    </label>
  );
}

function FooterActions({ onCancel, saving, saveLabel }: { onCancel: () => void; saving: boolean; saveLabel: string }) {
  return (
    <div className="flex justify-end gap-2 pt-2">
      <button type="button" onClick={onCancel} className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
        Cancel
      </button>
      <button type="submit" disabled={saving} className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-60 disabled:cursor-not-allowed">
        {saving ? 'Saving...' : saveLabel}
      </button>
    </div>
  );
}

function inputCls() {
  return 'w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';
}

function smallInputCls() {
  return 'w-full px-2 py-1.5 border border-gray-200 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';
}
