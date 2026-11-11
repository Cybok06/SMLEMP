import { useEffect, useMemo, useState } from 'react';
import {
  X,
  Building2,
  Search,
  Plus,
  Minus,
  ChevronRight,
  ChevronLeft,
  Package,
  CheckCircle,
  FileText,
  Info,
  Eye,
  AlertTriangle,
  Loader2,
} from 'lucide-react';
import { useAccessSafe } from '../context/RoleAccessContext';

type UpdateType = 'add' | 'subtract';

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

type InventoryProductOption = {
  id: string;
  name: string;
  sku: string;
  category: string;
  brand: string;
  quantity: number;
  unitCost: number;
};

type StockUpdate = {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  category: string;
  brand: string;
  currentQuantity: number;
  updateType: UpdateType;
  quantityChanged: number;
  newQuantity: number;
  unitCost: number;
  valueImpact: number;
  notes: string;
};

type StockUpdateSessionReport = {
  sessionNumber: string;
  branch: string;
  warehouse: string;
  warehouseCode: string;
  reason: string;
  createdBy: string;
  createdAt: string;
  closedAt: string;
  updates: StockUpdate[];
  summary: {
    totalProductsUpdated: number;
    totalQuantityAdded: number;
    totalQuantitySubtracted: number;
    netQuantityChange: number;
    totalValueImpact: number;
  };
};

interface UpdateStockModalProps {
  onClose: () => void;
  branches: string[];
  locations: Record<string, WarehouseLocationOption[]>;
  onSessionClosed?: () => Promise<void> | void;
}

const UPDATE_REASONS = [
  'New stock received',
  'Manual correction',
  'Damaged stock removed',
  'Returned stock added',
  'Stock transfer adjustment',
  'Opening balance update',
  'Other',
];

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    const preview = raw.trim().slice(0, 180) || `HTTP ${response.status}`;
    throw new Error(preview);
  }
}

