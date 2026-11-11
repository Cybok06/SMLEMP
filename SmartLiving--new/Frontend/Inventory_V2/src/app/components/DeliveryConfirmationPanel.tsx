import { useState } from 'react';
import {
  X,
  Truck,
  PackageCheck,
  UserCheck,
  Building2,
  CheckCircle,
  Clock,
  AlertTriangle,
  AlertOctagon,
  ChevronRight,
  User,
  Shield,
  CalendarClock,
  FileText,
  ArrowRight,
  Info,
  Lock,
} from 'lucide-react';
import { toast } from 'sonner';

// ─── Types ────────────────────────────────────────────────────────────────────

export type ConfirmationRole = 'Warehouse Staff' | 'Driver' | 'Customer' | 'Branch Manager' | 'Logistics Officer';

export type ConfirmationLog = {
  action: string;
  user: string;
  role: ConfirmationRole | string;
  timestamp: string;
  notes?: string;
};

export type DeliveryLineItem = {
  product: string;
  sku?: string;
  sentQty: number;
  receivedQty?: number;
  variance?: number;
};

export type DeliveryTracking = {
  orderId: string;
  customerName: string;
  address: string;
  productCard: string;
  assignedDriver: string;
  step: 0 | 1 | 2 | 3; // 0=not started, 1=dispatched, 2=picked, 3=received
  status: 'ready-delivery' | 'picked' | 'in-transit' | 'received' | 'discrepancy-flagged';
  items: DeliveryLineItem[];
  dispatchLog?: ConfirmationLog;
  pickupLog?: ConfirmationLog;
  receivedLog?: ConfirmationLog;
  discrepancyExplanation?: string;
  discrepancyResolved?: boolean;
};

interface DeliveryConfirmationPanelProps {
  tracking: DeliveryTracking;
  onClose: () => void;
  onUpdate: (updated: DeliveryTracking) => void;
}

// ─── Step Config ──────────────────────────────────────────────────────────────

const STEPS = [
  {
    id: 1,
    label: 'Sender Dispatches',
    sublabel: 'Warehouse / Sender',
    icon: Building2,
    actionLabel: 'Confirm Dispatch',
    color: { active: 'bg-indigo-600 text-white', done: 'bg-emerald-500 text-white', pending: 'bg-gray-100 text-gray-400' },
    border: { active: 'border-indigo-300 bg-indigo-50', done: 'border-emerald-300 bg-emerald-50', pending: 'border-gray-200 bg-gray-50' },
  },
  {
    id: 2,
    label: 'Driver Confirms Pickup',
    sublabel: 'Driver / Logistics',
    icon: Truck,
    actionLabel: 'Confirm Pickup',
    color: { active: 'bg-blue-600 text-white', done: 'bg-emerald-500 text-white', pending: 'bg-gray-100 text-gray-400' },
    border: { active: 'border-blue-300 bg-blue-50', done: 'border-emerald-300 bg-emerald-50', pending: 'border-gray-200 bg-gray-50' },
  },
  {
    id: 3,
    label: 'Receiver Confirms Arrival',
    sublabel: 'Customer / Recipient',
    icon: UserCheck,
    actionLabel: 'Confirm Receipt',
    color: { active: 'bg-violet-600 text-white', done: 'bg-emerald-500 text-white', pending: 'bg-gray-100 text-gray-400' },
    border: { active: 'border-violet-300 bg-violet-50', done: 'border-emerald-300 bg-emerald-50', pending: 'border-gray-200 bg-gray-50' },
  },
];

// ─── Main Component ────────────────────────────────────────────────────────────

