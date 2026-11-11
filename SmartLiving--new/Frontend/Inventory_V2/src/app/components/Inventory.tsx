import { useEffect, useState } from 'react';
import {
  Search,
  Download,
  Plus,
  Package,
  AlertTriangle,
  TrendingUp,
  Grid3x3,
  List,
  Eye,
  Edit,
  MoreVertical,
  CheckCircle,
  Clock,
  BarChart3,
  X,
  ZoomIn,
  ClipboardCheck,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { InventoryDetail } from './InventoryDetail';
import {
  AddInventoryItemModal,
  type EditableInventoryItem,
  type InventoryItemPayload,
} from './AddInventoryItemModal';
import { UpdateStockModal } from './UpdateStockModal';
import { ImageWithFallback } from './figma/ImageWithFallback';
import { AccessBanner, PriceMask, PriceHeader, PriceCell } from './PriceGuard';
import { useAccessSafe } from '../context/RoleAccessContext';

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

type InventoryItem = {
  id: string;
  sku: string;
  name: string;
  category: string;
  brand: string;
  description: string;
  image: string;
  cfImageId?: string;
  totalStock: number;
  available: number;
  reserved: number;
  forecastDemand: number;
  safeAvailable: number;
  reorderPoint: number;
  reorderQuantity: number;
  unitCost: number;
  sellingPrice: number;
  status: string;
  entries?: {
    branch: string;
    locationId: string;
    locationName: string;
    locationCode: string;
    quantity: number;
    expiryDate: string;
    reminderDays: number;
    costPrice: number;
    sellingPrice: number;
    installmentPrice: number | null;
    wholesalePrice: number | null;
  }[];
};

interface InventoryApiResponse {
  summary?: Record<string, number>;
  categories?: string[];
  brands?: string[];
  pagination?: {page: number; total: number; totalPages: number};
  ok: boolean;
  products?: InventoryItem[];
  branches?: string[];
  locations?: Record<string, WarehouseLocationOption[]>;
  error?: string;
}

interface InventoryProps {
  onNavigate?: (menu: string, tab?: string) => void;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();

  try {
    return JSON.parse(raw) as T;
  } catch {
    const preview = raw.trim().slice(0, 140) || `HTTP ${response.status}`;
    throw new Error(preview);
  }
}

const DEFAULT_INVENTORY_CATEGORIES = [
  'Electronics',
  'Furniture',
  'Appliances',
  'Foods',
  'Lighting',
  'Decor',
  'Kitchen',
  'Bedroom',
  'Outdoor',
  'Office',
  'Other',
];

const CUSTOM_CATEGORY_STORAGE_KEY = 'inventory_v2_custom_categories';

export function Inventory({ onNavigate }: InventoryProps = {}) {
  const { canAccessPage } = useAccessSafe();
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('table');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [filterBrand, setFilterBrand] = useState<string>('all');
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [previewImage, setPreviewImage] = useState<{ url: string; name: string } | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingItem, setEditingItem] = useState<EditableInventoryItem | null>(null);
  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<InventoryItem | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');
  const [showUpdateStockModal, setShowUpdateStockModal] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [availableBranches, setAvailableBranches] = useState<string[]>([]);
  const [availableLocations, setAvailableLocations] = useState<Record<string, WarehouseLocationOption[]>>({});
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState({page: 1, total: 0, totalPages: 1});
  const [summaryMetrics, setSummaryMetrics] = useState<Record<string, number>>({totalItems: 0, totalStock: 0, availableStock: 0, reservedStock: 0, forecastDemand: 0, criticalItems: 0, lowStockItems: 0});
  const [uniqueCategories, setUniqueCategories] = useState<string[]>([]);
  const [uniqueBrands, setUniqueBrands] = useState<string[]>([]);
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [customCategories, setCustomCategories] = useState<string[]>(() => {
    if (typeof window === 'undefined') return [];
    try {
      const parsed = JSON.parse(window.localStorage.getItem(CUSTOM_CATEGORY_STORAGE_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string' && value.trim() !== '') : [];
    } catch {
      return [];
    }
  });

  const loadInventory = async (signal?: AbortSignal) => {
    setIsLoading(true);
    setLoadError('');
    try {
      const response = await fetch('/api/inventory/products?' + new URLSearchParams({paginated: '1', page: String(page), search: searchQuery, category: filterCategory, brand: filterBrand}), {
        signal, credentials: 'same-origin',
      });
      const data = await parseJsonResponse<InventoryApiResponse>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to load inventory products.');
      }
      if (signal?.aborted) return;
      if (data.pagination) {setPagination(data.pagination); if(page !== data.pagination.page) setPage(data.pagination.page);}
      if (data.summary) setSummaryMetrics(data.summary);
      setUniqueCategories(data.categories || []); setUniqueBrands(data.brands || []);
      setInventoryItems(Array.isArray(data.products) ? data.products : []);
      setAvailableBranches(Array.isArray(data.branches) ? data.branches : []);
      setAvailableLocations(data.locations || {});
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to load inventory products.';
      if (!signal?.aborted) setLoadError(message);
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => void loadInventory(controller.signal), 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [page, searchQuery, filterCategory, filterBrand]);
  useEffect(() => { setPage(1); }, [searchQuery, filterCategory, filterBrand]);

  useEffect(() => {
    window.localStorage.setItem(CUSTOM_CATEGORY_STORAGE_KEY, JSON.stringify(customCategories));
  }, [customCategories]);


  const categoryOptions = Array.from(new Set([
    ...DEFAULT_INVENTORY_CATEGORIES,
    ...uniqueCategories,
    ...customCategories,
  ])).sort((a, b) => a.localeCompare(b));


  const filteredItems = inventoryItems;

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      good: 'text-green-600 bg-green-50 border-green-200',
      warning: 'text-orange-600 bg-orange-50 border-orange-200',
      critical: 'text-red-600 bg-red-50 border-red-200',
    };
    return colors[status] || 'text-gray-600 bg-gray-50 border-gray-200';
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'good':
        return CheckCircle;
      case 'warning':
        return Clock;
      case 'critical':
        return AlertTriangle;
      default:
        return Package;
    }
  };

  const handleSaveNewItem = async (payload: InventoryItemPayload) => {
    setIsSaving(true);
    try {
      const response = await fetch('/api/inventory/products', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string }>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to save inventory item.');
      }
      await loadInventory();
    } finally {
      setIsSaving(false);
    }
  };

  const buildEditableItem = (item: InventoryItem): EditableInventoryItem => {
    const latestEntry = item.entries && item.entries.length > 0
      ? item.entries[item.entries.length - 1]
      : undefined;

    const stockAssignments = Array.from(
      new Map(
        (item.entries || []).map((entry) => [
          `${entry.branch}:${entry.locationId}`,
          { branch: entry.branch, locationId: entry.locationId },
        ])
      ).values()
    );

    return {
      id: item.id,
      name: item.name,
      description: item.description || '',
      category: item.category || '',
      brand: item.brand || '',
      imageUrl: item.image || '',
      imageId: item.cfImageId,
      quantity: item.totalStock,
      expiryDate: latestEntry?.expiryDate || '',
      reminderDays: latestEntry?.reminderDays || 7,
      costPrice: latestEntry?.costPrice ?? item.unitCost ?? 0,
      sellingPrice: latestEntry?.sellingPrice ?? item.sellingPrice ?? 0,
      installmentPrice: latestEntry?.installmentPrice ?? null,
      wholesalePrice: latestEntry?.wholesalePrice ?? null,
      branches: Array.from(new Set(stockAssignments.map((assignment) => assignment.branch))),
      stockAssignments,
    };
  };

  const handleSaveEditedItem = async (payload: InventoryItemPayload) => {
    if (!payload.id) {
      throw new Error('Missing item ID for update.');
    }

    setIsSaving(true);
    try {
      const response = await fetch(`/api/inventory/products/${payload.id}`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string }>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to update inventory item.');
      }
      setEditingItem(null);
      await loadInventory();
    } finally {
      setIsSaving(false);
    }
  };

  const openDeleteDialog = (item: InventoryItem) => {
    setOpenActionMenuId(null);
    setDeleteTarget(item);
    setDeleteConfirmName('');
  };

  const handleDeleteItem = async () => {
    if (!deleteTarget) {
      return;
    }

    setIsDeleting(true);
    try {
      const response = await fetch(`/api/inventory/products/${deleteTarget.id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ confirmName: deleteConfirmName }),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string }>(response);
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Unable to delete inventory item.');
      }
      setDeleteTarget(null);
      setDeleteConfirmName('');
      await loadInventory();
      toast.success('Inventory item deleted.');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleAddCategory = (nextCategory: string) => {
    const trimmed = nextCategory.trim();
    if (!trimmed) return;
    setCustomCategories(current => {
      const exists = [...DEFAULT_INVENTORY_CATEGORIES, ...uniqueCategories, ...current]
        .some(category => category.toLowerCase() === trimmed.toLowerCase());
      return exists ? current : [...current, trimmed];
    });
  };

  const handleExportClick = async () => {
    setIsExporting(true);
    try {
      const params = new URLSearchParams();
      if (searchQuery.trim()) {
        params.set('search', searchQuery.trim());
      }
      if (filterCategory !== 'all') {
        params.set('category', filterCategory);
      }
      if (filterBrand !== 'all') {
        params.set('brand', filterBrand);
      }

      const response = await fetch(`/api/inventory/products/export.pdf?${params.toString()}`, {
        credentials: 'same-origin',
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text.trim().slice(0, 180) || 'Unable to export inventory report.');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename=\"?([^"]+)\"?/i);
      anchor.href = url;
      anchor.download = match?.[1] || 'inventory_stock_report.pdf';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Inventory report exported.');
    } catch (exportError) {
      toast.error(exportError instanceof Error ? exportError.message : 'Unable to export inventory report.');
    } finally {
      setIsExporting(false);
    }
  };

  if (selectedItem) {
    return (
      <InventoryDetail
        itemId={selectedItem}
        onBack={() => setSelectedItem(null)}
      />
    );
  }

  return (
    <>
      {showAddModal && (
        <AddInventoryItemModal
          onClose={() => setShowAddModal(false)}
          onSave={handleSaveNewItem}
          branches={availableBranches}
          locations={availableLocations}
          categories={categoryOptions}
          onAddCategory={handleAddCategory}
        />
      )}

      {editingItem && (
        <AddInventoryItemModal
          onClose={() => setEditingItem(null)}
          onSave={handleSaveEditedItem}
          branches={availableBranches}
          locations={availableLocations}
          categories={categoryOptions}
          onAddCategory={handleAddCategory}
          mode="edit"
          initialItem={editingItem}
        />
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Delete Inventory Item</h2>
                <p className="mt-1 text-sm text-gray-500">
                  Type the exact product name to confirm permanent deletion.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (isDeleting) return;
                  setDeleteTarget(null);
                  setDeleteConfirmName('');
                }}
                className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-4 px-6 py-5">
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                This will remove <span className="font-semibold">{deleteTarget.name}</span> from inventory and clear its stored stock entries.
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">
                  Enter <span className="font-semibold text-gray-900">{deleteTarget.name}</span>
                </label>
                <input
                  type="text"
                  value={deleteConfirmName}
                  onChange={(event) => setDeleteConfirmName(event.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-4 py-2.5 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-red-500"
                  placeholder="Type the exact product name"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                type="button"
                onClick={() => {
                  if (isDeleting) return;
                  setDeleteTarget(null);
                  setDeleteConfirmName('');
                }}
                className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteItem}
                disabled={isDeleting || deleteConfirmName !== deleteTarget.name}
                className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                  isDeleting || deleteConfirmName !== deleteTarget.name
                    ? 'cursor-not-allowed bg-red-300'
                    : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {isDeleting ? 'Deleting...' : 'Delete Product'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showUpdateStockModal && (
        <UpdateStockModal
          onClose={() => setShowUpdateStockModal(false)}
          branches={availableBranches}
          locations={availableLocations}
          onSessionClosed={loadInventory}
        />
      )}

      {previewImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-75 p-4"
          onClick={() => setPreviewImage(null)}
        >
          <div className="relative max-w-5xl max-h-[90vh]" onClick={e => e.stopPropagation()}>
            <button
              onClick={() => setPreviewImage(null)}
              className="absolute -top-12 right-0 rounded-lg bg-white p-2 transition-colors hover:bg-gray-100"
            >
              <X className="h-6 w-6 text-gray-900" />
            </button>
            <img
              src={previewImage.url}
              alt={previewImage.name}
              className="max-h-[90vh] max-w-full rounded-lg shadow-2xl"
            />
            <div className="absolute -bottom-12 left-0 right-0 text-center">
              <p className="font-medium text-white">{previewImage.name}</p>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Inventory</h1>
            <p className="mt-1 text-gray-600">Track physical stock items and availability</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleExportClick}
              disabled={isExporting}
              className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 transition-colors hover:bg-gray-50"
            >
              <Download className="h-4 w-4 text-gray-600" />
              <span className="text-sm font-medium text-gray-700">{isExporting ? 'Exporting...' : 'Export'}</span>
            </button>
            <button
              onClick={() => setShowUpdateStockModal(true)}
              className="flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-white shadow-sm transition-colors hover:bg-green-700 active:bg-green-800"
            >
              <RefreshCw className="h-4 w-4" />
              <span className="text-sm font-medium">Update Stock</span>
            </button>
            {onNavigate && canAccessPage('audit') && (
              <button
                onClick={() => onNavigate('audit', 'stock-taking')}
                className="flex items-center gap-2 rounded-lg bg-purple-600 px-4 py-2 text-white shadow-sm transition-colors hover:bg-purple-700 active:bg-purple-800"
              >
                <ClipboardCheck className="h-4 w-4" />
                <span className="text-sm font-medium">Stock Taking</span>
              </button>
            )}
            <button
              onClick={() => setShowAddModal(true)}
              disabled={isSaving}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-white shadow-sm transition-colors hover:bg-indigo-700 active:bg-indigo-800 disabled:cursor-not-allowed disabled:bg-indigo-300"
            >
              <Plus className="h-4 w-4" />
              <span className="text-sm font-medium">{isSaving ? 'Saving...' : 'Add Item'}</span>
            </button>
          </div>
        </div>

        <AccessBanner />

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-indigo-50 p-2">
                <Package className="h-5 w-5 text-indigo-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.totalItems}</div>
            <div className="text-sm text-gray-600">Total Items</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-blue-50 p-2">
                <BarChart3 className="h-5 w-5 text-blue-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.totalStock.toLocaleString()}</div>
            <div className="text-sm text-gray-600">Total Stock</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-green-50 p-2">
                <CheckCircle className="h-5 w-5 text-green-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.availableStock.toLocaleString()}</div>
            <div className="text-sm text-gray-600">Available Stock</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-purple-50 p-2">
                <Clock className="h-5 w-5 text-purple-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.reservedStock.toLocaleString()}</div>
            <div className="text-sm text-gray-600">Reserved Stock</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-amber-50 p-2">
                <TrendingUp className="h-5 w-5 text-amber-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.forecastDemand.toLocaleString()}</div>
            <div className="text-sm text-gray-600">Forecast Demand</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-orange-50 p-2">
                <AlertTriangle className="h-5 w-5 text-orange-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.lowStockItems}</div>
            <div className="text-sm text-gray-600">Low Stock Items</div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between">
              <div className="rounded-lg bg-red-50 p-2">
                <AlertTriangle className="h-5 w-5 text-red-600" />
              </div>
            </div>
            <div className="mb-1 text-2xl font-semibold text-gray-900">{summaryMetrics.criticalItems}</div>
            <div className="text-sm text-gray-600">Critical Items</div>
          </div>
        </div>

        {loadError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {loadError}
          </div>
        )}

        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex items-center justify-between gap-4">
            <div className="max-w-md flex-1">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search by name, SKU, category, or brand..."
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 py-2 pl-10 pr-4 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <select
                value={filterCategory}
                onChange={e => setFilterCategory(e.target.value)}
                className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="all">All Categories</option>
                {uniqueCategories.map(cat => (
                  <option key={cat} value={cat}>{cat}</option>
                ))}
              </select>

              <select
                value={filterBrand}
                onChange={e => setFilterBrand(e.target.value)}
                className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="all">All Brands</option>
                {uniqueBrands.map(brand => (
                  <option key={brand} value={brand}>{brand}</option>
                ))}
              </select>

              <div className="flex items-center gap-1 rounded-lg bg-gray-100 p-1">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`rounded p-2 ${viewMode === 'grid' ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'}`}
                >
                  <Grid3x3 className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setViewMode('table')}
                  className={`rounded p-2 ${viewMode === 'table' ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'}`}
                >
                  <List className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </div>

        {isLoading && (
          <div className="rounded-lg border border-gray-200 bg-white px-6 py-12 text-center text-sm text-gray-500">
            Loading inventory products...
          </div>
        )}

        <div className="flex items-center justify-between rounded border bg-white p-3"><span>Page {pagination.page} of {pagination.totalPages} ({pagination.total} matching items)</span><div className="flex gap-3"><button disabled={page <= 1 || isLoading} onClick={() => setPage(page - 1)}>Previous</button><button disabled={page >= pagination.totalPages || isLoading} onClick={() => setPage(page + 1)}>Next</button></div></div>
        {!isLoading && filteredItems.length === 0 && (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white px-6 py-12 text-center">
            <Package className="mx-auto mb-3 h-8 w-8 text-gray-300" />
            <p className="text-sm font-medium text-gray-700">No inventory products yet</p>
            <p className="mt-1 text-sm text-gray-500">Use Add Item to create the first product in the `inventory_products` collection.</p>
          </div>
        )}

        {!isLoading && filteredItems.length > 0 && viewMode === 'grid' && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            <button
              onClick={() => setShowAddModal(true)}
              className="group flex min-h-64 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-indigo-300 bg-white transition-all hover:border-indigo-500 hover:bg-indigo-50"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-100 transition-colors group-hover:bg-indigo-200">
                <Plus className="h-6 w-6 text-indigo-600" />
              </div>
              <div className="text-center">
                <p className="font-medium text-indigo-600">Add New Item</p>
                <p className="mt-0.5 text-xs text-gray-500">Click to add inventory</p>
              </div>
            </button>

            {filteredItems.map(item => {
              const StatusIcon = getStatusIcon(item.status);
              return (
                <div
                  key={item.id}
                  className="overflow-hidden rounded-lg border border-gray-200 bg-white transition-shadow hover:shadow-lg"
                >
                  <div className="group relative flex h-48 items-center justify-center bg-gray-100 p-3">
                    <ImageWithFallback
                      src={item.image}
                      alt={item.name}
                      className="h-full w-full object-contain"
                    />
                    <button
                      type="button"
                      onClick={() => setPreviewImage({ url: item.image, name: item.name })}
                      className="absolute inset-0 flex items-center justify-center bg-transparent"
                    >
                      <div className="rounded-full bg-black/45 p-2 opacity-0 transition-opacity group-hover:opacity-100">
                        <ZoomIn className="h-8 w-8 text-white" />
                      </div>
                    </button>
                    <span className={`absolute right-3 top-3 rounded border px-2 py-1 text-xs font-medium ${getStatusColor(item.status)}`}>
                      <StatusIcon className="mr-1 inline h-3 w-3" />
                      {item.status}
                    </span>
                  </div>

                  <div className="p-5">
                    <div className="mb-3 flex items-start justify-between gap-3">
                      <div>
                        <h3 className="mb-1 font-semibold text-gray-900">{item.name}</h3>
                        <p className="text-xs text-gray-500">
                          {item.sku} • {item.category}
                          {item.brand && <> • <span className="font-medium text-indigo-600">{item.brand}</span></>}
                        </p>
                      </div>
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => setOpenActionMenuId((current) => current === item.id ? null : item.id)}
                          className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                          title="More"
                        >
                          <MoreVertical className="h-4 w-4" />
                        </button>
                        {openActionMenuId === item.id && (
                          <div className="absolute right-0 top-11 z-20 min-w-40 rounded-xl border border-gray-200 bg-white p-1 shadow-xl">
                            <button
                              type="button"
                              onClick={() => {
                                setOpenActionMenuId(null);
                                setEditingItem(buildEditableItem(item));
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-50"
                            >
                              <Edit className="h-4 w-4" />
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => openDeleteDialog(item)}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50"
                            >
                              <Trash2 className="h-4 w-4" />
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="mb-4 space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Total</span>
                        <span className="font-semibold text-gray-900">{item.totalStock}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Available</span>
                        <span className="font-semibold text-green-600">{item.available}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Reserved</span>
                        <span className="font-semibold text-purple-600">{item.reserved}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Unit Cost</span>
                        <PriceMask value={item.unitCost} className="font-semibold text-gray-700" />
                      </div>
                      <div className="flex justify-between border-t border-gray-200 pt-2 text-sm">
                        <span className="text-gray-600">Safe Available</span>
                        <span className={`font-semibold ${item.safeAvailable < 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {item.safeAvailable}
                        </span>
                      </div>
                    </div>

                    <button
                      onClick={() => setSelectedItem(item.id)}
                      className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-white transition-colors hover:bg-indigo-700"
                    >
                      <Eye className="h-4 w-4" />
                      <span className="text-sm font-medium">View Details</span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!isLoading && filteredItems.length > 0 && viewMode === 'table' && (
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="border-b border-gray-200 bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Item</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Category</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Brand</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Total Stock</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Available</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Reserved</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Forecast</th>
                    <PriceHeader align="left" className="text-xs font-medium uppercase tracking-wider text-gray-500">Unit Cost (GHS)</PriceHeader>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Safe Avail.</th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Status</th>
                    <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {filteredItems.map(item => {
                    const StatusIcon = getStatusIcon(item.status);
                    return (
                      <tr key={item.id} className="transition-colors hover:bg-gray-50">
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-3">
                            <div className="group relative flex h-16 w-16 flex-shrink-0 items-center justify-center overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1">
                              <ImageWithFallback
                                src={item.image}
                                alt={item.name}
                                className="h-full w-full object-contain"
                              />
                              <button
                                type="button"
                                onClick={() => setPreviewImage({ url: item.image, name: item.name })}
                                className="absolute inset-0 flex items-center justify-center bg-transparent"
                              >
                                <span className="rounded-full bg-black/45 p-1 opacity-0 transition-opacity group-hover:opacity-100">
                                  <ZoomIn className="h-5 w-5 text-white" />
                                </span>
                              </button>
                            </div>
                            <div>
                              <div className="font-medium text-gray-900">{item.name}</div>
                              <div className="text-sm text-gray-500">{item.sku}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-900">{item.category}</td>
                        <td className="px-6 py-4">
                          {item.brand ? (
                            <span className="text-sm font-medium text-indigo-600">{item.brand}</span>
                          ) : (
                            <span className="text-sm text-gray-400">-</span>
                          )}
                        </td>
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{item.totalStock}</td>
                        <td className="px-6 py-4 text-sm font-semibold text-green-600">{item.available}</td>
                        <td className="px-6 py-4 text-sm font-semibold text-purple-600">{item.reserved}</td>
                        <td className="px-6 py-4 text-sm font-semibold text-amber-600">{item.forecastDemand}</td>
                        <PriceCell align="left">
                          <span className="text-sm font-medium text-gray-900">{item.unitCost.toLocaleString()}</span>
                        </PriceCell>
                        <td className="px-6 py-4">
                          <span className={`text-sm font-semibold ${item.safeAvailable < 0 ? 'text-red-600' : 'text-green-600'}`}>
                            {item.safeAvailable}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span className={`rounded border px-2 py-1 text-xs font-medium ${getStatusColor(item.status)}`}>
                            <StatusIcon className="mr-1 inline h-3 w-3" />
                            {item.status}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center justify-end gap-2">
                            <button
                              onClick={() => setSelectedItem(item.id)}
                              className="rounded p-2 transition-colors hover:bg-gray-100"
                              title="View Details"
                            >
                              <Eye className="h-4 w-4 text-gray-600" />
                            </button>
                            <div className="relative">
                              <button
                                type="button"
                                onClick={() => setOpenActionMenuId((current) => current === item.id ? null : item.id)}
                                className="rounded p-2 transition-colors hover:bg-gray-100"
                                title="More"
                              >
                                <MoreVertical className="h-4 w-4 text-gray-600" />
                              </button>
                              {openActionMenuId === item.id && (
                                <div className="absolute right-0 top-10 z-20 min-w-40 rounded-xl border border-gray-200 bg-white p-1 shadow-xl">
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setOpenActionMenuId(null);
                                      setEditingItem(buildEditableItem(item));
                                    }}
                                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-50"
                                  >
                                    <Edit className="h-4 w-4" />
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => openDeleteDialog(item)}
                                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50"
                                  >
                                    <Trash2 className="h-4 w-4" />
                                    Delete
                                  </button>
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>


          </div>
        )}
      </div>
    </>
  );
}