export function UpdateStockModal({
  onClose,
  branches,
  locations,
  onSessionClosed,
}: UpdateStockModalProps) {
  const { canViewPricing } = useAccessSafe();
  const [currentStep, setCurrentStep] = useState(1);
  const [selectedBranchId, setSelectedBranchId] = useState('');
  const [selectedWarehouseId, setSelectedWarehouseId] = useState('');
  const [updateReason, setUpdateReason] = useState('');
  const [customReason, setCustomReason] = useState('');
  const [productSearch, setProductSearch] = useState('');
  const [selectedProductId, setSelectedProductId] = useState('');
  const [updateType, setUpdateType] = useState<UpdateType>('add');
  const [quantityChange, setQuantityChange] = useState('');
  const [updateNote, setUpdateNote] = useState('');
  const [stockUpdates, setStockUpdates] = useState<StockUpdate[]>([]);
  const [availableProducts, setAvailableProducts] = useState<InventoryProductOption[]>([]);
  const [isLoadingProducts, setIsLoadingProducts] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [report, setReport] = useState<StockUpdateSessionReport | null>(null);

  const availableWarehouses = selectedBranchId ? locations[selectedBranchId] || [] : [];
  const selectedBranch = selectedBranchId || '';
  const selectedWarehouse = availableWarehouses.find((warehouse) => warehouse.id === selectedWarehouseId) || null;

  const pendingNetByProduct = useMemo(() => {
    const next: Record<string, number> = {};
    for (const update of stockUpdates) {
      next[update.productId] = (next[update.productId] || 0) + (update.updateType === 'add' ? update.quantityChanged : -update.quantityChanged);
    }
    return next;
  }, [stockUpdates]);

  const visibleProducts = useMemo(
    () =>
      availableProducts.map((product) => ({
        ...product,
        quantity: Math.max(0, product.quantity + (pendingNetByProduct[product.id] || 0)),
      })),
    [availableProducts, pendingNetByProduct]
  );

  const filteredProducts = useMemo(() => {
    const query = productSearch.trim().toLowerCase();
    if (!query) return [];
    return visibleProducts.filter(
      (product) =>
        product.name.toLowerCase().includes(query) ||
        product.sku.toLowerCase().includes(query) ||
        product.category.toLowerCase().includes(query) ||
        product.brand.toLowerCase().includes(query)
    );
  }, [productSearch, visibleProducts]);

  const selectedProduct = visibleProducts.find((product) => product.id === selectedProductId) || null;

  const resolvedReason = updateReason === 'Other' ? customReason.trim() : updateReason;
  const canProceedStep1 = Boolean(selectedBranchId && selectedWarehouseId && resolvedReason);
  const canProceedStep2 = stockUpdates.length > 0;

  useEffect(() => {
    setStockUpdates([]);
    setSelectedProductId('');
    setProductSearch('');
    setQuantityChange('');
    setUpdateNote('');
    setError('');
  }, [selectedBranchId, selectedWarehouseId]);

  useEffect(() => {
    if (!selectedBranchId || !selectedWarehouseId) {
      setAvailableProducts([]);
      return;
    }

    let isMounted = true;
    setIsLoadingProducts(true);
    setError('');

    fetch(`/api/inventory/stock-update/bootstrap?branch=${encodeURIComponent(selectedBranchId)}&locationId=${encodeURIComponent(selectedWarehouseId)}`, {
      credentials: 'same-origin',
    })
      .then((response) => parseJsonResponse<{ ok?: boolean; error?: string; products?: InventoryProductOption[] }>(response))
      .then((data) => {
        if (!isMounted) return;
        if (!data.ok) {
          throw new Error(data.error || 'Unable to load location products.');
        }
        setAvailableProducts(Array.isArray(data.products) ? data.products : []);
      })
      .catch((loadError: unknown) => {
        if (!isMounted) return;
        const message = loadError instanceof Error ? loadError.message : 'Unable to load location products.';
        setAvailableProducts([]);
        setError(message);
      })
      .finally(() => {
        if (isMounted) {
          setIsLoadingProducts(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [selectedBranchId, selectedWarehouseId]);

  const totalProductsUpdated = stockUpdates.length;
  const totalQuantityAdded = stockUpdates.filter((update) => update.updateType === 'add').reduce((sum, update) => sum + update.quantityChanged, 0);
  const totalQuantitySubtracted = stockUpdates.filter((update) => update.updateType === 'subtract').reduce((sum, update) => sum + update.quantityChanged, 0);
  const netQuantityChange = totalQuantityAdded - totalQuantitySubtracted;
  const totalValueImpact = stockUpdates.reduce((sum, update) => sum + update.valueImpact, 0);

  const handleAddUpdate = () => {
    if (!selectedProduct) return;

    const qty = parseInt(quantityChange, 10);
    if (!qty || qty <= 0) return;

    const newQty = updateType === 'add' ? selectedProduct.quantity + qty : selectedProduct.quantity - qty;
    if (newQty < 0) {
      setError('Cannot subtract more than the current quantity in this location.');
      return;
    }

    setError('');
    setStockUpdates((current) => [
      ...current,
      {
        id: `UPD-${Date.now()}-${current.length + 1}`,
        productId: selectedProduct.id,
        productName: selectedProduct.name,
        sku: selectedProduct.sku,
        category: selectedProduct.category,
        brand: selectedProduct.brand,
        currentQuantity: selectedProduct.quantity,
        updateType,
        quantityChanged: qty,
        newQuantity: newQty,
        unitCost: selectedProduct.unitCost,
        valueImpact: (updateType === 'add' ? qty : -qty) * selectedProduct.unitCost,
        notes: updateNote.trim(),
      },
    ]);
    setSelectedProductId('');
    setProductSearch('');
    setQuantityChange('');
    setUpdateNote('');
    setUpdateType('add');
  };

  const handleRemoveUpdate = (id: string) => {
    setStockUpdates((current) => current.filter((update) => update.id !== id));
  };

  const handleCloseSession = async () => {
    if (!selectedWarehouseId || !resolvedReason || stockUpdates.length === 0) return;

    setIsSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/stock-update-sessions', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          branch: selectedBranchId,
          locationId: selectedWarehouseId,
          reason: resolvedReason,
          updates: stockUpdates.map((update) => ({
            productId: update.productId,
            updateType: update.updateType,
            quantityChanged: update.quantityChanged,
            notes: update.notes,
          })),
        }),
      });
      const data = await parseJsonResponse<{ ok?: boolean; error?: string; session?: StockUpdateSessionReport }>(response);
      if (!response.ok || !data.ok || !data.session) {
        throw new Error(data.error || 'Unable to close stock update session.');
      }
      setReport(data.session);
      setCurrentStep(4);
      await onSessionClosed?.();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to close stock update session.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const steps = [
    { number: 1, label: 'Select Location', icon: Building2 },
    { number: 2, label: 'Add Updates', icon: Package },
    { number: 3, label: 'Review', icon: Eye },
    { number: 4, label: 'Report', icon: FileText },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-white">
        <div className="border-b border-gray-200 px-6 py-5">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-900">Update Stock Session</h2>
              <p className="mt-1 text-sm text-gray-600">
                {report ? `Session ID: ${report.sessionNumber}` : 'Controlled stock update with full audit trail'}
              </p>
            </div>
            <button onClick={onClose} className="rounded-lg p-2 transition-colors hover:bg-gray-100">
              <X className="h-5 w-5 text-gray-600" />
            </button>
          </div>

          <div className="mt-6 flex items-center justify-between">
            {steps.map((step, index) => {
              const Icon = step.icon;
              const isActive = currentStep === step.number;
              const isCompleted = currentStep > step.number;
              return (
                <div key={step.number} className="flex flex-1 items-center">
                  <div className="flex flex-1 flex-col items-center">
                    <div
                      className={`flex h-10 w-10 items-center justify-center rounded-full border-2 transition-colors ${
                        isCompleted
                          ? 'border-green-500 bg-green-500 text-white'
                          : isActive
                            ? 'border-indigo-600 bg-indigo-600 text-white'
                            : 'border-gray-300 bg-white text-gray-400'
                      }`}
                    >
                      {isCompleted ? <CheckCircle className="h-5 w-5" /> : <Icon className="h-5 w-5" />}
                    </div>
                    <span className={`mt-2 text-xs font-medium ${isActive ? 'text-indigo-600' : isCompleted ? 'text-green-600' : 'text-gray-500'}`}>
                      {step.label}
                    </span>
                  </div>
                  {index < steps.length - 1 && <div className={`mx-2 h-0.5 flex-1 ${isCompleted ? 'bg-green-500' : 'bg-gray-300'}`} />}
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {currentStep === 1 && (
            <div className="space-y-6">
              <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                <div className="flex items-start gap-3">
                  <Info className="mt-0.5 h-5 w-5 flex-shrink-0 text-blue-600" />
                  <div>
                    <h4 className="mb-1 font-medium text-blue-900">Select Stock Location</h4>
                    <p className="text-sm text-blue-800">
                      Choose the branch and warehouse or room where this stock update applies. All quantities in this session will be validated against that location.
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="mb-2 block text-sm font-medium text-gray-700">Branch <span className="text-red-500">*</span></label>
                  <select
                    value={selectedBranchId}
                    onChange={(event) => {
                      setSelectedBranchId(event.target.value);
                      setSelectedWarehouseId('');
                    }}
                    className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="">Select branch...</option>
                    {branches.map((branch) => (
                      <option key={branch} value={branch}>{branch}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="mb-2 block text-sm font-medium text-gray-700">Warehouse / Room <span className="text-red-500">*</span></label>
                  <select
                    value={selectedWarehouseId}
                    onChange={(event) => setSelectedWarehouseId(event.target.value)}
                    disabled={!selectedBranchId}
                    className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:cursor-not-allowed disabled:bg-gray-100"
                  >
                    <option value="">Select warehouse/room...</option>
                    {availableWarehouses.map((warehouse) => (
                      <option key={warehouse.id} value={warehouse.id} disabled={warehouse.status !== 'active'}>
                        {warehouse.name} ({warehouse.code}){warehouse.status !== 'active' ? ' - inactive' : ''}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700">Update Reason / Session Note <span className="text-red-500">*</span></label>
                <select
                  value={updateReason}
                  onChange={(event) => setUpdateReason(event.target.value)}
                  className="mb-3 w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="">Select reason...</option>
                  {UPDATE_REASONS.map((reason) => (
                    <option key={reason} value={reason}>{reason}</option>
                  ))}
                </select>
                {updateReason === 'Other' && (
                  <textarea
                    value={customReason}
                    onChange={(event) => setCustomReason(event.target.value)}
                    placeholder="Please specify the reason for this stock update..."
                    rows={3}
                    className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                )}
              </div>
            </div>
          )}

          {currentStep === 2 && (
            <div className="space-y-6">
              <div className="rounded-lg border border-gray-200 bg-white p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-gray-900">Location</h3>
                    <p className="text-sm text-gray-600">
                      {selectedBranch} {'>'} {selectedWarehouse?.name || ''}
                    </p>
                  </div>
                  <div className="text-sm text-gray-600">
                    Reason: <span className="font-medium text-gray-900">{resolvedReason}</span>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-gray-200 bg-white p-5">
                <h4 className="mb-4 font-semibold text-gray-900">Add Product Update</h4>

                <div className="space-y-4">
                  <div>
                    <label className="mb-2 block text-sm font-medium text-gray-700">Search Product</label>
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
                      <input
                        type="text"
                        placeholder="Search by name, SKU, category, or brand..."
                        value={productSearch}
                        onChange={(event) => setProductSearch(event.target.value)}
                        className="w-full rounded-lg border border-gray-300 py-2.5 pl-10 pr-4 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>

                    {isLoadingProducts && (
                      <div className="mt-3 flex items-center gap-2 text-sm text-gray-600">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading location products...
                      </div>
                    )}

                    {productSearch && !isLoadingProducts && (
                      <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-gray-200">
                        {filteredProducts.length > 0 ? filteredProducts.map((product) => (
                          <button
                            key={product.id}
                            onClick={() => {
                              setSelectedProductId(product.id);
                              setProductSearch('');
                            }}
                            className="w-full border-b border-gray-100 px-4 py-3 text-left transition-colors last:border-0 hover:bg-gray-50"
                          >
                            <div className="font-medium text-gray-900">{product.name}</div>
                            <div className="text-sm text-gray-600">
                              {product.sku} • {product.category} • Current: {product.quantity}
                            </div>
                          </button>
                        )) : (
                          <div className="px-4 py-3 text-sm text-gray-500">No products match that search.</div>
                        )}
                      </div>
                    )}
                  </div>

                  {selectedProduct && (
                    <>
                      <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                        <div className="flex items-center justify-between">
                          <div>
                            <div className="font-medium text-gray-900">{selectedProduct.name}</div>
                            <div className="text-sm text-gray-600">
                              {selectedProduct.sku} • Current Qty: <span className="font-semibold">{selectedProduct.quantity}</span>
                            </div>
                          </div>
                          <button onClick={() => setSelectedProductId('')} className="text-gray-500 hover:text-gray-700">
                            <X className="h-5 w-5" />
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-3 gap-4">
                        <div>
                          <label className="mb-2 block text-sm font-medium text-gray-700">Update Type</label>
                          <div className="flex gap-2">
                            <button
                              onClick={() => setUpdateType('add')}
                              className={`flex-1 rounded-lg border-2 px-4 py-2.5 transition-colors ${updateType === 'add' ? 'border-green-500 bg-green-50 text-green-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}
                            >
                              <span className="flex items-center justify-center gap-2"><Plus className="h-4 w-4" />Add</span>
                            </button>
                            <button
                              onClick={() => setUpdateType('subtract')}
                              className={`flex-1 rounded-lg border-2 px-4 py-2.5 transition-colors ${updateType === 'subtract' ? 'border-red-500 bg-red-50 text-red-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}
                            >
                              <span className="flex items-center justify-center gap-2"><Minus className="h-4 w-4" />Subtract</span>
                            </button>
                          </div>
                        </div>

                        <div>
                          <label className="mb-2 block text-sm font-medium text-gray-700">Quantity</label>
                          <input
                            type="number"
                            min="1"
                            value={quantityChange}
                            onChange={(event) => setQuantityChange(event.target.value)}
                            placeholder="0"
                            className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>

                        <div>
                          <label className="mb-2 block text-sm font-medium text-gray-700">New Quantity</label>
                          <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5">
                            <span className="font-semibold text-gray-900">
                              {quantityChange && parseInt(quantityChange, 10) > 0
                                ? updateType === 'add'
                                  ? selectedProduct.quantity + parseInt(quantityChange, 10)
                                  : selectedProduct.quantity - parseInt(quantityChange, 10)
                                : selectedProduct.quantity}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div>
                        <label className="mb-2 block text-sm font-medium text-gray-700">Note (Optional)</label>
                        <input
                          type="text"
                          value={updateNote}
                          onChange={(event) => setUpdateNote(event.target.value)}
                          placeholder="Add a note for this update..."
                          className="w-full rounded-lg border border-gray-300 px-4 py-2.5 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                      </div>

                      <button
                        onClick={handleAddUpdate}
                        disabled={!quantityChange || parseInt(quantityChange, 10) <= 0}
                        className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                      >
                        <Plus className="h-4 w-4" />
                        Add to Update List
                      </button>
                    </>
                  )}
                </div>
              </div>

              {stockUpdates.length > 0 && (
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                  <div className="border-b border-gray-200 bg-gray-50 px-5 py-3">
                    <h4 className="font-semibold text-gray-900">Updates ({stockUpdates.length})</h4>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead className="border-b border-gray-200 bg-gray-50">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Product</th>
                          <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Current</th>
                          <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Action</th>
                          <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Change</th>
                          <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">New Qty</th>
                          {canViewPricing && (
                            <>
                              <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Unit Cost</th>
                              <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Value Impact</th>
                            </>
                          )}
                          <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Note</th>
                          <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {stockUpdates.map((update) => (
                          <tr key={update.id} className="hover:bg-gray-50">
                            <td className="px-4 py-3">
                              <div className="font-medium text-gray-900">{update.productName}</div>
                              <div className="text-sm text-gray-500">{update.sku}</div>
                            </td>
                            <td className="px-4 py-3 text-center font-semibold text-gray-900">{update.currentQuantity}</td>
                            <td className="px-4 py-3 text-center">
                              <span className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium ${update.updateType === 'add' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                                {update.updateType === 'add' ? <Plus className="h-3 w-3" /> : <Minus className="h-3 w-3" />}
                                {update.updateType}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-center font-semibold text-gray-900">{update.quantityChanged}</td>
                            <td className="px-4 py-3 text-center font-semibold text-indigo-600">{update.newQuantity}</td>
                            {canViewPricing && (
                              <>
                                <td className="px-4 py-3 text-right text-sm text-gray-900">GHS {update.unitCost.toLocaleString()}</td>
                                <td className={`px-4 py-3 text-right font-semibold ${update.valueImpact >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {update.valueImpact > 0 ? '+' : ''}GHS {update.valueImpact.toLocaleString()}
                                </td>
                              </>
                            )}
                            <td className="px-4 py-3 text-sm text-gray-600">{update.notes || '-'}</td>
                            <td className="px-4 py-3 text-center">
                              <button onClick={() => handleRemoveUpdate(update.id)} className="rounded p-1 transition-colors hover:bg-red-50">
                                <X className="h-4 w-4 text-red-600" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {currentStep === 3 && (
            <div className="space-y-6">
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600" />
                  <div>
                    <h4 className="mb-1 font-medium text-amber-900">Review Before Closing</h4>
                    <p className="text-sm text-amber-800">
                      Closing this session writes the stock movements to inventory and updates the selected location totals immediately.
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-4">
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-gray-900">{totalProductsUpdated}</div>
                  <div className="mt-1 text-sm text-gray-600">Products Updated</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-green-600">+{totalQuantityAdded}</div>
                  <div className="mt-1 text-sm text-gray-600">Total Added</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-red-600">-{totalQuantitySubtracted}</div>
                  <div className="mt-1 text-sm text-gray-600">Total Subtracted</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className={`text-2xl font-bold ${netQuantityChange >= 0 ? 'text-indigo-600' : 'text-red-600'}`}>
                    {netQuantityChange > 0 ? '+' : ''}{netQuantityChange}
                  </div>
                  <div className="mt-1 text-sm text-gray-600">Net Change</div>
                </div>
              </div>

              <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                <div className="border-b border-gray-200 bg-gray-50 px-5 py-3">
                  <h4 className="font-semibold text-gray-900">Session Updates</h4>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="border-b border-gray-200 bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Product</th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">SKU</th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Current</th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">Change</th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase text-gray-500">New Qty</th>
                        {canViewPricing && (
                          <>
                            <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Unit Cost</th>
                            <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Value Impact</th>
                          </>
                        )}
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Note</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {stockUpdates.map((update) => (
                        <tr key={update.id}>
                          <td className="px-4 py-3 font-medium text-gray-900">{update.productName}</td>
                          <td className="px-4 py-3 text-center text-sm text-gray-600">{update.sku}</td>
                          <td className="px-4 py-3 text-center text-gray-900">{update.currentQuantity}</td>
                          <td className={`px-4 py-3 text-center font-semibold ${update.updateType === 'add' ? 'text-green-600' : 'text-red-600'}`}>
                            {update.updateType === 'add' ? '+' : '-'}{update.quantityChanged}
                          </td>
                          <td className="px-4 py-3 text-center font-semibold text-indigo-600">{update.newQuantity}</td>
                          {canViewPricing && (
                            <>
                              <td className="px-4 py-3 text-right text-sm text-gray-900">GHS {update.unitCost.toLocaleString()}</td>
                              <td className={`px-4 py-3 text-right font-semibold ${update.valueImpact >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                {update.valueImpact > 0 ? '+' : ''}GHS {update.valueImpact.toLocaleString()}
                              </td>
                            </>
                          )}
                          <td className="px-4 py-3 text-sm text-gray-600">{update.notes || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {currentStep === 4 && report && (
            <div className="space-y-6">
              <div className="rounded-lg border border-green-200 bg-green-50 p-5">
                <div className="flex items-start gap-3">
                  <CheckCircle className="mt-0.5 h-6 w-6 flex-shrink-0 text-green-600" />
                  <div>
                    <h3 className="font-semibold text-green-900">Stock session closed successfully</h3>
                    <p className="mt-1 text-sm text-green-800">
                      Inventory has been updated for {report.branch} {'>'} {report.warehouse}. Session ID: <span className="font-mono font-semibold">{report.sessionNumber}</span>
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 rounded-lg border border-gray-200 bg-white p-5">
                <div>
                  <div className="text-xs uppercase text-gray-500">Branch</div>
                  <div className="mt-1 font-medium text-gray-900">{report.branch}</div>
                </div>
                <div>
                  <div className="text-xs uppercase text-gray-500">Warehouse / Room</div>
                  <div className="mt-1 font-medium text-gray-900">{report.warehouse} ({report.warehouseCode})</div>
                </div>
                <div>
                  <div className="text-xs uppercase text-gray-500">Reason</div>
                  <div className="mt-1 font-medium text-gray-900">{report.reason}</div>
                </div>
                <div>
                  <div className="text-xs uppercase text-gray-500">Closed By</div>
                  <div className="mt-1 font-medium text-gray-900">{report.createdBy}</div>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-4">
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-gray-900">{report.summary.totalProductsUpdated}</div>
                  <div className="mt-1 text-sm text-gray-600">Products Updated</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-green-600">+{report.summary.totalQuantityAdded}</div>
                  <div className="mt-1 text-sm text-gray-600">Total Added</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="text-2xl font-bold text-red-600">-{report.summary.totalQuantitySubtracted}</div>
                  <div className="mt-1 text-sm text-gray-600">Total Subtracted</div>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className={`text-2xl font-bold ${report.summary.netQuantityChange >= 0 ? 'text-indigo-600' : 'text-red-600'}`}>
                    {report.summary.netQuantityChange > 0 ? '+' : ''}{report.summary.netQuantityChange}
                  </div>
                  <div className="mt-1 text-sm text-gray-600">Net Change</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-6 py-4">
          <div>
            {currentStep < 4 && (
              <button
                onClick={() => setCurrentStep(Math.max(1, currentStep - 1))}
                disabled={currentStep === 1 || isSubmitting}
                className="flex items-center gap-2 rounded-lg px-4 py-2 text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ChevronLeft className="h-4 w-4" />
                Back
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            {currentStep === 1 && (
              <button
                onClick={() => setCurrentStep(2)}
                disabled={!canProceedStep1}
                className="flex items-center gap-2 rounded-lg bg-indigo-600 px-6 py-2 text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                Next: Add Updates
                <ChevronRight className="h-4 w-4" />
              </button>
            )}

            {currentStep === 2 && (
              <button
                onClick={() => setCurrentStep(3)}
                disabled={!canProceedStep2}
                className="flex items-center gap-2 rounded-lg bg-indigo-600 px-6 py-2 text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                Next: Review
                <ChevronRight className="h-4 w-4" />
              </button>
            )}

            {currentStep === 3 && (
              <button
                onClick={() => void handleCloseSession()}
                disabled={isSubmitting}
                className="flex items-center gap-2 rounded-lg bg-green-600 px-6 py-2 text-white transition-colors hover:bg-green-700 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle className="h-4 w-4" />}
                Close Session
              </button>
            )}

            {currentStep === 4 && (
              <button onClick={onClose} className="rounded-lg bg-indigo-600 px-6 py-2 text-white transition-colors hover:bg-indigo-700">
                Done
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
