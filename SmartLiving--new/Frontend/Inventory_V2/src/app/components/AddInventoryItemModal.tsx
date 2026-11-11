import { useState, useRef, useCallback, useEffect } from 'react';
import {
  X,
  Upload,
  ImageIcon,
  Package,
  DollarSign,
  MapPin,
  Calendar,
  Bell,
  Check,
  AlertTriangle,
  Info,
  ChevronDown,
  ChevronUp,
  Minus,
  Plus,
  Trash2,
  CheckSquare,
  Square,
  TrendingUp
} from 'lucide-react';
import { toast } from 'sonner';

interface AddInventoryItemModalProps {
  onClose: () => void;
  onSave: (item: InventoryItemPayload) => Promise<void> | void;
  branches: string[];
  locations: Record<string, WarehouseLocationOption[]>;
  categories: string[];
  onAddCategory: (category: string) => void;
  mode?: 'create' | 'edit';
  initialItem?: EditableInventoryItem | null;
}

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

export interface InventoryItemPayload {
  id?: string;
  name: string;
  description: string;
  imageUrl: string;
  imageId?: string;
  quantity: number;
  expiryDate: string;
  reminderDays: number;
  costPrice: number;
  sellingPrice: number;
  installmentPrice: number | null;
  wholesalePrice: number | null;
  branches: string[];
  stockAssignments: {
    branch: string;
    locationId: string;
  }[];
  category: string;
  brand: string;
}

export interface EditableInventoryItem {
  id: string;
  name: string;
  description: string;
  category: string;
  brand: string;
  imageUrl: string;
  imageId?: string;
  quantity: number;
  expiryDate: string;
  reminderDays: number;
  costPrice: number;
  sellingPrice: number;
  installmentPrice: number | null;
  wholesalePrice: number | null;
  branches: string[];
  stockAssignments: {
    branch: string;
    locationId: string;
  }[];
}

type FormErrors = Record<string, string>;

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();

  try {
    return JSON.parse(raw) as T;
  } catch {
    const preview = raw.trim().slice(0, 140) || `HTTP ${response.status}`;
    throw new Error(preview);
  }
}

function Tooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex items-center ml-1.5">
      <button
        type="button"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        className="text-gray-400 hover:text-indigo-500 transition-colors"
      >
        <Info className="w-3.5 h-3.5" />
      </button>
      {show && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50 w-52 px-3 py-2 bg-gray-900 text-white text-xs rounded-lg shadow-xl pointer-events-none">
          {text}
          <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-gray-900" />
        </div>
      )}
    </span>
  );
}

