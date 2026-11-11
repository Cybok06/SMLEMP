import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Package,
  Download,
  Share2,
  MapPin,
  AlertTriangle,
  Building2,
  Box,
  TrendingUp,
  Activity,
  Settings,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Users,
  Search,
} from 'lucide-react';
import { ImageWithFallback } from './figma/ImageWithFallback';
import { toast } from 'sonner';

interface InventoryDetailProps {
  itemId: string;
  onBack: () => void;
}

type InventoryLocationDetail = {
  locationId: string;
  locationName: string;
  locationCode: string;
  branch: string;
  type: string;
  status: 'active' | 'inactive';
  responsibleUser: string;
  productStock: number;
  locationTotalStock: number;
  capacity: number;
  utilizationPct: number;
  latestExpiryDate: string;
  latestCostPrice: number;
};

type ProductCardUsageManager = {
  id: string;
  name: string;
  branch: string;
  price: number;
  cashPrice: number;
  costPrice: number;
  quantityPerCard: number;
  productDocumentId: string;
  updatedAt: string;
};

type ProductCardUsage = {
  id: string;
  name: string;
  description: string;
  image: string;
  productType: string;
  category: string;
  packageName: string;
  price: number;
  cashPrice: number;
  costPrice: number;
  managerCount: number;
  branchCount: number;
  branches: string[];
  quantityPerCard: number;
  requiredUnits: number;
  coveragePct: number;
  customers: number;
  purchaseCount: number;
  salesValue: number;
  completion70: number;
  completion80: number;
  completion90: number;
  lastPurchaseDate: string;
  sourceProductIds: string[];
  managers: ProductCardUsageManager[];
};

type ProductCardUsagePayload = {
  summary: {
    cardCount: number;
    managerCopyCount: number;
    totalRequiredUnits: number;
    availableStock: number;
    coveragePct: number;
    customerCount: number;
    purchaseCount: number;
    salesValue: number;
  };
  cards: ProductCardUsage[];
};

type ForecastDemandPayload = {
  summary: {
    last7DaysUnits: number;
    last30DaysUnits: number;
    last90DaysUnits: number;
    projected30DaysUnits: number;
    dailyRunRate: number;
    coverageDays: number | null;
    recommendedReorderUnits: number;
    riskLevel: string;
    basis: string;
    availableStock: number;
  };
  byCard: Array<{
    cardId: string;
    cardName: string;
    image: string;
    quantityPerCard: number;
    last7DaysUnits: number;
    last30DaysUnits: number;
    last90DaysUnits: number;
    purchaseCount: number;
    salesValue: number;
    lastPurchaseDate: string;
    sharePct: number;
  }>;
  weeklyTrend: Array<{
    label: string;
    units: number;
  }>;
  recentDemand: Array<{
    date: string;
    cardId: string;
    cardName: string;
    customerName: string;
    cardQuantity: number;
    unitsConsumed: number;
    salesValue: number;
  }>;
};

type InventoryMovementEvent = {
  id: string;
  type: string;
  source: string;
  quantity: number;
  direction: 'in' | 'out';
  branch: string;
  locationId: string;
  locationName: string;
  locationCode: string;
  orderId: string;
  lineId: string;
  costPrice: number;
  sellingPrice: number;
  movedAt: string;
};

type ProductCustomerRow = {
  id: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  branch: string;
  location: string;
  dateRegistered: string;
  purchaseDate: string;
  amountPaid: number;
  productCard: string;
  profileUrl: string;
};

type ProductCustomersPayload = {
  summary: {
    customerCount: number;
    purchaseCount: number;
    totalPaid: number;
  };
  branches: string[];
  customers: ProductCustomerRow[];
};

type InventoryProductDetail = {
  id: string;
  sku: string;
  name: string;
  category: string;
  brand: string;
  description: string;
  image: string;
  totalStock: number;
  available: number;
  reserved: number;
  forecastDemand: number;
  safeAvailable: number;
  reorderPoint: number;
  reorderQuantity: number;
  unitCost: number;
  status: string;
  lastRestocked: string;
  createdAt: string;
  locations: InventoryLocationDetail[];
  movementHistory: InventoryMovementEvent[];
  productCards: ProductCardUsagePayload;
  customers: ProductCustomersPayload;
  forecast: ForecastDemandPayload;
};

type ProductDetailResponse = {
  ok: boolean;
  product?: InventoryProductDetail;
  error?: string;
};

const LINKED_CARDS_PER_PAGE = 5;

