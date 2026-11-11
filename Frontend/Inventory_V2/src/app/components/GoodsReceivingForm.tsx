import { useState, useMemo } from 'react';
import {
  X,
  FileText,
  ClipboardList,
  ChevronRight,
  ChevronLeft,
  Package,
  AlertTriangle,
  CheckCircle,
  Info,
  Truck,
  User,
  Calendar,
  AlertOctagon,
  ShieldAlert,
  Eye,
  EyeOff,
  ClipboardCheck,
  ArrowRight,
  Search,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAccessSafe } from '../context/RoleAccessContext';

// ─── Types ────────────────────────────────────────────────────────────────────

export type DiscrepancyReason = 'damage' | 'shortage' | 'theft' | 'error';

export type GRNLineItem = {
  product: string;
  sku: string;
  expectedQty: number;
  receivedQty: number;
  damagedQty: number;
  variance: number;
  discrepancyReason?: DiscrepancyReason;
  discrepancyNotes?: string;
  unitCost?: number;
};

export type GRNRecord = {
  id: string;
  linkedType: 'po' | 'pr';
  linkedRef: string;
  supplier: string;
  receivedBy: string;
  receivedDate: string;
  status: 'complete' | 'partial' | 'discrepancy';
  lineItems: GRNLineItem[];
  auditTriggered: boolean;
  auditRef?: string;
  notes?: string;
  createdAt: string;
};

interface GoodsReceivingFormProps {
  onClose: () => void;
  onSave: (grn: GRNRecord) => void;
  canSeePricing?: boolean; // kept for backward-compat; context takes precedence
  purchaseOrders: { id: string; supplier: string; items: number; totalQuantity: number; status: string }[];
  procurementRequests: { id: string; supplier: string; items: { product: string; quantity: number }[]; status: string }[];
}

const DISCREPANCY_REASONS: { value: DiscrepancyReason; label: string; color: string; icon: string }[] = [
  { value: 'damage',   label: 'Damaged Goods',    color: 'bg-orange-50 text-orange-700 border-orange-300', icon: '📦' },
  { value: 'shortage', label: 'Supplier Shortage', color: 'bg-amber-50 text-amber-700 border-amber-300',   icon: '📉' },
  { value: 'theft',    label: 'Suspected Theft',   color: 'bg-red-50 text-red-700 border-red-300',         icon: '🚨' },
  { value: 'error',    label: 'Counting / Data Error', color: 'bg-blue-50 text-blue-700 border-blue-300', icon: '📋' },
];

// Mock expected line items from linked PO/PR
const MOCK_PO_LINES: Record<string, GRNLineItem[]> = {
  'PO-1234': [
    { product: '65″ Smart TV', sku: 'TV-65-001', expectedQty: 40, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 3200 },
    { product: '55″ Smart TV', sku: 'TV-55-001', expectedQty: 30, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 2600 },
    { product: 'Soundbar Pro', sku: 'AUD-SB-002', expectedQty: 30, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 900 },
  ],
  'PO-1233': [
    { product: 'Premium Sofa Set', sku: 'SF-PRE-001', expectedQty: 25, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 4500 },
    { product: 'Coffee Table', sku: 'TB-COF-001', expectedQty: 40, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 850 },
    { product: 'Side Tables', sku: 'TB-SID-002', expectedQty: 30, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 620 },
  ],
  'PO-1232': [
    { product: 'Refrigerator — Stainless', sku: 'REF-001', expectedQty: 8, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 5200 },
    { product: 'Washing Machine', sku: 'WM-FT-001', expectedQty: 5, receivedQty: 0, damagedQty: 0, variance: 0, unitCost: 4100 },
  ],
};

// ─── Main Component ────────────────────────────────────────────────────────────