export function AddInventoryItemModal({
  onClose,
  onSave,
  branches,
  locations,
  categories,
  onAddCategory,
  mode = 'create',
  initialItem = null,
}: AddInventoryItemModalProps) {
  const isEditMode = mode === 'edit';
  // Section 1 — Basic Info
  const [productName, setProductName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [brand, setBrand] = useState('');
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [uploadedImageUrl, setUploadedImageUrl] = useState('');
  const [uploadedImageId, setUploadedImageId] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewObjectUrlRef = useRef<string | null>(null);

  // Section 2 — Stock Info
  const [quantity, setQuantity] = useState('0');
  const [expiryDate, setExpiryDate] = useState('');
  const [reminderDays, setReminderDays] = useState('7');

  // Section 3 — Pricing
  const [costPrice, setCostPrice] = useState('');
  const [sellingPrice, setSellingPrice] = useState('');
  const [installmentPrice, setInstallmentPrice] = useState('');
  const [wholesalePrice, setWholesalePrice] = useState('');
  const [showAdvancedPricing, setShowAdvancedPricing] = useState(false);

  // Section 4 — Distribution
  const [selectedBranches, setSelectedBranches] = useState<string[]>([]);
  const [selectedLocations, setSelectedLocations] = useState<Record<string, string>>({});
  const [branchSearch, setBranchSearch] = useState('');

  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Computed pricing values
  const cost = parseFloat(costPrice) || 0;
  const sell = parseFloat(sellingPrice) || 0;
  const margin = sell - cost;
  const marginPct = cost > 0 ? ((sell - cost) / cost) * 100 : 0;
  const isProfitable = margin >= 0;
  const hasPricingWarning = sellingPrice !== '' && costPrice !== '' && sell < cost;

  // Image handling
  const clearImage = useCallback(() => {
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
      previewObjectUrlRef.current = null;
    }
    setImagePreviewUrl(null);
    setUploadedImageUrl('');
    setUploadedImageId('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, []);

  useEffect(() => {
    return () => {
      if (previewObjectUrlRef.current) {
        URL.revokeObjectURL(previewObjectUrlRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!initialItem) {
      return;
    }

    setProductName(initialItem.name || '');
    setDescription(initialItem.description || '');
    setCategory(initialItem.category || '');
    setBrand(initialItem.brand || '');
    setImagePreviewUrl(initialItem.imageUrl || null);
    setUploadedImageUrl(initialItem.imageUrl || '');
    setUploadedImageId(initialItem.imageId || '');
    setQuantity(String(initialItem.quantity ?? 0));
    setExpiryDate(initialItem.expiryDate || '');
    setReminderDays(String(initialItem.reminderDays || 7));
    setCostPrice(initialItem.costPrice > 0 ? String(initialItem.costPrice) : '');
    setSellingPrice(initialItem.sellingPrice > 0 ? String(initialItem.sellingPrice) : '');
    setInstallmentPrice(initialItem.installmentPrice != null ? String(initialItem.installmentPrice) : '');
    setWholesalePrice(initialItem.wholesalePrice != null ? String(initialItem.wholesalePrice) : '');
    setSelectedBranches(initialItem.branches || []);
    setSelectedLocations(
      (initialItem.stockAssignments || []).reduce<Record<string, string>>((acc, assignment) => {
        if (assignment.branch && assignment.locationId) {
          acc[assignment.branch] = assignment.locationId;
        }
        return acc;
      }, {})
    );
    setShowAdvancedPricing(
      initialItem.installmentPrice != null || initialItem.wholesalePrice != null
    );
  }, [initialItem]);

  const handleImageFile = useCallback(async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setErrors(prev => ({ ...prev, image: 'Please upload a valid image file (JPG, PNG, WebP).' }));
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setErrors(prev => ({ ...prev, image: 'Image must be under 5MB.' }));
      return;
    }
    setErrors(prev => { const e = { ...prev }; delete e.image; return e; });
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
    }
    const localPreviewUrl = URL.createObjectURL(file);
    previewObjectUrlRef.current = localPreviewUrl;
    setImagePreviewUrl(localPreviewUrl);
    setUploadedImageUrl('');
    setUploadedImageId('');
    setIsUploadingImage(true);
    try {
      const formData = new FormData();
      formData.append('image', file);

      const response = await fetch('/products/upload_image?variant=public', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData,
      });
      const data = await parseJsonResponse<{
        success?: boolean;
        error?: string;
        image_url?: string;
        image_id?: string;
      }>(response);

      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Image upload failed.');
      }

      if (!data.image_url) {
        throw new Error('Image upload completed without an image URL.');
      }

      setUploadedImageUrl(data.image_url);
      setUploadedImageId(data.image_id || '');
      toast.success('Image uploaded successfully.');
    } catch (error) {
      clearImage();
      const message = error instanceof Error ? error.message : 'Image upload failed.';
      setErrors(prev => ({ ...prev, image: message }));
      toast.error(message);
    } finally {
      setIsUploadingImage(false);
    }
  }, [clearImage]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) void handleImageFile(file);
  }, [handleImageFile]);

  // Branch selection
  const toggleBranch = (branch: string) => {
    if (isEditMode) return;
    setSelectedBranches(prev => {
      if (prev.includes(branch)) {
        setSelectedLocations((current) => {
          const next = { ...current };
          delete next[branch];
          return next;
        });
        return prev.filter(b => b !== branch);
      }
      return [...prev, branch];
    });
  };

  const toggleAllBranches = () => {
    if (isEditMode) return;
    if (selectedBranches.length === branches.length) {
      setSelectedBranches([]);
      setSelectedLocations({});
    } else {
      setSelectedBranches([...branches]);
    }
  };

  const removeBranch = (branch: string) => {
    if (isEditMode) return;
    setSelectedBranches(prev => prev.filter(b => b !== branch));
    setSelectedLocations((current) => {
      const next = { ...current };
      delete next[branch];
      return next;
    });
  };

  const filteredBranches = branches.filter(b =>
    b.toLowerCase().includes(branchSearch.toLowerCase())
  );
  const activeLocationMap = selectedBranches.reduce<Record<string, WarehouseLocationOption[]>>((acc, branch) => {
    acc[branch] = (locations[branch] || []).filter((location) => location.status === 'active');
    return acc;
  }, {});

  const handleAddCategory = () => {
    const trimmed = newCategory.trim();
    if (!trimmed) {
      setErrors(prev => ({ ...prev, newCategory: 'Enter a category name first.' }));
      return;
    }

    const existingCategory = categories.find(c => c.toLowerCase() === trimmed.toLowerCase());
    const categoryToUse = existingCategory || trimmed;
    if (!existingCategory) {
      onAddCategory(trimmed);
      toast.success(`"${trimmed}" added to categories.`);
    } else {
      toast.info(`"${existingCategory}" is already available.`);
    }

    setCategory(categoryToUse);
    setNewCategory('');
    setErrors(prev => {
      const next = { ...prev };
      delete next.category;
      delete next.newCategory;
      return next;
    });
  };

  // Quantity stepper
  const incrementQty = () => setQuantity(prev => String(Math.max(0, (parseInt(prev) || 0) + 1)));
  const decrementQty = () => setQuantity(prev => String(Math.max(0, (parseInt(prev) || 0) - 1)));

  // Validation
  const validate = (): boolean => {
    const newErrors: FormErrors = {};
    if (!productName.trim()) newErrors.productName = 'Product name is required.';
    if (!imagePreviewUrl) newErrors.image = 'Please upload a product image to continue.';
    if (imagePreviewUrl && !uploadedImageUrl) newErrors.image = 'Please wait for the image upload to finish.';
    if (!costPrice || cost <= 0) newErrors.costPrice = 'Cost price must be greater than 0.';
    if (!sellingPrice || sell <= 0) newErrors.sellingPrice = 'Selling price must be greater than 0.';
    if (!category) newErrors.category = 'Please select a category.';
    if (selectedBranches.length === 0) newErrors.branches = 'Select at least one branch.';
    const missingLocationBranch = selectedBranches.find((branch) => !selectedLocations[branch]);
    if (missingLocationBranch) newErrors.locations = `Select a warehouse or location for ${missingLocationBranch}.`;
    if (quantity === '' || Number.isNaN(parseInt(quantity)) || parseInt(quantity) < 0) {
      newErrors.quantity = 'Quantity cannot be less than 0.';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) {
      toast.error('Please fix the errors before submitting.');
      return;
    }
    setIsSubmitting(true);
    const payload: InventoryItemPayload = {
      id: initialItem?.id,
      name: productName,
      description,
      imageUrl: uploadedImageUrl,
      imageId: uploadedImageId || undefined,
      quantity: parseInt(quantity),
      expiryDate,
      reminderDays: parseInt(reminderDays) || 7,
      costPrice: cost,
      sellingPrice: sell,
      installmentPrice: installmentPrice ? parseFloat(installmentPrice) : null,
      wholesalePrice: wholesalePrice ? parseFloat(wholesalePrice) : null,
      branches: isEditMode ? (initialItem?.branches || selectedBranches) : selectedBranches,
      stockAssignments: isEditMode
        ? (initialItem?.stockAssignments || [])
        : selectedBranches.map((branch) => ({
            branch,
            locationId: selectedLocations[branch],
          })),
      category,
      brand,
    };
    try {
      await onSave(payload);
      toast.success(
        isEditMode
          ? `"${productName}" updated successfully.`
          : `"${productName}" added to inventory successfully!`
      );
      onClose();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to save inventory item.';
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const canSubmit = !!uploadedImageUrl && !!productName.trim() && !!costPrice && !!sellingPrice && selectedBranches.length > 0 && selectedBranches.every((branch) => Boolean(selectedLocations[branch])) && !isSubmitting && !isUploadingImage;

  const sectionHeader = (icon: React.ReactNode, title: string, subtitle: string, color: string) => (
    <div className={`flex items-start gap-3 mb-5 pb-4 border-b border-gray-100`}>
      <div className={`p-2 rounded-lg ${color}`}>{icon}</div>
      <div>
        <h3 className="font-semibold text-gray-900">{title}</h3>
        <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div
        className="flex-1 bg-black bg-opacity-50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Drawer Panel */}
      <div className="w-full max-w-2xl bg-white flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-gray-100 bg-white sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-50 rounded-lg">
              <Package className="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h2 className="font-semibold text-gray-900">{isEditMode ? 'Edit Inventory Item' : 'Add Inventory Item'}</h2>
              <p className="text-xs text-gray-500">
                {isEditMode ? 'Update product details and pricing using the same inventory form' : 'Fill in all required fields to add a new item'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors text-gray-500 hover:text-gray-700"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Step progress */}
        <div className="flex items-center gap-0 px-6 py-3 bg-gray-50 border-b border-gray-100 text-xs">
          {[
            { label: 'Basic Info', color: 'bg-blue-500' },
            { label: 'Stock', color: 'bg-green-500' },
            { label: 'Pricing', color: 'bg-yellow-500' },
            { label: 'Distribution', color: 'bg-purple-500' },
          ].map((step, i) => (
            <div key={step.label} className="flex items-center gap-0">
              <div className="flex items-center gap-1.5">
                <div className={`w-2 h-2 rounded-full ${step.color}`} />
                <span className="text-gray-600">{step.label}</span>
              </div>
              {i < 3 && <div className="w-6 h-px bg-gray-300 mx-2" />}
            </div>
          ))}
        </div>

        {/* Scrollable Form Body */}
        <div className="flex-1 overflow-y-auto">
          <div className="p-6 space-y-6">

            {/* ─── SECTION 1: BASIC INFORMATION ─── */}
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              {sectionHeader(
                <ImageIcon className="w-4 h-4 text-blue-600" />,
                'Basic Information',
                'Identify the product with a name, description, and image',
                'bg-blue-50'
              )}
              <div className="space-y-4">
                {/* Product Name */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Product Name <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Samsung 65″ Smart TV"
                    value={productName}
                    onChange={e => { setProductName(e.target.value); if (errors.productName) setErrors(p => { const n = {...p}; delete n.productName; return n; }); }}
                    className={`w-full px-4 py-2.5 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm ${errors.productName ? 'border-red-400 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}
                  />
                  {errors.productName && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.productName}</p>}
                </div>

                {/* Category */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Category <span className="text-red-500">*</span>
                  </label>
                  <select
                    value={category}
                    onChange={e => { setCategory(e.target.value); if (errors.category) setErrors(p => { const n = {...p}; delete n.category; return n; }); }}
                    className={`w-full px-4 py-2.5 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm bg-white ${errors.category ? 'border-red-400 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}
                  >
                    <option value="">Select a category...</option>
                    {categories.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <div className="mt-2 flex items-start gap-2">
                    <div className="flex-1">
                      <input
                        type="text"
                        placeholder="Add a new category..."
                        value={newCategory}
                        onChange={e => {
                          setNewCategory(e.target.value);
                          if (errors.newCategory) setErrors(p => { const n = {...p}; delete n.newCategory; return n; });
                        }}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleAddCategory();
                          }
                        }}
                        className={`w-full px-4 py-2.5 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm ${errors.newCategory ? 'border-red-400 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}
                      />
                      {errors.newCategory && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.newCategory}</p>}
                    </div>
                    <button
                      type="button"
                      onClick={handleAddCategory}
                      className="flex items-center gap-1.5 rounded-lg border border-indigo-200 px-3 py-2.5 text-sm font-medium text-indigo-600 transition-colors hover:bg-indigo-50 hover:text-indigo-700"
                    >
                      <Plus className="h-4 w-4" />
                      Add New
                    </button>
                  </div>
                  {errors.category && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.category}</p>}
                </div>

                {/* Brand */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Brand
                    <span className="text-gray-400 font-normal ml-2 text-xs">(optional)</span>
                    <Tooltip text="Enter the brand or manufacturer name, e.g., Samsung, LG, Roch, Hisense, etc." />
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Samsung, LG, Roch, Hisense..."
                    value={brand}
                    onChange={e => setBrand(e.target.value)}
                    className="w-full px-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm"
                  />
                </div>

                {/* Description */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Description</label>
                  <textarea
                    placeholder="Briefly describe the product — model, features, specs..."
                    value={description}
                    onChange={e => setDescription(e.target.value)}
                    rows={3}
                    className="w-full px-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm resize-none"
                  />
                </div>

                {/* Image Upload */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Product Image <span className="text-red-500">*</span>
                    <Tooltip text="Upload a clear product photo. Supported: JPG, PNG, WebP. Max 5MB. Required before saving." />
                  </label>

                  {imagePreviewUrl ? (
                    <div className="relative group rounded-xl overflow-hidden border-2 border-indigo-200 bg-gray-50">
                      <img
                        src={imagePreviewUrl}
                        alt="Preview"
                        className="w-full h-48 object-contain bg-white"
                      />
                      <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-center justify-center gap-3">
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          className="opacity-0 group-hover:opacity-100 transition-opacity px-3 py-2 bg-white text-gray-800 text-xs rounded-lg shadow font-medium hover:bg-gray-50"
                        >
                          Change Image
                        </button>
                        <button
                          type="button"
                          onClick={clearImage}
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-2 bg-red-500 text-white rounded-lg shadow hover:bg-red-600"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div className="absolute top-3 right-3">
                        <span className={`flex items-center gap-1 px-2 py-1 text-white text-xs rounded-full font-medium ${uploadedImageUrl ? 'bg-green-500' : 'bg-amber-500'}`}>
                          {uploadedImageUrl ? <Check className="w-3 h-3" /> : <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
                          {uploadedImageUrl ? 'Uploaded' : 'Uploading'}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div
                      onDragOver={e => { e.preventDefault(); setIsDragOver(true); }}
                      onDragLeave={() => setIsDragOver(false)}
                      onDrop={handleDrop}
                      onClick={() => fileInputRef.current?.click()}
                      className={`relative flex flex-col items-center justify-center h-40 border-2 border-dashed rounded-xl cursor-pointer transition-all ${
                        isDragOver
                          ? 'border-indigo-500 bg-indigo-50'
                          : errors.image
                          ? 'border-red-400 bg-red-50'
                          : 'border-gray-300 hover:border-indigo-400 hover:bg-indigo-50 bg-gray-50'
                      }`}
                    >
                      <Upload className={`w-8 h-8 mb-2 ${isDragOver ? 'text-indigo-500' : 'text-gray-400'}`} />
                      <p className="text-sm font-medium text-gray-700">
                        {isUploadingImage ? 'Uploading to Cloudflare...' : isDragOver ? 'Drop your image here' : 'Click or drag & drop to upload'}
                      </p>
                      <p className="text-xs text-gray-400 mt-1">JPG, PNG, WebP — Max 5MB</p>
                    </div>
                  )}

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={e => { const f = e.target.files?.[0]; if (f) void handleImageFile(f); }}
                  />
                  {errors.image && (
                    <p className="text-xs text-red-500 mt-1.5 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" />{errors.image}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* ─── SECTION 2: STOCK INFORMATION ─── */}
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              {sectionHeader(
                <Package className="w-4 h-4 text-green-600" />,
                'Stock Information',
                'Set quantity levels and expiry tracking for this item',
                'bg-green-50'
              )}
              <div className="space-y-4">
                {/* Quantity */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Quantity <span className="text-red-500">*</span>
                  </label>
                  <div className="flex items-center gap-0 w-fit border border-gray-200 rounded-lg overflow-hidden hover:border-gray-300 transition-colors">
                    <button
                      type="button"
                      onClick={decrementQty}
                      className="px-3 py-2.5 hover:bg-gray-100 transition-colors text-gray-600 border-r border-gray-200"
                    >
                      <Minus className="w-4 h-4" />
                    </button>
                    <input
                      type="number"
                      min="0"
                      value={quantity}
                      onChange={e => setQuantity(e.target.value)}
                      readOnly={isEditMode}
                      className="w-20 px-3 py-2.5 text-center text-sm font-medium text-gray-900 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-500 border-none"
                    />
                    <button
                      type="button"
                      onClick={incrementQty}
                      className="px-3 py-2.5 hover:bg-gray-100 transition-colors text-gray-600 border-l border-gray-200"
                    >
                      <Plus className="w-4 h-4" />
                    </button>
                  </div>
                  {errors.quantity && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.quantity}</p>}
                  {isEditMode && (
                    <p className="mt-1 text-xs text-gray-500">
                      Quantity stays locked here. Use stock update sessions to change physical stock counts safely.
                    </p>
                  )}
                </div>

                {/* Expiry Date */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Expiry Date
                    <span className="text-gray-400 font-normal ml-2 text-xs">(optional)</span>
                    <Tooltip text="Only needed for perishable goods, consumables, or items with a shelf life." />
                  </label>
                  <p className="text-xs text-gray-400 mb-2 flex items-center gap-1">
                    <Info className="w-3 h-3" />
                    Leave empty for non-perishable items
                  </p>
                  <div className="relative">
                    <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="date"
                      value={expiryDate}
                      onChange={e => setExpiryDate(e.target.value)}
                      className="w-full pl-9 pr-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm"
                    />
                  </div>
                  {expiryDate && (
                    <div className="mt-2 flex items-center gap-2">
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-amber-50 text-amber-700 border border-amber-200 rounded-full text-xs font-medium">
                        <Bell className="w-3 h-3" /> Expiring Soon badge will appear when due
                      </span>
                    </div>
                  )}
                </div>

                {/* Reminder Window */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Reminder Window
                    <Tooltip text="Number of days before expiry to trigger a low-stock or expiry alert notification." />
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      type="number"
                      min="1"
                      max="365"
                      value={reminderDays}
                      onChange={e => setReminderDays(e.target.value)}
                      className="w-28 px-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm"
                    />
                    <span className="text-sm text-gray-500">days before expiry</span>
                  </div>
                </div>
              </div>
            </div>

            {/* ─── SECTION 3: PRICING ─── */}
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              {sectionHeader(
                <DollarSign className="w-4 h-4 text-yellow-600" />,
                'Pricing',
                'Set cost, selling price, and view profit margin instantly',
                'bg-yellow-50'
              )}

              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  {/* Cost Price */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                      Cost Price (GHS) <span className="text-red-500">*</span>
                      <Tooltip text="The price you pay to acquire or produce this item. Used to calculate your profit margin." />
                    </label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">₵</span>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="0.00"
                        value={costPrice}
                        onChange={e => { setCostPrice(e.target.value); if (errors.costPrice) setErrors(p => { const n = {...p}; delete n.costPrice; return n; }); }}
                        className={`w-full pl-7 pr-4 py-2.5 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm ${errors.costPrice ? 'border-red-400 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}
                      />
                    </div>
                    {errors.costPrice && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.costPrice}</p>}
                  </div>

                  {/* Selling Price */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                      Selling Price (GHS) <span className="text-red-500">*</span>
                      <Tooltip text="The price your customers pay. Should be higher than cost price to make profit." />
                    </label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">₵</span>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="0.00"
                        value={sellingPrice}
                        onChange={e => { setSellingPrice(e.target.value); if (errors.sellingPrice) setErrors(p => { const n = {...p}; delete n.sellingPrice; return n; }); }}
                        className={`w-full pl-7 pr-4 py-2.5 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm ${errors.sellingPrice ? 'border-red-400 bg-red-50' : hasPricingWarning ? 'border-red-400 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}
                      />
                    </div>
                    {errors.sellingPrice && <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.sellingPrice}</p>}
                  </div>
                </div>

                {/* Live Margin Display */}
                {costPrice && sellingPrice && (
                  <div className={`rounded-xl p-4 border-2 transition-all ${
                    hasPricingWarning
                      ? 'border-red-300 bg-red-50'
                      : 'border-green-300 bg-green-50'
                  }`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {hasPricingWarning ? (
                          <AlertTriangle className="w-4 h-4 text-red-500" />
                        ) : (
                          <TrendingUp className="w-4 h-4 text-green-600" />
                        )}
                        <span className="text-sm font-medium text-gray-700">Profit Margin</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <div className="text-right">
                          <div className={`text-lg font-bold ${hasPricingWarning ? 'text-red-600' : 'text-green-600'}`}>
                            {hasPricingWarning ? '-' : '+'}GHS {Math.abs(margin).toFixed(2)}
                          </div>
                          <div className={`text-xs ${hasPricingWarning ? 'text-red-500' : 'text-green-600'}`}>
                            {Math.abs(marginPct).toFixed(1)}% margin
                          </div>
                        </div>
                      </div>
                    </div>
                    {hasPricingWarning && (
                      <p className="text-xs text-red-600 mt-2 font-medium">
                        ⚠ Selling price is below cost price — you will lose money on each sale.
                      </p>
                    )}
                    {!hasPricingWarning && isProfitable && (
                      <p className="text-xs text-green-600 mt-2">
                        ✓ Healthy margin — you earn GHS {margin.toFixed(2)} on every unit sold.
                      </p>
                    )}
                  </div>
                )}

                {/* Advanced Pricing Toggle */}
                <div>
                  <button
                    type="button"
                    onClick={() => setShowAdvancedPricing(p => !p)}
                    className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-700 font-medium transition-colors"
                  >
                    {showAdvancedPricing ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    {showAdvancedPricing ? 'Hide' : 'Show'} optional pricing
                  </button>

                  {showAdvancedPricing && (
                    <div className="mt-4 grid grid-cols-2 gap-4 p-4 bg-gray-50 rounded-xl border border-gray-200">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1.5">
                          Installment Price (GHS)
                          <Tooltip text="Price for customers paying in installments. Usually slightly higher than selling price." />
                        </label>
                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">₵</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="0.00"
                            value={installmentPrice}
                            onChange={e => setInstallmentPrice(e.target.value)}
                            className="w-full pl-7 pr-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm bg-white"
                          />
                        </div>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1.5">
                          Wholesale Price (GHS)
                          <Tooltip text="Discounted price for bulk or trade buyers. Should be above cost price." />
                        </label>
                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">₵</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="0.00"
                            value={wholesalePrice}
                            onChange={e => setWholesalePrice(e.target.value)}
                            className="w-full pl-7 pr-4 py-2.5 border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors text-sm bg-white"
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* ─── SECTION 4: DISTRIBUTION ─── */}
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              {sectionHeader(
                <MapPin className="w-4 h-4 text-purple-600" />,
                'Distribution',
                'Select branches where this item will be stocked',
                'bg-purple-50'
              )}

              {/* Selected Branch Tags */}
              {selectedBranches.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4">
                  {selectedBranches.map(branch => (
                    <span
                      key={branch}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full text-xs font-medium"
                    >
                      {branch}
                      {!isEditMode && (
                        <button
                          type="button"
                          onClick={() => removeBranch(branch)}
                          className="hover:text-red-500 transition-colors"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              )}

              {/* Search + Select All */}
              <div className="flex items-center gap-2 mb-3">
                <div className="relative flex-1">
                  <input
                    type="text"
                    placeholder="Search branches..."
                    value={branchSearch}
                    onChange={e => setBranchSearch(e.target.value)}
                    disabled={isEditMode}
                    className="w-full pl-4 pr-4 py-2 text-sm border border-gray-200 hover:border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-colors"
                  />
                </div>
                <button
                  type="button"
                  onClick={toggleAllBranches}
                  disabled={isEditMode}
                  className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-indigo-600 hover:text-indigo-700 border border-indigo-200 rounded-lg hover:bg-indigo-50 transition-colors whitespace-nowrap"
                >
                  {selectedBranches.length === branches.length ? (
                    <><CheckSquare className="w-3.5 h-3.5" /> Deselect All</>
                  ) : (
                    <><Square className="w-3.5 h-3.5" /> Select All</>
                  )}
                </button>
              </div>

              {/* Branch List */}
              {branches.length === 0 ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                  No branches are available yet. Add manager branches first before creating inventory items.
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  {filteredBranches.map(branch => {
                  const selected = selectedBranches.includes(branch);
                  return (
                    <button
                      key={branch}
                      type="button"
                      onClick={() => toggleBranch(branch)}
                      disabled={isEditMode}
                      className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg border text-sm text-left transition-all ${
                        selected
                          ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                          : 'bg-white border-gray-200 text-gray-700 hover:bg-gray-50 hover:border-gray-300'
                      }`}
                    >
                      <div className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors ${selected ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300'}`}>
                        {selected && <Check className="w-2.5 h-2.5 text-white" />}
                      </div>
                      <MapPin className={`w-3.5 h-3.5 flex-shrink-0 ${selected ? 'text-indigo-500' : 'text-gray-400'}`} />
                      <span className="truncate">{branch}</span>
                    </button>
                  );
                  })}
                </div>
              )}
              {errors.branches && <p className="text-xs text-red-500 mt-2 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.branches}</p>}
              {isEditMode && (
                <p className="mt-2 text-xs text-gray-500">
                  Branch and warehouse assignments are locked during edit so price and naming updates do not move stock accidentally.
                </p>
              )}
              {selectedBranches.length > 0 && (
                <div className="mt-5 space-y-3 border-t border-gray-200 pt-4">
                  <div>
                    <h4 className="text-sm font-semibold text-gray-900">Assign Warehouse / Location</h4>
                    <p className="mt-1 text-xs text-gray-500">
                      Pick the active warehouse or storage location where this stock will be placed for each selected branch.
                    </p>
                  </div>

                  {selectedBranches.map((branch) => {
                    const branchLocations = activeLocationMap[branch] || [];
                    const selectedLocationId = selectedLocations[branch] || '';
                    return (
                      <div key={branch} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium text-gray-900">{branch}</div>
                            <div className="text-xs text-gray-500">
                              {branchLocations.length > 0
                                ? `${branchLocations.length} active locations available`
                                : 'No active locations available for this branch'}
                            </div>
                          </div>
                        </div>

                        {branchLocations.length === 0 ? (
                          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                            Add and activate a location for {branch} in Settings before stocking items here.
                          </div>
                        ) : (
                          <select
                            value={selectedLocationId}
                            onChange={(e) =>
                              setSelectedLocations((current) => ({
                                ...current,
                                [branch]: e.target.value,
                              }))
                            }
                            disabled={isEditMode}
                            className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          >
                            <option value="">Select a warehouse / location...</option>
                            {branchLocations.map((location) => {
                              const utilization = location.capacity > 0
                                ? Math.round((location.stockUnits / location.capacity) * 100)
                                : 0;
                              return (
                                <option key={location.id} value={location.id}>
                                  {location.name} ({location.code}) - {location.stockUnits}/{location.capacity || 0} units
                                  {location.capacity > 0 ? ` - ${utilization}% used` : ''}
                                </option>
                              );
                            })}
                          </select>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {errors.locations && <p className="text-xs text-red-500 mt-2 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.locations}</p>}
            </div>

          </div>
        </div>

        {/* Sticky Footer */}
        <div className="px-6 py-4 border-t border-gray-100 bg-white flex items-center justify-between gap-4">
          <div className="text-xs text-gray-400 flex items-center gap-1.5">
            {!imagePreviewUrl && (
              <><AlertTriangle className="w-3.5 h-3.5 text-amber-400" /> Upload an image to enable save</>
            )}
            {isUploadingImage && (
              <><AlertTriangle className="w-3.5 h-3.5 text-amber-400" /> Uploading image to Cloudflare</>
            )}
            {imagePreviewUrl && !canSubmit && !isUploadingImage && (
              <><AlertTriangle className="w-3.5 h-3.5 text-amber-400" /> Fill required fields to continue</>
            )}
            {canSubmit && (
              <><Check className="w-3.5 h-3.5 text-green-500" /> Ready to save</>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg transition-all ${
                canSubmit
                  ? 'bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm'
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed'
              }`}
            >
              {isSubmitting ? (
                <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> Saving...</>
              ) : (
                <><Plus className="w-4 h-4" /> {isEditMode ? 'Save Changes' : 'Add to Inventory'}</>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