export function InventoryDetail({ itemId, onBack }: InventoryDetailProps) {
  const [activeTab, setActiveTab] = useState('overview');
  const [product, setProduct] = useState<InventoryProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sharing, setSharing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [linkedCardsPage, setLinkedCardsPage] = useState(1);
  const [customersPage, setCustomersPage] = useState(1);
  const [customerSearchQuery, setCustomerSearchQuery] = useState('');
  const [customerBranchFilter, setCustomerBranchFilter] = useState('all');

  const tabs = [
    { id: 'overview', label: 'Overview', icon: Package, live: true },
    { id: 'locations', label: 'Stock by Location', icon: MapPin, live: true },
    { id: 'product-cards', label: 'Product Cards', icon: Box, live: true },
    { id: 'customers', label: 'Customers', icon: Users, live: true },
    { id: 'forecast', label: 'Forecast Demand', icon: TrendingUp, live: true },
    { id: 'movement', label: 'Movement History', icon: Activity, live: true },
    { id: 'adjustments', label: 'Adjustments', icon: Settings, live: false },
  ];

  useEffect(() => {
    let mounted = true;

    const loadProduct = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await fetch(`/api/inventory/products/${itemId}`, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        const data = (await response.json()) as ProductDetailResponse;
        if (!response.ok || !data.ok || !data.product) {
          throw new Error(data.error || 'Unable to load product details.');
        }
        if (mounted) {
          setProduct(data.product);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Unable to load product details.');
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };

    void loadProduct();
    return () => {
      mounted = false;
    };
  }, [itemId]);

  const locationSummary = useMemo(() => {
    const locations = product?.locations || [];
    return {
      totalLocations: locations.length,
      totalProductStock: locations.reduce((sum, location) => sum + location.productStock, 0),
      averageUtilization: locations.length > 0
        ? Math.round(locations.reduce((sum, location) => sum + location.utilizationPct, 0) / locations.length)
        : 0,
    };
  }, [product]);

  const cardSummary = product?.productCards?.summary || {
    cardCount: 0,
    managerCopyCount: 0,
    totalRequiredUnits: 0,
    availableStock: 0,
    coveragePct: 0,
    customerCount: 0,
    purchaseCount: 0,
    salesValue: 0,
  };
  const linkedCards = product?.productCards?.cards || [];
  const linkedCardsTotalPages = Math.max(1, Math.ceil(linkedCards.length / LINKED_CARDS_PER_PAGE));
  const linkedCardsStartIndex = (linkedCardsPage - 1) * LINKED_CARDS_PER_PAGE;
  const paginatedLinkedCards = linkedCards.slice(linkedCardsStartIndex, linkedCardsStartIndex + LINKED_CARDS_PER_PAGE);
  const customerSummary = product?.customers?.summary || {
    customerCount: 0,
    purchaseCount: 0,
    totalPaid: 0,
  };
  const customerBranches = product?.customers?.branches || [];
  const customerRows = product?.customers?.customers || [];
  const filteredCustomerRows = useMemo(() => {
    const search = customerSearchQuery.trim().toLowerCase();
    return customerRows.filter((row) => {
      if (customerBranchFilter !== 'all' && row.branch !== customerBranchFilter) {
        return false;
      }
      if (!search) {
        return true;
      }
      const haystacks = [
        row.customerName,
        row.customerPhone,
        row.location,
        row.branch,
        row.productCard,
      ].map((value) => String(value || '').toLowerCase());
      return haystacks.some((value) => value.includes(search));
    });
  }, [customerBranchFilter, customerRows, customerSearchQuery]);
  const customersPerPage = 12;
  const customersTotalPages = Math.max(1, Math.ceil(filteredCustomerRows.length / customersPerPage));
  const customersStartIndex = (customersPage - 1) * customersPerPage;
  const paginatedCustomerRows = filteredCustomerRows.slice(customersStartIndex, customersStartIndex + customersPerPage);
  const forecast = product?.forecast;
  const movementHistory = product?.movementHistory || [];
  const forecastSummary = forecast?.summary || {
    last7DaysUnits: 0,
    last30DaysUnits: 0,
    last90DaysUnits: 0,
    projected30DaysUnits: 0,
    dailyRunRate: 0,
    coverageDays: null,
    recommendedReorderUnits: 0,
    riskLevel: 'no-demand',
    basis: '',
    availableStock: 0,
  };

  useEffect(() => {
    setLinkedCardsPage(1);
  }, [itemId, activeTab]);

  useEffect(() => {
    setCustomersPage(1);
  }, [itemId, activeTab, customerBranchFilter, customerSearchQuery]);

  useEffect(() => {
    setLinkedCardsPage((page) => Math.min(page, linkedCardsTotalPages));
  }, [linkedCardsTotalPages]);

  useEffect(() => {
    setCustomersPage((page) => Math.min(page, customersTotalPages));
  }, [customersTotalPages]);

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      good: 'text-green-600 bg-green-50 border-green-200',
      warning: 'text-orange-600 bg-orange-50 border-orange-200',
      critical: 'text-red-600 bg-red-50 border-red-200',
      active: 'text-green-600 bg-green-50 border-green-200',
      inactive: 'text-gray-600 bg-gray-50 border-gray-200',
    };
    return colors[status] || 'text-gray-600 bg-gray-50 border-gray-200';
  };

  const getMovementTypeColor = (direction: 'in' | 'out') =>
    direction === 'in'
      ? 'text-green-700 bg-green-50 border-green-200'
      : 'text-red-700 bg-red-50 border-red-200';

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'good':
      case 'active':
        return CheckCircle;
      case 'warning':
        return Clock;
      case 'critical':
        return AlertTriangle;
      default:
        return Package;
    }
  };

  const getLocationTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      'main-storage': 'Main Storage',
      room: 'Room',
      dispatch: 'Dispatch',
      receiving: 'Receiving',
      damaged: 'Damaged',
      returned: 'Returned',
      'delivery-holding': 'Delivery Holding',
    };
    return labels[type] || type;
  };

  const getForecastRiskClass = (riskLevel: string) => {
    const classes: Record<string, string> = {
      healthy: 'border-green-200 bg-green-50 text-green-700',
      warning: 'border-orange-200 bg-orange-50 text-orange-700',
      critical: 'border-red-200 bg-red-50 text-red-700',
      'no-demand': 'border-gray-200 bg-gray-50 text-gray-700',
    };
    return classes[riskLevel] || classes['no-demand'];
  };

  const currentTabMeta = tabs.find((tab) => tab.id === activeTab);
  const currentTabIsExportable = activeTab === 'overview' || activeTab === 'locations' || activeTab === 'product-cards' || activeTab === 'forecast';

  const downloadCurrentTabPdf = async () => {
    if (!product) return null;
    const response = await fetch(`/api/inventory/products/${product.id}/export.pdf?tab=${encodeURIComponent(activeTab)}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/pdf' },
    });
    if (!response.ok) {
      let message = `Export failed with status ${response.status}`;
      try {
        const data = await response.json();
        message = data?.error || message;
      } catch {
        // ignore JSON parse failure
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/i);
    return {
      blob,
      filename: match?.[1] || `${product.name}_${activeTab}.pdf`,
    };
  };

  const handleExport = async () => {
    if (!product || !currentTabIsExportable) {
      toast.error('Only live tabs can be exported right now.');
      return;
    }
    setExporting(true);
    try {
      const pdf = await downloadCurrentTabPdf();
      if (!pdf) return;
      const url = window.URL.createObjectURL(pdf.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = pdf.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
      toast.success(`${currentTabMeta?.label || 'Tab'} exported.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Unable to export PDF.');
    } finally {
      setExporting(false);
    }
  };

  const handleShare = async () => {
    if (!product || !currentTabIsExportable) {
      toast.error('Only live tabs can be shared right now.');
      return;
    }
    setSharing(true);
    try {
      const pdf = await downloadCurrentTabPdf();
      if (!pdf) return;
      const file = new File([pdf.blob], pdf.filename, { type: 'application/pdf' });
      const shareData = {
        title: `${product.name} - ${currentTabMeta?.label || 'Details'}`,
        text: `SmartLiving inventory ${currentTabMeta?.label || 'details'} for ${product.name}`,
        files: [file],
      };

      if (navigator.share && (!navigator.canShare || navigator.canShare({ files: [file] }))) {
        await navigator.share(shareData);
        toast.success(`${currentTabMeta?.label || 'Details'} shared.`);
        return;
      }

      await navigator.clipboard.writeText(`${window.location.href}?tab=${activeTab}`);
      toast.success('Share link copied to clipboard. PDF export is available with the Export button.');
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      toast.error(err instanceof Error ? err.message : 'Unable to share current tab.');
    } finally {
      setSharing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="rounded-lg p-2 transition-colors hover:bg-gray-100"
          >
            <ArrowLeft className="h-5 w-5 text-gray-600" />
          </button>
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">{product?.name || 'Product Details'}</h1>
            <p className="mt-1 text-gray-600">
              {product ? `${product.sku} • ${product.category}` : 'Loading detail view...'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleShare}
            disabled={sharing || loading}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100"
          >
            <Share2 className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">{sharing ? 'Sharing...' : 'Share'}</span>
          </button>
          <button
            onClick={handleExport}
            disabled={exporting || loading}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100"
          >
            <Download className="h-4 w-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">{exporting ? 'Exporting...' : 'Export'}</span>
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
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-6 py-4 transition-colors ${
                    activeTab === tab.id
                      ? 'border-indigo-600 text-indigo-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
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
          {loading ? (
            <div className="text-sm text-gray-500">Loading product details...</div>
          ) : error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {error}
            </div>
          ) : !product ? (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
              Product not found.
            </div>
          ) : (
            <>
              {activeTab === 'overview' && (
                <div className="space-y-6">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <div className="mb-1 text-sm text-blue-600">Total Stock</div>
                      <div className="text-2xl font-semibold text-gray-900">{product.totalStock}</div>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-600">Available</div>
                      <div className="text-2xl font-semibold text-gray-900">{product.available}</div>
                    </div>
                    <div className="rounded-lg border border-purple-200 bg-purple-50 p-4">
                      <div className="mb-1 text-sm text-purple-600">Locations</div>
                      <div className="text-2xl font-semibold text-gray-900">{locationSummary.totalLocations}</div>
                    </div>
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                      <div className="mb-1 text-sm text-amber-600">Avg Utilization</div>
                      <div className="text-2xl font-semibold text-gray-900">{locationSummary.averageUtilization}%</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <div className="flex h-64 items-center justify-center rounded-lg bg-white p-4">
                        <ImageWithFallback
                          src={product.image}
                          alt={product.name}
                          className="h-full w-full object-contain"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                      <div>
                        <h3 className="mb-4 font-semibold text-gray-900">Item Details</h3>
                        <div className="space-y-3">
                          <DetailRow label="SKU" value={product.sku} />
                          <DetailRow label="Category" value={product.category} />
                          <DetailRow label="Brand" value={product.brand || '-'} />
                          <DetailRow label="Description" value={product.description || '-'} multiline />
                          <DetailRow label="Last Restocked" value={product.lastRestocked || '-'} />
                          <DetailRow label="Created On" value={product.createdAt || '-'} />
                        </div>
                      </div>

                      <div>
                        <h3 className="mb-4 font-semibold text-gray-900">Inventory Settings</h3>
                        <div className="space-y-3">
                          <DetailRow label="Unit Cost" value={`GHS ${product.unitCost.toLocaleString()}`} />
                          <DetailRow label="Reorder Point" value={`${product.reorderPoint} units`} />
                          <DetailRow label="Reorder Quantity" value={`${product.reorderQuantity} units`} />
                          <DetailRow
                            label="Safe Available"
                            value={`${product.safeAvailable} units`}
                            valueClassName={product.safeAvailable < 0 ? 'text-red-600' : 'text-green-600'}
                          />
                          <div className="flex justify-between">
                            <span className="text-gray-600">Status</span>
                            <span className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs font-medium ${getStatusColor(product.status)}`}>
                              {(() => {
                                const StatusIcon = getStatusIcon(product.status);
                                return <StatusIcon className="h-3 w-3" />;
                              })()}
                              {product.status}
                            </span>
                          </div>
                        </div>

                        {product.safeAvailable < 0 && (
                          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4">
                            <div className="flex items-start gap-3">
                              <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-600" />
                              <div>
                                <div className="mb-1 font-medium text-red-900">Stock Shortage Alert</div>
                                <div className="text-sm text-red-700">
                                  Current available stock is below the safe threshold. Consider restocking this item soon.
                                </div>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'locations' && (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                      <div className="mb-1 text-sm text-indigo-600">Total Locations</div>
                      <div className="text-2xl font-semibold text-gray-900">{locationSummary.totalLocations}</div>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-600">Product Units Across Locations</div>
                      <div className="text-2xl font-semibold text-gray-900">{locationSummary.totalProductStock}</div>
                    </div>
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                      <div className="mb-1 text-sm text-amber-600">Average Utilization</div>
                      <div className="text-2xl font-semibold text-gray-900">{locationSummary.averageUtilization}%</div>
                    </div>
                  </div>

                  {product.locations.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center text-sm text-gray-600">
                      No location allocations found for this product yet.
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {product.locations.map((location) => (
                        <div key={`${location.locationId}-${location.branch}`} className="rounded-lg border border-gray-200 bg-gray-50 p-5">
                          <div className="mb-4 flex items-start justify-between gap-4">
                            <div className="flex items-start gap-3">
                              <div className="rounded-lg bg-indigo-100 p-2">
                                <Building2 className="h-5 w-5 text-indigo-600" />
                              </div>
                              <div>
                                <h4 className="font-semibold text-gray-900">{location.locationName}</h4>
                                <p className="mt-1 text-sm text-gray-600">
                                  {location.branch} • {location.locationCode || 'No code'}
                                </p>
                              </div>
                            </div>
                            <span className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs font-medium ${getStatusColor(location.status)}`}>
                              {location.status}
                            </span>
                          </div>

                          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
                            <LocationMetric label="Product Stock Here" value={String(location.productStock)} />
                            <LocationMetric label="Total Location Stock" value={String(location.locationTotalStock)} />
                            <LocationMetric label="Capacity" value={location.capacity > 0 ? String(location.capacity) : 'Not set'} />
                            <LocationMetric label="Responsible User" value={location.responsibleUser || '-'} />
                          </div>

                          <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div>
                              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">Location Details</div>
                              <div className="space-y-2 text-sm">
                                <div className="flex justify-between">
                                  <span className="text-gray-600">Type</span>
                                  <span className="font-medium text-gray-900">{getLocationTypeLabel(location.type)}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-gray-600">Latest Cost</span>
                                  <span className="font-medium text-gray-900">GHS {location.latestCostPrice.toLocaleString()}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-gray-600">Latest Expiry</span>
                                  <span className="font-medium text-gray-900">{location.latestExpiryDate || '-'}</span>
                                </div>
                              </div>
                            </div>

                            <div>
                              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">Capacity Usage</div>
                              <div className="rounded-lg border border-gray-200 bg-white p-3">
                                <div className="mb-2 flex items-center justify-between text-sm">
                                  <span className="text-gray-600">Utilization</span>
                                  <span className="font-medium text-gray-900">{location.utilizationPct}%</span>
                                </div>
                                <div className="h-2 rounded-full bg-gray-200">
                                  <div
                                    className={`h-2 rounded-full ${
                                      location.utilizationPct > 85 ? 'bg-red-500' : location.utilizationPct > 60 ? 'bg-orange-500' : 'bg-green-500'
                                    }`}
                                    style={{ width: `${Math.min(location.utilizationPct, 100)}%` }}
                                  />
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'product-cards' && (
                <div className="space-y-5">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                      <div className="mb-1 text-sm text-indigo-600">Linked Cards</div>
                      <div className="text-2xl font-semibold text-gray-900">{cardSummary.cardCount}</div>
                    </div>
                    <div className="rounded-lg border border-violet-200 bg-violet-50 p-4">
                      <div className="mb-1 text-sm text-violet-600">Manager Copies</div>
                      <div className="text-2xl font-semibold text-gray-900">{cardSummary.managerCopyCount}</div>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-600">Stock Coverage</div>
                      <div className="text-2xl font-semibold text-gray-900">{cardSummary.coveragePct}%</div>
                    </div>
                    <div className="rounded-lg border border-sky-200 bg-sky-50 p-4">
                      <div className="mb-1 text-sm text-sky-600">Customers</div>
                      <div className="text-2xl font-semibold text-gray-900">{cardSummary.customerCount}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                    <ProductCardMetric label="Available units" value={cardSummary.availableStock.toLocaleString()} />
                    <ProductCardMetric label="Units required by cards" value={cardSummary.totalRequiredUnits.toLocaleString()} />
                    <ProductCardMetric label="Linked sales value" value={`GHS ${cardSummary.salesValue.toLocaleString()}`} />
                  </div>

                  {linkedCards.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center text-sm text-gray-600">
                      This inventory item is not used inside any product card yet.
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {paginatedLinkedCards.map((card) => (
                        <div key={card.id} className="overflow-hidden rounded-xl border border-gray-200 bg-white">
                          <div className="grid grid-cols-1 gap-5 border-b border-gray-200 bg-gray-50 p-5 lg:grid-cols-[120px_minmax(0,1fr)_220px]">
                            <div className="flex h-28 items-center justify-center rounded-lg border border-gray-200 bg-white p-3">
                              <ImageWithFallback src={card.image} alt={card.name} className="h-full w-full object-contain" />
                            </div>
                            <div>
                              <div className="flex flex-wrap items-center gap-2">
                                {card.productType && (
                                  <span className="rounded-full bg-indigo-100 px-2.5 py-1 text-xs font-semibold text-indigo-700">
                                    {card.productType}
                                  </span>
                                )}
                                {card.category && (
                                  <span className="rounded-full bg-gray-200 px-2.5 py-1 text-xs font-semibold text-gray-700">
                                    {card.category}
                                  </span>
                                )}
                              </div>
                              <h3 className="mt-3 text-lg font-semibold text-gray-900">{card.name}</h3>
                              <p className="mt-1 line-clamp-2 text-sm text-gray-600">{card.description || 'No description provided.'}</p>
                              <div className="mt-3 flex flex-wrap gap-2 text-xs text-gray-600">
                                <span className="rounded-full bg-white px-3 py-1">{card.managerCount} manager copy/copies</span>
                                <span className="rounded-full bg-white px-3 py-1">{card.branchCount} branch{card.branchCount === 1 ? '' : 'es'}</span>
                                <span className="rounded-full bg-white px-3 py-1">{card.quantityPerCard} unit(s) per card</span>
                              </div>
                            </div>
                            <div className="rounded-lg border border-gray-200 bg-white p-4">
                              <div className="mb-2 flex items-center justify-between text-sm">
                                <span className="text-gray-600">Coverage</span>
                                <span className="font-semibold text-gray-900">{card.coveragePct}%</span>
                              </div>
                              <div className="h-2 rounded-full bg-gray-200">
                                <div
                                  className={`h-2 rounded-full ${card.coveragePct >= 100 ? 'bg-green-500' : card.coveragePct >= 60 ? 'bg-orange-500' : 'bg-red-500'}`}
                                  style={{ width: `${Math.min(card.coveragePct, 100)}%` }}
                                />
                              </div>
                              <div className="mt-3 text-xs text-gray-500">
                                {card.requiredUnits.toLocaleString()} units required from {product.totalStock.toLocaleString()} available.
                              </div>
                            </div>
                          </div>

                          <div className="grid grid-cols-1 gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                            <div className="overflow-x-auto">
                              <table className="min-w-full divide-y divide-gray-200 text-sm">
                                <thead>
                                  <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                                    <th className="pb-3 pr-4">Manager</th>
                                    <th className="pb-3 pr-4">Branch</th>
                                    <th className="pb-3 pr-4">Installment</th>
                                    <th className="pb-3 pr-4">Cash</th>
                                    <th className="pb-3 pr-4">Units</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                  {card.managers.map((manager) => (
                                    <tr key={manager.productDocumentId}>
                                      <td className="py-3 pr-4 font-medium text-gray-900">{manager.name}</td>
                                      <td className="py-3 pr-4 text-gray-600">{manager.branch || '-'}</td>
                                      <td className="py-3 pr-4 text-gray-900">GHS {manager.price.toLocaleString()}</td>
                                      <td className="py-3 pr-4 text-gray-900">GHS {manager.cashPrice.toLocaleString()}</td>
                                      <td className="py-3 pr-4 text-gray-900">{manager.quantityPerCard}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                              <ProductCardMetric label="Customers" value={card.customers.toLocaleString()} />
                              <ProductCardMetric label="Purchases" value={card.purchaseCount.toLocaleString()} />
                              <ProductCardMetric label="Sales value" value={`GHS ${card.salesValue.toLocaleString()}`} />
                              <ProductCardMetric label="Last sale" value={card.lastPurchaseDate || '-'} />
                              <ProductCardMetric label="70%+" value={card.completion70.toLocaleString()} />
                              <ProductCardMetric label="90%+" value={card.completion90.toLocaleString()} />
                            </div>
                          </div>
                        </div>
                      ))}

                      <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          Showing <span className="font-semibold text-gray-900">{linkedCardsStartIndex + 1}</span>-
                          <span className="font-semibold text-gray-900">
                            {Math.min(linkedCardsStartIndex + LINKED_CARDS_PER_PAGE, linkedCards.length)}
                          </span>{' '}
                          of <span className="font-semibold text-gray-900">{linkedCards.length}</span> linked card
                          {linkedCards.length === 1 ? '' : 's'}
                        </div>

                        <div className="inline-flex items-center gap-2">
                          <button
                            onClick={() => setLinkedCardsPage((page) => Math.max(1, page - 1))}
                            disabled={linkedCardsPage === 1}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <ChevronLeft className="h-4 w-4" />
                            Previous
                          </button>
                          <span className="rounded-lg bg-gray-100 px-3 py-2 font-semibold text-gray-900">
                            Page {linkedCardsPage} of {linkedCardsTotalPages}
                          </span>
                          <button
                            onClick={() => setLinkedCardsPage((page) => Math.min(linkedCardsTotalPages, page + 1))}
                            disabled={linkedCardsPage === linkedCardsTotalPages}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            Next
                            <ChevronRight className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'forecast' && (
                <div className="space-y-5">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <div className="mb-1 text-sm text-blue-600">Projected 30-Day Demand</div>
                      <div className="text-2xl font-semibold text-gray-900">{forecastSummary.projected30DaysUnits.toLocaleString()}</div>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-600">Available Stock</div>
                      <div className="text-2xl font-semibold text-gray-900">{forecastSummary.availableStock.toLocaleString()}</div>
                    </div>
                    <div className="rounded-lg border border-purple-200 bg-purple-50 p-4">
                      <div className="mb-1 text-sm text-purple-600">Coverage Days</div>
                      <div className="text-2xl font-semibold text-gray-900">
                        {forecastSummary.coverageDays === null ? '-' : forecastSummary.coverageDays.toLocaleString()}
                      </div>
                    </div>
                    <div className={`rounded-lg border p-4 ${getForecastRiskClass(forecastSummary.riskLevel)}`}>
                      <div className="mb-1 text-sm">Demand Risk</div>
                      <div className="text-2xl font-semibold capitalize">{forecastSummary.riskLevel.replace('-', ' ')}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
                    <ForecastMetric label="Last 7 days" value={`${forecastSummary.last7DaysUnits.toLocaleString()} units`} />
                    <ForecastMetric label="Last 30 days" value={`${forecastSummary.last30DaysUnits.toLocaleString()} units`} />
                    <ForecastMetric label="Last 90 days" value={`${forecastSummary.last90DaysUnits.toLocaleString()} units`} />
                    <ForecastMetric label="Recommended reorder" value={`${forecastSummary.recommendedReorderUnits.toLocaleString()} units`} />
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <h3 className="font-semibold text-gray-900">Forecast Basis</h3>
                        <p className="mt-1 text-sm text-gray-600">{forecastSummary.basis || 'No forecast basis available.'}</p>
                      </div>
                      <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700">
                        Daily run rate: <span className="font-semibold text-gray-900">{forecastSummary.dailyRunRate.toLocaleString()} units</span>
                      </div>
                    </div>
                  </div>

                  {forecastSummary.projected30DaysUnits <= 0 ? (
                    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center text-sm text-gray-600">
                      No customer demand has been recorded for linked cards yet. Forecast will populate after customers purchase cards that use this inventory item.
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                        <div className="rounded-xl border border-gray-200 bg-white p-5">
                          <div className="mb-4 flex items-center justify-between">
                            <h3 className="font-semibold text-gray-900">8-Week Demand Trend</h3>
                            <span className="text-xs font-medium uppercase tracking-wide text-gray-500">Units consumed</span>
                          </div>
                          <div className="space-y-3">
                            {(forecast?.weeklyTrend || []).map((week) => {
                              const maxUnits = Math.max(...(forecast?.weeklyTrend || []).map((row) => row.units), 1);
                              return (
                                <div key={week.label} className="grid grid-cols-[120px_minmax(0,1fr)_56px] items-center gap-3 text-sm">
                                  <div className="truncate text-gray-600">{week.label}</div>
                                  <div className="h-2 rounded-full bg-gray-200">
                                    <div
                                      className="h-2 rounded-full bg-indigo-600"
                                      style={{ width: `${Math.max(4, (week.units / maxUnits) * 100)}%` }}
                                    />
                                  </div>
                                  <div className="text-right font-semibold text-gray-900">{week.units}</div>
                                </div>
                              );
                            })}
                          </div>
                        </div>

                        <div className="rounded-xl border border-gray-200 bg-white p-5">
                          <h3 className="font-semibold text-gray-900">Restock Decision</h3>
                          <div className="mt-4 space-y-3 text-sm">
                            <div className="flex justify-between gap-3">
                              <span className="text-gray-600">Projected demand</span>
                              <span className="font-semibold text-gray-900">{forecastSummary.projected30DaysUnits.toLocaleString()} units</span>
                            </div>
                            <div className="flex justify-between gap-3">
                              <span className="text-gray-600">Current stock</span>
                              <span className="font-semibold text-gray-900">{forecastSummary.availableStock.toLocaleString()} units</span>
                            </div>
                            <div className="flex justify-between gap-3">
                              <span className="text-gray-600">Reorder point</span>
                              <span className="font-semibold text-gray-900">{product.reorderPoint.toLocaleString()} units</span>
                            </div>
                            <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3">
                              <div className="text-xs font-medium uppercase tracking-wide text-indigo-700">Recommendation</div>
                              <div className="mt-1 text-lg font-semibold text-gray-900">
                                {forecastSummary.recommendedReorderUnits > 0
                                  ? `Order ${forecastSummary.recommendedReorderUnits.toLocaleString()} units`
                                  : 'No reorder needed now'}
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="rounded-xl border border-gray-200 bg-white">
                        <div className="border-b border-gray-200 px-5 py-4">
                          <h3 className="font-semibold text-gray-900">Demand by Linked Card</h3>
                        </div>
                        <div className="overflow-x-auto">
                          <table className="min-w-full divide-y divide-gray-200 text-sm">
                            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                              <tr>
                                <th className="px-5 py-3">Card</th>
                                <th className="px-5 py-3">7 Days</th>
                                <th className="px-5 py-3">30 Days</th>
                                <th className="px-5 py-3">90 Days</th>
                                <th className="px-5 py-3">Share</th>
                                <th className="px-5 py-3">Last Purchase</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                              {(forecast?.byCard || []).map((card) => (
                                <tr key={card.cardId}>
                                  <td className="px-5 py-4">
                                    <div className="flex items-center gap-3">
                                      <div className="h-10 w-10 overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1">
                                        <ImageWithFallback src={card.image} alt={card.cardName} className="h-full w-full object-contain" />
                                      </div>
                                      <div>
                                        <div className="font-medium text-gray-900">{card.cardName}</div>
                                        <div className="text-xs text-gray-500">{card.quantityPerCard} unit(s) per card</div>
                                      </div>
                                    </div>
                                  </td>
                                  <td className="px-5 py-4 font-medium text-gray-900">{card.last7DaysUnits.toLocaleString()}</td>
                                  <td className="px-5 py-4 font-medium text-gray-900">{card.last30DaysUnits.toLocaleString()}</td>
                                  <td className="px-5 py-4 font-medium text-gray-900">{card.last90DaysUnits.toLocaleString()}</td>
                                  <td className="px-5 py-4 text-gray-700">{card.sharePct}%</td>
                                  <td className="px-5 py-4 text-gray-700">{card.lastPurchaseDate || '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      <div className="rounded-xl border border-gray-200 bg-white">
                        <div className="border-b border-gray-200 px-5 py-4">
                          <h3 className="font-semibold text-gray-900">Recent Demand Events</h3>
                        </div>
                        <div className="divide-y divide-gray-100">
                          {(forecast?.recentDemand || []).map((event) => (
                            <div key={`${event.date}-${event.cardId}-${event.customerName}`} className="grid grid-cols-1 gap-2 px-5 py-4 text-sm md:grid-cols-[120px_minmax(0,1fr)_160px_120px] md:items-center">
                              <div className="font-medium text-gray-900">{event.date}</div>
                              <div>
                                <div className="font-medium text-gray-900">{event.cardName}</div>
                                <div className="text-gray-500">{event.customerName}</div>
                              </div>
                              <div className="text-gray-700">{event.cardQuantity} card(s) sold</div>
                              <div className="font-semibold text-gray-900">{event.unitsConsumed} unit(s)</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              )}

              {activeTab === 'customers' && (
                <div className="space-y-5">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                      <div className="mb-1 text-sm text-indigo-600">Customers</div>
                      <div className="text-2xl font-semibold text-gray-900">{customerSummary.customerCount.toLocaleString()}</div>
                    </div>
                    <div className="rounded-lg border border-sky-200 bg-sky-50 p-4">
                      <div className="mb-1 text-sm text-sky-600">Purchases</div>
                      <div className="text-2xl font-semibold text-gray-900">{customerSummary.purchaseCount.toLocaleString()}</div>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-600">Amount Paid</div>
                      <div className="text-2xl font-semibold text-gray-900">GHS {customerSummary.totalPaid.toLocaleString()}</div>
                    </div>
                  </div>

                  <div className="grid gap-3 rounded-xl border border-gray-200 bg-white p-4 md:grid-cols-[minmax(0,1.5fr)_220px]">
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                      <input
                        type="text"
                        value={customerSearchQuery}
                        onChange={(event) => setCustomerSearchQuery(event.target.value)}
                        placeholder="Search customer name, phone, branch, location, or product card..."
                        className="w-full rounded-lg border border-gray-200 py-2 pl-10 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    <select
                      value={customerBranchFilter}
                      onChange={(event) => setCustomerBranchFilter(event.target.value)}
                      className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    >
                      <option value="all">All Branches</option>
                      {customerBranches.map((branch) => (
                        <option key={branch} value={branch}>
                          {branch}
                        </option>
                      ))}
                    </select>
                  </div>

                  {filteredCustomerRows.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center text-sm text-gray-600">
                      No customer purchases found for this inventory item.
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
                        <table className="min-w-full divide-y divide-gray-200 text-sm">
                          <thead className="bg-gray-50">
                            <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                              <th className="px-4 py-3">Customer</th>
                              <th className="px-4 py-3">Phone</th>
                              <th className="px-4 py-3">Date Registered</th>
                              <th className="px-4 py-3">Location</th>
                              <th className="px-4 py-3">Branch</th>
                              <th className="px-4 py-3">Amount Paid</th>
                              <th className="px-4 py-3">Product Card</th>
                              <th className="px-4 py-3 text-right">Profile</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100 bg-white">
                            {paginatedCustomerRows.map((row) => (
                              <tr key={row.id} className="hover:bg-gray-50/60">
                                <td className="px-4 py-4 font-medium text-gray-900">{row.customerName || 'Customer'}</td>
                                <td className="px-4 py-4 text-gray-700">{row.customerPhone || '-'}</td>
                                <td className="px-4 py-4 text-gray-700">{row.dateRegistered || row.purchaseDate || '-'}</td>
                                <td className="px-4 py-4 text-gray-700">{row.location || '-'}</td>
                                <td className="px-4 py-4 text-gray-700">{row.branch || '-'}</td>
                                <td className="px-4 py-4 font-medium text-gray-900">GHS {row.amountPaid.toLocaleString()}</td>
                                <td className="px-4 py-4 text-gray-700">{row.productCard || '-'}</td>
                                <td className="px-4 py-4">
                                  <div className="flex justify-end">
                                    <a
                                      href={row.profileUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                                    >
                                      View
                                    </a>
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          Showing <span className="font-semibold text-gray-900">{customersStartIndex + 1}</span>-
                          <span className="font-semibold text-gray-900">
                            {Math.min(customersStartIndex + customersPerPage, filteredCustomerRows.length)}
                          </span>{' '}
                          of <span className="font-semibold text-gray-900">{filteredCustomerRows.length}</span> customer purchase
                          {filteredCustomerRows.length === 1 ? '' : 's'}
                        </div>
                        <div className="inline-flex items-center gap-2">
                          <button
                            onClick={() => setCustomersPage((page) => Math.max(1, page - 1))}
                            disabled={customersPage === 1}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <ChevronLeft className="h-4 w-4" />
                            Previous
                          </button>
                          <span className="rounded-lg bg-gray-100 px-3 py-2 font-semibold text-gray-900">
                            Page {customersPage} of {customersTotalPages}
                          </span>
                          <button
                            onClick={() => setCustomersPage((page) => Math.min(customersTotalPages, page + 1))}
                            disabled={customersPage === customersTotalPages}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            Next
                            <ChevronRight className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'movement' && (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 text-sm text-green-700">Inbound Movements</div>
                      <div className="text-2xl font-semibold text-gray-900">
                        {movementHistory.filter((event) => event.direction === 'in').reduce((sum, event) => sum + Math.abs(event.quantity), 0)}
                      </div>
                    </div>
                    <div className="rounded-lg border border-red-200 bg-red-50 p-4">
                      <div className="mb-1 text-sm text-red-700">Outbound Movements</div>
                      <div className="text-2xl font-semibold text-gray-900">
                        {movementHistory.filter((event) => event.direction === 'out').reduce((sum, event) => sum + Math.abs(event.quantity), 0)}
                      </div>
                    </div>
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                      <div className="mb-1 text-sm text-indigo-700">Movement Events</div>
                      <div className="text-2xl font-semibold text-gray-900">{movementHistory.length}</div>
                    </div>
                  </div>

                  {movementHistory.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center text-sm text-gray-600">
                      No stock movement history has been recorded for this product yet.
                    </div>
                  ) : (
                    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
                      <div className="grid grid-cols-[150px_minmax(0,1fr)_140px_180px_120px] gap-4 border-b border-gray-200 bg-gray-50 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
                        <div>Date</div>
                        <div>Movement</div>
                        <div>Quantity</div>
                        <div>Location</div>
                        <div>Reference</div>
                      </div>
                      <div className="divide-y divide-gray-100">
                        {movementHistory.map((event) => (
                          <div
                            key={event.id}
                            className="grid grid-cols-[150px_minmax(0,1fr)_140px_180px_120px] gap-4 px-5 py-4 text-sm"
                          >
                            <div className="font-medium text-gray-900">
                              {event.movedAt ? new Date(event.movedAt).toLocaleString() : '-'}
                            </div>
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${getMovementTypeColor(event.direction)}`}>
                                  {event.type}
                                </span>
                                {event.source && (
                                  <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">
                                    {event.source}
                                  </span>
                                )}
                              </div>
                              <div className="mt-2 text-xs text-gray-500">
                                {event.branch || 'No branch'}{event.locationCode ? ` | ${event.locationCode}` : ''}
                              </div>
                            </div>
                            <div className={`font-semibold ${event.direction === 'in' ? 'text-green-700' : 'text-red-700'}`}>
                              {event.direction === 'in' ? '+' : '-'}{Math.abs(event.quantity)}
                            </div>
                            <div className="text-gray-700">
                              <div className="font-medium text-gray-900">{event.locationName || 'Unknown location'}</div>
                              <div className="text-xs text-gray-500">{event.branch || '-'}</div>
                            </div>
                            <div className="text-xs text-gray-500">
                              {event.orderId ? `Order ${event.orderId.slice(-6)}` : '-'}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {!tabs.find((tab) => tab.id === activeTab)?.live && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
                  This tab is not connected to the live inventory backend yet. `Overview` and `Stock by Location` are now live.
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailRow({
  label,
  value,
  multiline,
  valueClassName,
}: {
  label: string;
  value: string;
  multiline?: boolean;
  valueClassName?: string;
}) {
  return (
    <div className={`flex ${multiline ? 'items-start' : 'items-center'} justify-between gap-4`}>
      <span className="text-gray-600">{label}</span>
      <span className={`font-medium text-gray-900 text-right ${valueClassName || ''}`}>{value}</span>
    </div>
  );
}

function LocationMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-gray-900">{value}</div>
    </div>
  );
}

function ProductCardMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-1 text-base font-semibold text-gray-900">{value}</div>
    </div>
  );
}

function ForecastMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-2 text-lg font-semibold text-gray-900">{value}</div>
    </div>
  );
}