export function GoodsReceivingForm({
  onClose,
  onSave,
  canSeePricing: propCanSeePricing,
  purchaseOrders,
  procurementRequests,
}: GoodsReceivingFormProps) {
  const { canViewPricing } = useAccessSafe();
  // Context takes precedence over prop; prop is fallback for isolated usage
  const canSeePricing = canViewPricing ?? propCanSeePricing ?? true;
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [linkType, setLinkType] = useState<'po' | 'pr'>('po');
  const [linkedRef, setLinkedRef] = useState('');
  const [receivedBy, setReceivedBy] = useState('');
  const [receivedDate, setReceivedDate] = useState(new Date().toISOString().split('T')[0]);
  const [notes, setNotes] = useState('');
  const [lineItems, setLineItems] = useState<GRNLineItem[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [refSearch, setRefSearch] = useState('');
  const [showPricing, setShowPricing] = useState(true);

  // Derive link options
  const poOptions = purchaseOrders.filter(p => ['approved', 'sent', 'partially-delivered'].includes(p.status));
  const prOptions = procurementRequests.filter(r => r.status === 'approved');

  const filteredPO = poOptions.filter(p => p.id.toLowerCase().includes(refSearch.toLowerCase()) || p.supplier.toLowerCase().includes(refSearch.toLowerCase()));
  const filteredPR = prOptions.filter(p => p.id.toLowerCase().includes(refSearch.toLowerCase()) || p.supplier.toLowerCase().includes(refSearch.toLowerCase()));

  // Derived stats
  const hasDiscrepancy = lineItems.some(li => li.variance !== 0);
  const totalExpected = lineItems.reduce((s, l) => s + l.expectedQty, 0);
  const totalReceived = lineItems.reduce((s, l) => s + l.receivedQty, 0);
  const totalVariance = totalExpected - totalReceived;
  const totalDamaged = lineItems.reduce((s, l) => s + l.damagedQty, 0);
  const mismatches = lineItems.filter(l => l.variance !== 0);
  const missingReasons = mismatches.filter(l => !l.discrepancyReason);
  const auditRequired = mismatches.some(l => l.discrepancyReason === 'theft');

  // Total cost value (finance only)
  const totalCostValue = lineItems.reduce((s, l) => s + (l.unitCost || 0) * l.receivedQty, 0);

  const linkedSupplier = useMemo(() => {
    if (!linkedRef) return '';
    if (linkType === 'po') return purchaseOrders.find(p => p.id === linkedRef)?.supplier || '';
    return procurementRequests.find(r => r.id === linkedRef)?.supplier || '';
  }, [linkedRef, linkType, purchaseOrders, procurementRequests]);

  // Step 1: select reference
  const selectRef = (ref: string, type: 'po' | 'pr') => {
    setLinkedRef(ref);
    setLinkType(type);
    // Populate line items
    if (type === 'po' && MOCK_PO_LINES[ref]) {
      setLineItems(MOCK_PO_LINES[ref].map(li => ({ ...li, receivedQty: 0, damagedQty: 0, variance: li.expectedQty })));
    } else if (type === 'pr') {
      const pr = procurementRequests.find(r => r.id === ref);
      if (pr) {
        setLineItems(pr.items.map(item => ({
          product: item.product,
          sku: `SKU-${Math.floor(Math.random() * 9000) + 1000}`,
          expectedQty: item.quantity,
          receivedQty: 0,
          damagedQty: 0,
          variance: item.quantity,
          unitCost: 0,
        })));
      }
    }
  };

  // Update line item received/damaged qty
  const updateLine = (i: number, field: 'receivedQty' | 'damagedQty' | 'discrepancyReason' | 'discrepancyNotes', value: number | string) => {
    setLineItems(prev => prev.map((li, idx) => {
      if (idx !== i) return li;
      const updated = { ...li, [field]: value };
      if (field === 'receivedQty' || field === 'damagedQty') {
        updated.variance = updated.expectedQty - (updated.receivedQty);
        // If variance resolved, clear reason
        if (updated.variance === 0) {
          delete updated.discrepancyReason;
          delete updated.discrepancyNotes;
        }
      }
      return updated;
    }));
  };

  const validateStep1 = () => {
    const e: Record<string, string> = {};
    if (!linkedRef) e.linkedRef = 'Please select a Purchase Order or Procurement Request';
    if (!receivedBy.trim()) e.receivedBy = 'Received by is required';
    if (!receivedDate) e.receivedDate = 'Date is required';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const validateStep2 = () => {
    const e: Record<string, string> = {};
    if (missingReasons.length > 0) {
      e.reasons = `Please provide discrepancy reasons for ${missingReasons.length} item(s) with variance`;
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleNext = () => {
    if (step === 1) { if (validateStep1()) setStep(2); }
    else if (step === 2) { if (validateStep2()) setStep(3); }
  };

  const handleSubmit = () => {
    const status: GRNRecord['status'] = !hasDiscrepancy ? 'complete' : totalReceived < totalExpected ? 'discrepancy' : 'partial';
    const grn: GRNRecord = {
      id: `GRN-${String(Math.floor(Math.random() * 9000) + 1000)}`,
      linkedType: linkType,
      linkedRef,
      supplier: linkedSupplier,
      receivedBy,
      receivedDate,
      status,
      lineItems,
      auditTriggered: auditRequired || hasDiscrepancy,
      auditRef: (auditRequired || hasDiscrepancy) ? `AUD-${String(Math.floor(Math.random() * 9000) + 1000)}` : undefined,
      notes,
      createdAt: new Date().toISOString().split('T')[0],
    };
    onSave(grn);
    if (auditRequired) toast.error(`🚨 Audit investigation auto-triggered: ${grn.auditRef}`, { duration: 5000 });
    else if (hasDiscrepancy) toast.warning(`⚠ GRN saved with discrepancies — audit ref: ${grn.auditRef}`, { duration: 4000 });
    else toast.success(`GRN ${grn.id} recorded successfully — all quantities match!`);
    onClose();
  };

  const stepLabels = ['Link Reference', 'Verify Quantities', 'Review & Submit'];

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="w-full max-w-3xl bg-white flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-50 rounded-lg"><Truck className="w-5 h-5 text-indigo-600" /></div>
            <div>
              <h2 className="font-semibold text-gray-900">Record Goods Receipt (GRN)</h2>
              <p className="text-xs text-gray-500">Verify received items against expected quantities</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {canSeePricing && (
              <button
                onClick={() => setShowPricing(p => !p)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50 text-gray-600"
              >
                {showPricing ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                {showPricing ? 'Hide' : 'Show'} Pricing
              </button>
            )}
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg text-gray-500">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-0 px-6 py-3 bg-gray-50 border-b border-gray-100">
          {stepLabels.map((label, i) => {
            const num = i + 1;
            const done = step > num;
            const active = step === num;
            return (
              <div key={label} className="flex items-center gap-0">
                <div className="flex items-center gap-2">
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold transition-all ${done ? 'bg-emerald-500 text-white' : active ? 'bg-indigo-600 text-white' : 'bg-gray-200 text-gray-500'}`}>
                    {done ? '✓' : num}
                  </div>
                  <span className={`text-xs font-medium ${active ? 'text-indigo-600' : done ? 'text-emerald-600' : 'text-gray-400'}`}>{label}</span>
                </div>
                {i < 2 && <ChevronRight className="w-4 h-4 text-gray-300 mx-3 flex-shrink-0" />}
              </div>
            );
          })}
        </div>

        {/* Access control banner for non-finance */}
        {!canSeePricing && (
          <div className="flex items-center gap-2.5 px-6 py-2 bg-amber-50 border-b border-amber-200">
            <ShieldAlert className="w-4 h-4 text-amber-500 flex-shrink-0" />
            <p className="text-xs text-amber-700"><strong>Cost pricing is hidden</strong> — your role does not have access to financial data. Quantities and discrepancy details only.</p>
          </div>
        )}

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto">

          {/* ─── STEP 1: Link Reference ─── */}
          {step === 1 && (
            <div className="p-6 space-y-6">
              {/* Link type selector */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Link this GRN to</label>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { value: 'po', label: 'Purchase Order', icon: FileText, desc: 'Link to an approved or sent PO' },
                    { value: 'pr', label: 'Procurement Request', icon: ClipboardList, desc: 'Link to an approved PR (no PO yet)' },
                  ].map(opt => {
                    const Icon = opt.icon;
                    const sel = linkType === opt.value;
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => { setLinkType(opt.value as 'po' | 'pr'); setLinkedRef(''); setLineItems([]); }}
                        className={`flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all ${sel ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'}`}
                      >
                        <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${sel ? 'text-indigo-600' : 'text-gray-400'}`} />
                        <div>
                          <div className={`text-sm font-medium ${sel ? 'text-indigo-700' : 'text-gray-700'}`}>{opt.label}</div>
                          <div className="text-xs text-gray-500 mt-0.5">{opt.desc}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Reference search & select */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Select {linkType === 'po' ? 'Purchase Order' : 'Procurement Request'} <span className="text-red-500">*</span>
                </label>
                <div className="relative mb-2">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Search by ID or supplier..."
                    value={refSearch}
                    onChange={e => setRefSearch(e.target.value)}
                    className="w-full pl-9 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div className="border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100 max-h-52 overflow-y-auto">
                  {(linkType === 'po' ? filteredPO : filteredPR).length === 0 ? (
                    <div className="py-8 text-center text-gray-400 text-sm">No {linkType === 'po' ? 'active purchase orders' : 'approved requests'} found</div>
                  ) : (linkType === 'po' ? filteredPO : filteredPR).map((item: any) => {
                    const sel = linkedRef === item.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => selectRef(item.id, linkType)}
                        className={`w-full flex items-center justify-between px-4 py-3 text-left transition-colors ${sel ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}
                      >
                        <div className="flex items-center gap-3">
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${sel ? 'bg-indigo-100' : 'bg-gray-100'}`}>
                            {linkType === 'po' ? <FileText className={`w-4 h-4 ${sel ? 'text-indigo-600' : 'text-gray-500'}`} /> : <ClipboardList className={`w-4 h-4 ${sel ? 'text-indigo-600' : 'text-gray-500'}`} />}
                          </div>
                          <div>
                            <div className={`text-sm font-medium ${sel ? 'text-indigo-700' : 'text-gray-800'}`}>{item.id}</div>
                            <div className="text-xs text-gray-500">{item.supplier}</div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-gray-400">{linkType === 'po' ? `${item.totalQuantity} units` : `${item.items?.length} items`}</span>
                          {sel && <CheckCircle className="w-4 h-4 text-indigo-600" />}
                        </div>
                      </button>
                    );
                  })}
                </div>
                {errors.linkedRef && <p className="text-xs text-red-500 mt-1.5 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{errors.linkedRef}</p>}
              </div>

              {/* Basic info */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Received by <span className="text-red-500">*</span></label>
                  <div className="relative">
                    <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="text"
                      placeholder="Warehouse Manager"
                      value={receivedBy}
                      onChange={e => setReceivedBy(e.target.value)}
                      className={`w-full pl-9 pr-4 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${errors.receivedBy ? 'border-red-400 bg-red-50' : 'border-gray-200'}`}
                    />
                  </div>
                  {errors.receivedBy && <p className="text-xs text-red-500 mt-1">{errors.receivedBy}</p>}
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Received Date <span className="text-red-500">*</span></label>
                  <div className="relative">
                    <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="date"
                      value={receivedDate}
                      onChange={e => setReceivedDate(e.target.value)}
                      className={`w-full pl-9 pr-4 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${errors.receivedDate ? 'border-red-400 bg-red-50' : 'border-gray-200'}`}
                    />
                  </div>
                  {errors.receivedDate && <p className="text-xs text-red-500 mt-1">{errors.receivedDate}</p>}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">General Notes</label>
                <textarea
                  rows={3}
                  placeholder="Any observations during receiving (truck condition, packaging state, etc.)"
                  value={notes}
                  onChange={e => setNotes(e.target.value)}
                  className="w-full px-4 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                />
              </div>
            </div>
          )}

          {/* ─── STEP 2: Verify Quantities ─── */}
          {step === 2 && (
            <div className="p-6 space-y-5">
              {/* Linked info banner */}
              <div className="flex items-center gap-3 p-3 bg-indigo-50 rounded-xl border border-indigo-200">
                {linkType === 'po' ? <FileText className="w-4 h-4 text-indigo-600" /> : <ClipboardList className="w-4 h-4 text-indigo-600" />}
                <div>
                  <span className="text-sm font-semibold text-indigo-700">{linkedRef}</span>
                  <span className="text-sm text-indigo-600 ml-2">— {linkedSupplier}</span>
                </div>
                <div className="ml-auto flex items-center gap-1.5 text-xs text-indigo-600">
                  <ArrowRight className="w-3.5 h-3.5" />
                  Received by <strong>{receivedBy}</strong> on {receivedDate}
                </div>
              </div>

              {/* Quick summary bar */}
              {lineItems.length > 0 && (
                <div className="grid grid-cols-4 gap-3">
                  {[
                    { label: 'Expected', value: totalExpected, color: 'text-gray-800' },
                    { label: 'Received', value: totalReceived, color: totalReceived < totalExpected ? 'text-red-600' : 'text-emerald-600' },
                    { label: 'Variance', value: totalVariance, color: totalVariance !== 0 ? 'text-red-600' : 'text-emerald-600' },
                    { label: 'Damaged', value: totalDamaged, color: totalDamaged > 0 ? 'text-orange-600' : 'text-gray-500' },
                  ].map(stat => (
                    <div key={stat.label} className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                      <div className="text-xs text-gray-500">{stat.label}</div>
                      <div className={`text-lg font-bold mt-0.5 ${stat.color}`}>{stat.value >= 0 ? stat.value : `−${Math.abs(stat.value)}`}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Instruction */}
              <div className="flex items-start gap-2 p-3 bg-blue-50 rounded-lg border border-blue-200">
                <Info className="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-blue-700">Enter the <strong>actual received quantity</strong> for each line item. Items with variance will be highlighted in red and require a discrepancy reason.</p>
              </div>

              {errors.reasons && (
                <div className="flex items-center gap-2 p-3 bg-red-50 rounded-lg border border-red-200">
                  <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0" />
                  <p className="text-xs text-red-600 font-medium">{errors.reasons}</p>
                </div>
              )}

              {/* Line items table */}
              <div className="space-y-3">
                {lineItems.map((li, i) => {
                  const v = li.expectedQty - li.receivedQty;
                  const hasMismatch = v !== 0;
                  return (
                    <div
                      key={i}
                      className={`rounded-xl border-2 p-4 transition-all ${hasMismatch ? 'border-red-300 bg-red-50/30' : 'border-gray-200 bg-white'}`}
                    >
                      {/* Item header */}
                      <div className="flex items-start justify-between mb-3">
                        <div>
                          <div className="font-medium text-gray-900 text-sm">{li.product}</div>
                          <div className="text-xs text-gray-500 mt-0.5">{li.sku}</div>
                        </div>
                        {hasMismatch ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 bg-red-100 text-red-700 rounded-full text-xs font-semibold border border-red-200">
                            <AlertTriangle className="w-3 h-3" /> Mismatch
                          </span>
                        ) : li.receivedQty > 0 ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 bg-emerald-100 text-emerald-700 rounded-full text-xs font-semibold border border-emerald-200">
                            <CheckCircle className="w-3 h-3" /> Match
                          </span>
                        ) : null}
                      </div>

                      {/* Qty inputs */}
                      <div className="grid grid-cols-4 gap-3 mb-3">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Expected</label>
                          <div className="px-3 py-2 bg-gray-100 border border-gray-200 rounded-lg text-sm font-semibold text-gray-700 text-center">{li.expectedQty}</div>
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Received <span className="text-red-400">*</span></label>
                          <input
                            type="number"
                            min="0"
                            max={li.expectedQty + 10}
                            value={li.receivedQty === 0 ? '' : li.receivedQty}
                            placeholder="0"
                            onChange={e => updateLine(i, 'receivedQty', Math.max(0, parseInt(e.target.value) || 0))}
                            className={`w-full px-3 py-2 border rounded-lg text-sm text-center font-semibold focus:outline-none focus:ring-2 focus:ring-indigo-500 ${hasMismatch ? 'border-red-400 bg-red-50 text-red-700' : 'border-gray-200'}`}
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Damaged</label>
                          <input
                            type="number"
                            min="0"
                            value={li.damagedQty === 0 ? '' : li.damagedQty}
                            placeholder="0"
                            onChange={e => updateLine(i, 'damagedQty', Math.max(0, parseInt(e.target.value) || 0))}
                            className={`w-full px-3 py-2 border rounded-lg text-sm text-center focus:outline-none focus:ring-2 focus:ring-indigo-500 ${li.damagedQty > 0 ? 'border-orange-300 bg-orange-50 text-orange-700 font-semibold' : 'border-gray-200'}`}
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Variance</label>
                          <div className={`px-3 py-2 rounded-lg text-sm font-bold text-center border ${v > 0 ? 'bg-red-100 text-red-700 border-red-300' : v < 0 ? 'bg-orange-100 text-orange-700 border-orange-300' : 'bg-emerald-100 text-emerald-700 border-emerald-300'}`}>
                            {v > 0 ? `−${v}` : v < 0 ? `+${Math.abs(v)}` : '0'}
                          </div>
                        </div>
                        {canSeePricing && showPricing && li.unitCost && (
                          <div className="col-span-4 pt-1 flex items-center gap-2 text-xs text-gray-500 border-t border-gray-100 mt-1">
                            <span>Unit cost: <strong className="text-gray-700">GHS {li.unitCost.toLocaleString()}</strong></span>
                            <span className="text-gray-300">|</span>
                            <span>Line value: <strong className="text-gray-700">GHS {(li.unitCost * li.receivedQty).toLocaleString()}</strong></span>
                            {v !== 0 && (
                              <>
                                <span className="text-gray-300">|</span>
                                <span className="text-red-600">Discrepancy value: <strong>GHS {(li.unitCost * Math.abs(v)).toLocaleString()}</strong></span>
                              </>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Discrepancy reason (required if mismatch) */}
                      {hasMismatch && (
                        <div className="mt-3 space-y-2.5 pt-3 border-t border-red-200">
                          <div className="flex items-center gap-1.5">
                            <AlertOctagon className="w-3.5 h-3.5 text-red-500" />
                            <label className="text-xs font-semibold text-red-600">Discrepancy Reason Required</label>
                          </div>
                          <div className="grid grid-cols-2 gap-2">
                            {DISCREPANCY_REASONS.map(r => (
                              <button
                                key={r.value}
                                type="button"
                                onClick={() => updateLine(i, 'discrepancyReason', r.value)}
                                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left text-xs font-medium transition-all ${li.discrepancyReason === r.value ? `${r.color} ring-2 ring-offset-1 ring-current` : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300'}`}
                              >
                                <span>{r.icon}</span>
                                {r.label}
                                {li.discrepancyReason === r.value && <CheckCircle className="w-3 h-3 ml-auto flex-shrink-0" />}
                              </button>
                            ))}
                          </div>
                          {li.discrepancyReason === 'theft' && (
                            <div className="flex items-start gap-2 p-2.5 bg-red-100 rounded-lg border border-red-300">
                              <AlertOctagon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                              <p className="text-xs text-red-700 font-medium">🚨 <strong>Theft selected</strong> — an audit investigation will be automatically triggered and flagged to the Audit & Accountability team.</p>
                            </div>
                          )}
                          <textarea
                            placeholder="Additional notes about this discrepancy (required for Theft)..."
                            value={li.discrepancyNotes || ''}
                            onChange={e => updateLine(i, 'discrepancyNotes', e.target.value)}
                            className="w-full px-3 py-2 border border-red-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-red-400 bg-white resize-none"
                            rows={2}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ─── STEP 3: Review & Submit ─── */}
          {step === 3 && (
            <div className="p-6 space-y-5">
              {/* GRN Summary header */}
              <div className={`rounded-xl p-5 border-2 ${hasDiscrepancy ? 'bg-red-50 border-red-300' : 'bg-emerald-50 border-emerald-300'}`}>
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      {hasDiscrepancy ? <AlertTriangle className="w-5 h-5 text-red-600" /> : <CheckCircle className="w-5 h-5 text-emerald-600" />}
                      <span className={`font-semibold ${hasDiscrepancy ? 'text-red-700' : 'text-emerald-700'}`}>
                        {hasDiscrepancy ? 'Discrepancies Detected' : 'All Quantities Match'}
                      </span>
                    </div>
                    <p className={`text-xs ${hasDiscrepancy ? 'text-red-600' : 'text-emerald-600'}`}>
                      {hasDiscrepancy
                        ? `${mismatches.length} line item(s) have variance — audit will be auto-triggered`
                        : 'GRN is complete and balanced. No issues found.'}
                    </p>
                  </div>
                  <div className={`text-right text-xs ${hasDiscrepancy ? 'text-red-500' : 'text-emerald-500'}`}>
                    <div>Expected: <strong>{totalExpected}</strong></div>
                    <div>Received: <strong>{totalReceived}</strong></div>
                    <div>Variance: <strong>{totalVariance === 0 ? '0' : `−${Math.abs(totalVariance)}`}</strong></div>
                  </div>
                </div>
              </div>

              {/* Summary info grid */}
              <div className="grid grid-cols-2 gap-4">
                {[
                  { label: 'Linked Reference', value: `${linkType.toUpperCase()} → ${linkedRef}` },
                  { label: 'Supplier', value: linkedSupplier },
                  { label: 'Received By', value: receivedBy },
                  { label: 'Received Date', value: receivedDate },
                  { label: 'Total Items', value: lineItems.length },
                  { label: 'Damaged Qty', value: totalDamaged || 'None' },
                ].map(item => (
                  <div key={item.label} className="bg-gray-50 rounded-lg px-4 py-3 border border-gray-200">
                    <div className="text-xs text-gray-500">{item.label}</div>
                    <div className="text-sm font-semibold text-gray-800 mt-0.5">{item.value}</div>
                  </div>
                ))}
              </div>

              {/* Finance total */}
              {canSeePricing && showPricing && (
                <div className="bg-violet-50 border border-violet-200 rounded-xl px-4 py-3 flex items-center justify-between">
                  <div>
                    <div className="text-xs text-violet-600">Total Received Value (Finance Only)</div>
                    <div className="text-lg font-bold text-violet-800">GHS {totalCostValue.toLocaleString()}</div>
                  </div>
                  {hasDiscrepancy && (
                    <div className="text-right">
                      <div className="text-xs text-red-500">Discrepancy Value</div>
                      <div className="text-sm font-bold text-red-600">
                        −GHS {lineItems.filter(l => l.variance !== 0).reduce((s, l) => s + (l.unitCost || 0) * Math.abs(l.variance), 0).toLocaleString()}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Audit trigger notice */}
              {(hasDiscrepancy || auditRequired) && (
                <div className={`rounded-xl p-4 border ${auditRequired ? 'bg-red-50 border-red-300' : 'bg-amber-50 border-amber-300'}`}>
                  <div className="flex items-start gap-2.5">
                    <AlertOctagon className={`w-5 h-5 flex-shrink-0 mt-0.5 ${auditRequired ? 'text-red-600' : 'text-amber-600'}`} />
                    <div>
                      <p className={`text-sm font-semibold ${auditRequired ? 'text-red-700' : 'text-amber-700'}`}>
                        {auditRequired ? '🚨 Audit Investigation Will Be Triggered' : '⚠ Discrepancy Investigation Required'}
                      </p>
                      <p className={`text-xs mt-1 ${auditRequired ? 'text-red-600' : 'text-amber-600'}`}>
                        {auditRequired
                          ? 'Suspected theft was flagged. The Audit & Accountability team will be automatically notified and an investigation case will be opened.'
                          : `${mismatches.length} item(s) with discrepancies. An investigation record will be created in the Audit module.`}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {mismatches.map((l, i) => (
                          <span key={i} className={`text-xs px-2 py-1 rounded-full border ${l.discrepancyReason === 'theft' ? 'bg-red-100 text-red-700 border-red-300' : 'bg-amber-100 text-amber-700 border-amber-200'}`}>
                            {l.product}: −{l.variance} ({l.discrepancyReason || 'no reason'})
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Line item summary table */}
              <div>
                <h4 className="text-sm font-semibold text-gray-800 mb-2">Line Item Summary</h4>
                <div className="border border-gray-200 rounded-xl overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600">Product</th>
                        <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Expected</th>
                        <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Received</th>
                        <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Variance</th>
                        <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600">Reason</th>
                        {canSeePricing && showPricing && <th className="text-right px-3 py-2 text-xs font-semibold text-gray-600">Value (GHS)</th>}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {lineItems.map((li, i) => {
                        const v = li.expectedQty - li.receivedQty;
                        return (
                          <tr key={i} className={v !== 0 ? 'bg-red-50/40' : ''}>
                            <td className="px-3 py-2.5">
                              <div className="text-sm text-gray-800">{li.product}</div>
                              <div className="text-xs text-gray-400">{li.sku}</div>
                            </td>
                            <td className="px-3 py-2.5 text-center text-sm text-gray-700">{li.expectedQty}</td>
                            <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{li.receivedQty}</td>
                            <td className="px-3 py-2.5 text-center">
                              <span className={`inline-flex items-center justify-center w-10 h-6 rounded text-xs font-bold ${v !== 0 ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                                {v === 0 ? '✓' : `−${v}`}
                              </span>
                            </td>
                            <td className="px-3 py-2.5">
                              {li.discrepancyReason ? (
                                <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${DISCREPANCY_REASONS.find(r => r.value === li.discrepancyReason)?.color}`}>
                                  {DISCREPANCY_REASONS.find(r => r.value === li.discrepancyReason)?.label}
                                </span>
                              ) : (
                                <span className="text-xs text-gray-400">—</span>
                              )}
                            </td>
                            {canSeePricing && showPricing && (
                              <td className="px-3 py-2.5 text-right text-xs text-gray-700">
                                {li.unitCost ? `${(li.unitCost * li.receivedQty).toLocaleString()}` : '—'}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 bg-white flex items-center justify-between">
          <div>
            {step > 1 && (
              <button
                onClick={() => setStep(s => (s - 1) as 1 | 2 | 3)}
                className="flex items-center gap-1.5 px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
              >
                <ChevronLeft className="w-4 h-4" /> Back
              </button>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={onClose} className="px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
              Cancel
            </button>
            {step < 3 ? (
              <button
                onClick={handleNext}
                disabled={step === 1 && !linkedRef}
                className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg transition-all ${step === 1 && !linkedRef ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm'}`}
              >
                Continue <ChevronRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg shadow-sm transition-all ${hasDiscrepancy ? 'bg-red-600 hover:bg-red-700 text-white' : 'bg-emerald-600 hover:bg-emerald-700 text-white'}`}
              >
                <ClipboardCheck className="w-4 h-4" />
                {hasDiscrepancy ? 'Confirm & Flag Discrepancies' : 'Confirm GRN'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}