export function DeliveryConfirmationPanel({ tracking, onClose, onUpdate }: DeliveryConfirmationPanelProps) {
  const [activeForm, setActiveForm] = useState<1 | 2 | 3 | null>(
    tracking.step < 3 ? (tracking.step + 1) as 1 | 2 | 3 : null
  );

  // Form state
  const [formUser, setFormUser] = useState('');
  const [formRole, setFormRole] = useState<string>('');
  const [formNotes, setFormNotes] = useState('');
  const [itemQtys, setItemQtys] = useState<Record<number, number>>(
    Object.fromEntries(tracking.items.map((_, i) => [i, tracking.items[i].sentQty]))
  );
  const [receivedQtys, setReceivedQtys] = useState<Record<number, number>>(
    Object.fromEntries(tracking.items.map((_, i) => [i, tracking.items[i].sentQty]))
  );
  const [discrepancyExplanation, setDiscrepancyExplanation] = useState(tracking.discrepancyExplanation || '');
  const [errors, setErrors] = useState<Record<string, string>>({});

  const now = () => {
    const d = new Date();
    return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const hasDiscrepancy = tracking.step === 3 && tracking.items.some(it => (it.variance ?? 0) !== 0);

  const roleOptions: Record<number, string[]> = {
    1: ['Warehouse Staff', 'Logistics Officer', 'Branch Manager', 'Store Officer'],
    2: ['Driver', 'Logistics Officer', 'Dispatch Rider', 'Van Driver'],
    3: ['Customer', 'Household Member', 'Branch Manager', 'Office Manager'],
  };

  const validate = (step: number) => {
    const e: Record<string, string> = {};
    if (!formUser.trim()) e.user = 'Name is required';
    if (!formRole) e.role = 'Role is required';
    if (step === 3) {
      const newVariance = tracking.items.some((_, i) => receivedQtys[i] !== tracking.items[i].sentQty);
      if (newVariance && !discrepancyExplanation.trim()) e.explanation = 'Explanation required for discrepancy';
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleConfirm = (step: 1 | 2 | 3) => {
    if (!validate(step)) return;

    const log: ConfirmationLog = {
      action: STEPS[step - 1].actionLabel,
      user: formUser,
      role: formRole,
      timestamp: now(),
      notes: formNotes || undefined,
    };

    let updated = { ...tracking };

    if (step === 1) {
      // Update sent quantities from itemQtys
      updated.items = tracking.items.map((it, i) => ({ ...it, sentQty: itemQtys[i] ?? it.sentQty }));
      updated.dispatchLog = log;
      updated.step = 1;
      updated.status = 'picked';
      toast.success(`📦 Dispatch confirmed by ${formUser} (${formRole})`);
    } else if (step === 2) {
      updated.pickupLog = log;
      updated.step = 2;
      updated.status = 'in-transit';
      toast.success(`🚛 Pickup confirmed by ${formUser} (${formRole})`);
    } else if (step === 3) {
      const itemsWithReceipt = tracking.items.map((it, i) => {
        const rQty = receivedQtys[i] ?? it.sentQty;
        return { ...it, receivedQty: rQty, variance: it.sentQty - rQty };
      });
      const hasVar = itemsWithReceipt.some(it => (it.variance ?? 0) !== 0);
      updated.items = itemsWithReceipt;
      updated.receivedLog = log;
      updated.step = 3;
      updated.status = hasVar ? 'discrepancy-flagged' : 'received';
      if (hasVar) {
        updated.discrepancyExplanation = discrepancyExplanation;
        toast.error(`🚨 Discrepancy flagged on order ${tracking.orderId} — investigation required`, { duration: 5000 });
      } else {
        toast.success(`✅ ${tracking.orderId} fully confirmed and received — all quantities match!`);
      }
    }

    onUpdate(updated);
    setActiveForm(null);
    // Reset form
    setFormUser(''); setFormRole(''); setFormNotes('');
  };

  const resolveDiscrepancy = () => {
    if (!discrepancyExplanation.trim()) { setErrors({ explanation: 'Explanation required' }); return; }
    onUpdate({ ...tracking, discrepancyResolved: true, discrepancyExplanation, status: 'received' });
    toast.success('Discrepancy resolved and order marked as received.');
    onClose();
  };

  const getLog = (step: number): ConfirmationLog | undefined => {
    if (step === 1) return tracking.dispatchLog;
    if (step === 2) return tracking.pickupLog;
    return tracking.receivedLog;
  };

  const totalSent = tracking.items.reduce((s, it) => s + it.sentQty, 0);
  const totalReceived = tracking.items.reduce((s, it) => s + (it.receivedQty ?? 0), 0);

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="w-full max-w-2xl bg-white flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 sticky top-0 bg-white z-10">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-50 rounded-lg"><PackageCheck className="w-5 h-5 text-indigo-600" /></div>
            <div>
              <h2 className="font-semibold text-gray-900">Digital Delivery Confirmation</h2>
              <p className="text-xs text-gray-500">{tracking.orderId} — {tracking.customerName}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg"><X className="w-5 h-5 text-gray-500" /></button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto">

          {/* Order summary */}
          <div className="mx-5 mt-5 p-4 bg-gray-50 rounded-xl border border-gray-200">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-xs text-gray-500 block">Customer</span><span className="font-medium text-gray-800">{tracking.customerName}</span></div>
              <div><span className="text-xs text-gray-500 block">Driver</span><span className="font-medium text-gray-800">{tracking.assignedDriver}</span></div>
              <div className="col-span-2"><span className="text-xs text-gray-500 block">Delivery Address</span><span className="text-gray-700">{tracking.address}</span></div>
              <div><span className="text-xs text-gray-500 block">Package</span><span className="text-gray-700">{tracking.productCard}</span></div>
              <div><span className="text-xs text-gray-500 block">Total Items</span><span className="font-semibold text-gray-800">{tracking.items.reduce((s, it) => s + it.sentQty, 0)} units</span></div>
            </div>
          </div>

          {/* ─── 3-Step Pipeline ─── */}
          <div className="px-5 mt-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <Shield className="w-4 h-4 text-indigo-500" /> Mandatory Confirmation Chain
            </h3>
            <div className="flex items-start gap-0">
              {STEPS.map((s, i) => {
                const Icon = s.icon;
                const done = tracking.step >= s.id;
                const active = tracking.step === s.id - 1 && activeForm === s.id;
                const canAct = tracking.step === s.id - 1;
                const log = getLog(s.id);
                return (
                  <div key={s.id} className="flex items-start gap-0 flex-1">
                    <div className="flex-1">
                      <div className={`rounded-xl border-2 p-3 transition-all ${done ? s.border.done : active ? s.border.active : s.border.pending}`}>
                        <div className="flex items-center justify-between mb-2">
                          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${done ? s.color.done : canAct ? s.color.active : s.color.pending}`}>
                            {done ? '✓' : s.id}
                          </div>
                          {canAct && !done && (
                            <button
                              onClick={() => setActiveForm(s.id as 1 | 2 | 3)}
                              className="text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 px-2 py-1 rounded-md transition-colors"
                            >
                              Confirm
                            </button>
                          )}
                          {done && <Lock className="w-3.5 h-3.5 text-emerald-500" />}
                        </div>
                        <div className="text-xs font-semibold text-gray-700">{s.label}</div>
                        <div className="text-xs text-gray-400">{s.sublabel}</div>
                        {log && (
                          <div className="mt-2 pt-2 border-t border-gray-200 space-y-0.5">
                            <div className="flex items-center gap-1 text-xs text-gray-600"><User className="w-3 h-3" />{log.user}</div>
                            <div className="text-xs text-gray-400">{log.role}</div>
                            <div className="flex items-center gap-1 text-xs text-gray-400"><CalendarClock className="w-3 h-3" />{log.timestamp}</div>
                          </div>
                        )}
                        {!done && !canAct && (
                          <div className="mt-2 text-xs text-gray-400 flex items-center gap-1"><Lock className="w-3 h-3" /> Awaiting previous step</div>
                        )}
                      </div>
                    </div>
                    {i < 2 && <div className="flex items-center pt-5 px-1"><ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" /></div>}
                  </div>
                );
              })}
            </div>
          </div>

          {/* ─── Active Confirmation Form ─── */}
          {activeForm && tracking.step < 3 && (
            <div className="mx-5 mt-5 rounded-xl border-2 border-indigo-300 bg-indigo-50/40 p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="p-1.5 bg-indigo-100 rounded-lg">
                  {activeForm === 1 ? <Building2 className="w-4 h-4 text-indigo-600" /> : activeForm === 2 ? <Truck className="w-4 h-4 text-blue-600" /> : <UserCheck className="w-4 h-4 text-violet-600" />}
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-gray-800">Step {activeForm}: {STEPS[activeForm - 1].actionLabel}</h4>
                  <p className="text-xs text-gray-500">All fields are required and will be permanently logged</p>
                </div>
              </div>

              <div className="space-y-4">
                {/* User identity */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1.5">Your Name <span className="text-red-500">*</span></label>
                    <div className="relative">
                      <User className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                      <input
                        type="text"
                        placeholder="Full name"
                        value={formUser}
                        onChange={e => setFormUser(e.target.value)}
                        className={`w-full pl-8 pr-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${errors.user ? 'border-red-400 bg-red-50' : 'border-gray-200 bg-white'}`}
                      />
                    </div>
                    {errors.user && <p className="text-xs text-red-500 mt-1">{errors.user}</p>}
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1.5">Role <span className="text-red-500">*</span></label>
                    <select
                      value={formRole}
                      onChange={e => setFormRole(e.target.value)}
                      className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white ${errors.role ? 'border-red-400 bg-red-50' : 'border-gray-200'}`}
                    >
                      <option value="">Select role...</option>
                      {roleOptions[activeForm].map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                    {errors.role && <p className="text-xs text-red-500 mt-1">{errors.role}</p>}
                  </div>
                </div>

                {/* Step 1: Sender enters dispatch quantities */}
                {activeForm === 1 && (
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-2">Items Being Dispatched</label>
                    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50">
                          <tr>
                            <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600">Product</th>
                            <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Expected Qty</th>
                            <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Actual Sent <span className="text-red-500">*</span></th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {tracking.items.map((it, i) => (
                            <tr key={i}>
                              <td className="px-3 py-2.5 text-gray-800">{it.product}<div className="text-xs text-gray-400">{it.sku}</div></td>
                              <td className="px-3 py-2.5 text-center text-gray-600">{it.sentQty}</td>
                              <td className="px-3 py-2.5 text-center">
                                <input
                                  type="number" min="0"
                                  value={itemQtys[i] ?? it.sentQty}
                                  onChange={e => setItemQtys(p => ({ ...p, [i]: Math.max(0, parseInt(e.target.value) || 0) }))}
                                  className="w-16 px-2 py-1 border border-gray-300 rounded text-center text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Step 2: Driver confirms (read-only items) */}
                {activeForm === 2 && (
                  <div className="p-3 bg-blue-50 rounded-lg border border-blue-200">
                    <div className="flex items-start gap-2">
                      <Info className="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs font-semibold text-blue-700">Driver Confirms Physical Collection</p>
                        <p className="text-xs text-blue-600 mt-0.5">By confirming, you attest that you have physically collected all {totalSent} unit(s) from the sender and are responsible for their safe delivery.</p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {tracking.items.map((it, i) => (
                            <span key={i} className="text-xs bg-white border border-blue-200 text-blue-700 px-2 py-0.5 rounded-full">
                              {it.sentQty}× {it.product}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Step 3: Receiver enters received quantities */}
                {activeForm === 3 && (
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-2">Verify Received Quantities</label>
                    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50">
                          <tr>
                            <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600">Product</th>
                            <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Sent</th>
                            <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Received <span className="text-red-500">*</span></th>
                            <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600">Variance</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {tracking.items.map((it, i) => {
                            const rQty = receivedQtys[i] ?? it.sentQty;
                            const v = it.sentQty - rQty;
                            return (
                              <tr key={i} className={v !== 0 ? 'bg-red-50/40' : ''}>
                                <td className="px-3 py-2.5 text-gray-800 text-sm">{it.product}</td>
                                <td className="px-3 py-2.5 text-center text-gray-700">{it.sentQty}</td>
                                <td className="px-3 py-2.5 text-center">
                                  <input
                                    type="number" min="0"
                                    value={rQty === 0 ? '' : rQty}
                                    placeholder="0"
                                    onChange={e => setReceivedQtys(p => ({ ...p, [i]: Math.max(0, parseInt(e.target.value) || 0) }))}
                                    className={`w-16 px-2 py-1 border rounded text-center text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-indigo-500 ${v !== 0 ? 'border-red-400 bg-red-50 text-red-700' : 'border-gray-300'}`}
                                  />
                                </td>
                                <td className="px-3 py-2.5 text-center">
                                  <span className={`inline-flex items-center justify-center w-10 h-6 rounded text-xs font-bold ${v !== 0 ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                                    {v === 0 ? '✓' : `−${v}`}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    {tracking.items.some((_, i) => (receivedQtys[i] ?? tracking.items[i].sentQty) !== tracking.items[i].sentQty) && (
                      <div className="mt-3">
                        <label className="block text-xs font-semibold text-red-600 mb-1.5">
                          <AlertOctagon className="w-3.5 h-3.5 inline mr-1" />
                          Discrepancy Explanation Required <span className="text-red-500">*</span>
                        </label>
                        <textarea
                          rows={2}
                          placeholder="Explain why quantities don't match (e.g. 'Item damaged in transit, customer refused delivery', 'Missing from truck on arrival')..."
                          value={discrepancyExplanation}
                          onChange={e => { setDiscrepancyExplanation(e.target.value); setErrors(p => ({ ...p, explanation: '' })); }}
                          className={`w-full px-3 py-2 border rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-red-400 resize-none ${errors.explanation ? 'border-red-400 bg-red-50' : 'border-red-300 bg-red-50/30'}`}
                        />
                        {errors.explanation && <p className="text-xs text-red-500 mt-1">{errors.explanation}</p>}
                      </div>
                    )}
                  </div>
                )}

                {/* Notes */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1.5">Additional Notes (Optional)</label>
                  <textarea
                    rows={2}
                    placeholder="Any observations, conditions, or additional context..."
                    value={formNotes}
                    onChange={e => setFormNotes(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none bg-white"
                  />
                </div>

                {/* Immutability notice */}
                <div className="flex items-start gap-2 p-2.5 bg-amber-50 rounded-lg border border-amber-200">
                  <Lock className="w-3.5 h-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700">This confirmation is <strong>permanent and immutable</strong>. Your name, role, and timestamp will be recorded in the audit log and cannot be edited after submission.</p>
                </div>

                {/* Confirm button */}
                <button
                  onClick={() => handleConfirm(activeForm)}
                  className={`w-full py-2.5 rounded-xl text-sm font-semibold text-white transition-all shadow-sm ${
                    activeForm === 1 ? 'bg-indigo-600 hover:bg-indigo-700' :
                    activeForm === 2 ? 'bg-blue-600 hover:bg-blue-700' :
                    'bg-violet-600 hover:bg-violet-700'
                  }`}
                >
                  {STEPS[activeForm - 1].actionLabel} — Submit Confirmation
                </button>
              </div>
            </div>
          )}

          {/* ─── Discrepancy Resolution ─── */}
          {tracking.status === 'discrepancy-flagged' && !tracking.discrepancyResolved && (
            <div className="mx-5 mt-5 rounded-xl border-2 border-red-300 bg-red-50 p-5">
              <div className="flex items-center gap-2 mb-4">
                <AlertOctagon className="w-5 h-5 text-red-600" />
                <h4 className="text-sm font-semibold text-red-700">Discrepancy Alert — Resolution Required</h4>
              </div>
              <div className="space-y-3">
                <div className="border border-red-200 rounded-lg overflow-hidden bg-white">
                  <table className="w-full text-sm">
                    <thead className="bg-red-50"><tr><th className="text-left px-3 py-2 text-xs font-semibold text-red-600">Item</th><th className="text-center px-3 py-2 text-xs font-semibold text-red-600">Sent</th><th className="text-center px-3 py-2 text-xs font-semibold text-red-600">Received</th><th className="text-center px-3 py-2 text-xs font-semibold text-red-600">Variance</th></tr></thead>
                    <tbody className="divide-y divide-gray-100">
                      {tracking.items.filter(it => (it.variance ?? 0) !== 0).map((it, i) => (
                        <tr key={i} className="bg-red-50/30">
                          <td className="px-3 py-2 text-gray-800">{it.product}</td>
                          <td className="px-3 py-2 text-center text-gray-700">{it.sentQty}</td>
                          <td className="px-3 py-2 text-center text-gray-700">{it.receivedQty ?? 0}</td>
                          <td className="px-3 py-2 text-center"><span className="inline-flex items-center justify-center px-2 py-0.5 rounded text-xs font-bold bg-red-100 text-red-700">−{it.variance}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {tracking.discrepancyExplanation && (
                  <div className="p-3 bg-white rounded-lg border border-red-200">
                    <p className="text-xs font-semibold text-red-600 mb-1">Explanation on file:</p>
                    <p className="text-xs text-gray-700">{tracking.discrepancyExplanation}</p>
                  </div>
                )}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1.5">Resolution Notes / Manager Override</label>
                  <textarea rows={2} value={discrepancyExplanation} onChange={e => { setDiscrepancyExplanation(e.target.value); setErrors({}); }} placeholder="Manager resolution notes..." className={`w-full px-3 py-2 border rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-red-400 resize-none ${errors.explanation ? 'border-red-500' : 'border-gray-300'}`} />
                  {errors.explanation && <p className="text-xs text-red-500 mt-1">{errors.explanation}</p>}
                </div>
                <button onClick={resolveDiscrepancy} className="w-full py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-xl text-sm font-semibold transition-colors">
                  Resolve & Mark as Received
                </button>
              </div>
            </div>
          )}

          {/* ─── Matching Summary (after full confirmation) ─── */}
          {tracking.step === 3 && (
            <div className="mx-5 mt-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                <FileText className="w-4 h-4 text-gray-500" /> Quantity Matching Report
              </h3>
              <div className={`rounded-xl border-2 p-4 ${tracking.status === 'received' ? 'border-emerald-300 bg-emerald-50' : 'border-red-300 bg-red-50'}`}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    {tracking.status === 'received'
                      ? <CheckCircle className="w-5 h-5 text-emerald-600" />
                      : <AlertTriangle className="w-5 h-5 text-red-600" />}
                    <span className={`font-semibold text-sm ${tracking.status === 'received' ? 'text-emerald-700' : 'text-red-700'}`}>
                      {tracking.status === 'received' ? 'All Quantities Match' : 'Quantity Mismatch Detected'}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-xs">
                    <span className="text-gray-600">Sent: <strong>{totalSent}</strong></span>
                    <ArrowRight className="w-3 h-3 text-gray-400" />
                    <span className={tracking.status !== 'received' ? 'text-red-600' : 'text-emerald-600'}>Received: <strong>{totalReceived}</strong></span>
                  </div>
                </div>
                <div className="border border-current border-opacity-20 rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-white bg-opacity-50"><tr><th className="text-left px-3 py-2 font-semibold text-gray-600">Product</th><th className="text-center px-3 py-2 font-semibold text-gray-600">Sent</th><th className="text-center px-3 py-2 font-semibold text-gray-600">Received</th><th className="text-center px-3 py-2 font-semibold text-gray-600">Variance</th></tr></thead>
                    <tbody className="divide-y divide-white divide-opacity-30 bg-white bg-opacity-30">
                      {tracking.items.map((it, i) => (
                        <tr key={i}>
                          <td className="px-3 py-2 text-gray-800">{it.product}</td>
                          <td className="px-3 py-2 text-center text-gray-700">{it.sentQty}</td>
                          <td className="px-3 py-2 text-center font-semibold text-gray-800">{it.receivedQty ?? 0}</td>
                          <td className="px-3 py-2 text-center">
                            <span className={`inline-flex px-2 py-0.5 rounded font-bold ${(it.variance ?? 0) !== 0 ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                              {(it.variance ?? 0) === 0 ? '✓' : `−${it.variance}`}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* ─── Immutable Audit Log ─── */}
          <div className="mx-5 mt-5 mb-6">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <Lock className="w-4 h-4 text-gray-400" /> Immutable Audit Log
            </h3>
            {[tracking.dispatchLog, tracking.pickupLog, tracking.receivedLog].filter(Boolean).length === 0 ? (
              <div className="p-4 border border-dashed border-gray-300 rounded-xl text-center text-gray-400 text-xs">
                No confirmations recorded yet — complete the steps above to build the audit trail
              </div>
            ) : (
              <div className="space-y-2">
                {[
                  tracking.dispatchLog && { log: tracking.dispatchLog, step: 1, icon: Building2, color: 'border-indigo-200 bg-indigo-50/30' },
                  tracking.pickupLog  && { log: tracking.pickupLog,  step: 2, icon: Truck,     color: 'border-blue-200 bg-blue-50/30' },
                  tracking.receivedLog && { log: tracking.receivedLog, step: 3, icon: UserCheck, color: 'border-violet-200 bg-violet-50/30' },
                ].filter(Boolean).map((entry: any) => {
                  const Icon = entry.icon;
                  return (
                    <div key={entry.step} className={`flex items-start gap-3 p-3.5 rounded-xl border ${entry.color}`}>
                      <div className="w-7 h-7 rounded-full bg-white border border-gray-200 flex items-center justify-center flex-shrink-0">
                        <Icon className="w-3.5 h-3.5 text-gray-500" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-semibold text-gray-800">{entry.log.action}</span>
                          <span className="text-xs text-gray-400 whitespace-nowrap flex items-center gap-1"><CalendarClock className="w-3 h-3" />{entry.log.timestamp}</span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-xs text-gray-700 font-medium">{entry.log.user}</span>
                          <span className="text-xs text-gray-400">·</span>
                          <span className="text-xs text-gray-500">{entry.log.role}</span>
                        </div>
                        {entry.log.notes && <p className="text-xs text-gray-500 mt-1 italic">"{entry.log.notes}"</p>}
                      </div>
                      <Lock className="w-3.5 h-3.5 text-gray-300 flex-shrink-0 mt-1" />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
