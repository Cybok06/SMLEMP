import { useEffect, useMemo, useState } from 'react';
import {
  Building2,
  Check,
  ChevronRight,
  Download,
  Edit,
  Eye,
  MapPin,
  Package,
  Phone,
  Plus,
  Power,
  Search,
  User,
  Warehouse,
  X,
  AlertCircle,
} from 'lucide-react';

export type Branch = {
  id: string;
  name: string;
  code: string;
  manager: string;
  location: string;
  phone: string;
  status: 'active' | 'inactive';
  totalWarehouses: number;
  totalStockUnits: number;
};

export type WarehouseLocation = {
  id: string;
  branchId: string;
  name: string;
  code: string;
  type: 'main-storage' | 'room' | 'dispatch' | 'receiving' | 'damaged' | 'returned' | 'delivery-holding';
  responsibleUser: string;
  stockUnits: number;
  capacity: number;
  status: 'active' | 'inactive';
  notes: string;
};

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.error || `Request failed with status ${response.status}`);
  }
  return data as T;
}

export function BranchesWarehouses() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [locationsMap, setLocationsMap] = useState<Record<string, WarehouseLocation[]>>({});
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddWarehouseModal, setShowAddWarehouseModal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api<{ ok: true; branches: Branch[]; locations: Record<string, WarehouseLocation[]> }>(
        '/api/inventory/settings/branches-warehouses'
      );
      setBranches(data.branches);
      setLocationsMap(data.locations || {});
      setSelectedBranchId((current) => {
        if (current && data.branches.some((branch) => branch.id === current)) return current;
        return data.branches[0]?.id || null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load branches.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const selectedBranch = branches.find((branch) => branch.id === selectedBranchId) || null;
  const branchWarehouses = selectedBranch ? (locationsMap[selectedBranch.id] || []) : [];

  const filteredBranches = useMemo(
    () =>
      branches.filter((branch) => {
        const q = searchQuery.trim().toLowerCase();
        if (!q) return true;
        return (
          branch.name.toLowerCase().includes(q) ||
          branch.code.toLowerCase().includes(q) ||
          branch.manager.toLowerCase().includes(q)
        );
      }),
    [branches, searchQuery]
  );

  const getStatusColor = (status: string) =>
    status === 'active'
      ? 'text-green-600 bg-green-50 border-green-200'
      : 'text-gray-600 bg-gray-50 border-gray-200';

  const getTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      'main-storage': 'Main Storage',
      room: 'Room',
      dispatch: 'Dispatch Area',
      receiving: 'Receiving Area',
      damaged: 'Damaged Area',
      returned: 'Returned Area',
      'delivery-holding': 'Delivery Holding',
    };
    return labels[type] || type;
  };

  const getTypeColor = (type: string) => {
    const colors: Record<string, string> = {
      'main-storage': 'text-indigo-600 bg-indigo-50 border-indigo-200',
      room: 'text-blue-600 bg-blue-50 border-blue-200',
      dispatch: 'text-purple-600 bg-purple-50 border-purple-200',
      receiving: 'text-green-600 bg-green-50 border-green-200',
      damaged: 'text-red-600 bg-red-50 border-red-200',
      returned: 'text-orange-600 bg-orange-50 border-orange-200',
      'delivery-holding': 'text-amber-600 bg-amber-50 border-amber-200',
    };
    return colors[type] || 'text-gray-600 bg-gray-50 border-gray-200';
  };

  const exportPdf = async () => {
    setExporting(true);
    setError('');
    try {
      const response = await fetch('/api/inventory/settings/branches-warehouses/export.pdf', {
        credentials: 'same-origin',
      });
      if (!response.ok) {
        throw new Error(`Export failed with status ${response.status}`);
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="?([^"]+)"?/i);
      anchor.href = url;
      anchor.download = match?.[1] || 'inventory_branches_warehouses.pdf';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to export PDF.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      {showAddWarehouseModal && selectedBranch && (
        <AddWarehouseModal
          branch={selectedBranch}
          onClose={() => setShowAddWarehouseModal(false)}
          onSaved={async () => {
            setShowAddWarehouseModal(false);
            await loadData();
          }}
        />
      )}

      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Branches & Warehouses</h3>
            <p className="text-sm text-gray-600 mt-1">Branches are sourced from manager records. Add locations or warehouses under each branch.</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={exportPdf}
              disabled={exporting || loading}
              className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed transition-colors text-sm"
            >
              <Download className="w-4 h-4 text-gray-600" />
              <span className="font-medium text-gray-700">{exporting ? 'Exporting...' : 'Export PDF'}</span>
            </button>
            <button
              onClick={() => setShowAddWarehouseModal(true)}
              disabled={!selectedBranchId}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors text-sm"
            >
              <Plus className="w-4 h-4" />
              <span className="font-medium">Add Location / Warehouse</span>
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-rose-200 bg-rose-50 text-rose-700 rounded-lg px-4 py-3 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-sm text-gray-500">Loading branches...</div>
        ) : (
          <div className="grid grid-cols-12 gap-6">
            <div className="col-span-4 space-y-4">
              <div className="bg-white rounded-lg border border-gray-200 p-4">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Search branches..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-9 pr-4 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm"
                  />
                </div>
              </div>

              <div className="space-y-3">
                {filteredBranches.map((branch) => (
                  <button
                    key={branch.id}
                    onClick={() => setSelectedBranchId(branch.id)}
                    className={`w-full text-left p-4 rounded-lg border transition-all ${
                      selectedBranchId === branch.id
                        ? 'border-indigo-500 bg-indigo-50 shadow-sm'
                        : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
                    }`}
                  >
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Building2 className={`w-5 h-5 ${selectedBranchId === branch.id ? 'text-indigo-600' : 'text-gray-400'}`} />
                        <div>
                          <div className="font-semibold text-gray-900">{branch.name}</div>
                          <div className="text-xs text-gray-500">{branch.code}</div>
                        </div>
                      </div>
                      {selectedBranchId === branch.id && <ChevronRight className="w-5 h-5 text-indigo-600" />}
                    </div>

                    <div className="space-y-1.5 mt-3">
                      <div className="flex items-center gap-2 text-xs text-gray-600">
                        <User className="w-3.5 h-3.5" />
                        <span>{branch.manager}</span>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-gray-600">
                        <MapPin className="w-3.5 h-3.5" />
                        <span>{branch.location || '-'}</span>
                      </div>
                      <div className="flex items-center justify-between mt-2">
                        <div className="text-xs text-gray-600">
                          <span className="font-medium text-gray-900">{branch.totalWarehouses}</span> Locations
                        </div>
                        <div className="text-xs text-gray-600">
                          <span className="font-medium text-gray-900">{branch.totalStockUnits.toLocaleString()}</span> Units
                        </div>
                      </div>
                    </div>

                    <div className="mt-3 pt-3 border-t border-gray-200">
                      <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border font-medium ${getStatusColor(branch.status)}`}>
                        {branch.status === 'active' ? <Check className="w-3 h-3" /> : <Power className="w-3 h-3" />}
                        {branch.status}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="col-span-8 space-y-4">
              {selectedBranch ? (
                <>
                  <div className="bg-white rounded-lg border border-gray-200 p-6">
                    <div className="flex items-start justify-between mb-6">
                      <div>
                        <h3 className="text-xl font-semibold text-gray-900">{selectedBranch.name}</h3>
                        <p className="text-sm text-gray-600 mt-1">Branch Details & Overview</p>
                      </div>
                      <div className="text-xs text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-md px-3 py-2">
                        Branch source: managers collection
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-6">
                      <InfoRow label="Branch Code" value={selectedBranch.code} />
                      <InfoRow label="Manager" value={selectedBranch.manager} icon={User} />
                      <InfoRow label="Location" value={selectedBranch.location || '-'} icon={MapPin} />
                      <InfoRow label="Phone" value={selectedBranch.phone || '-'} icon={Phone} />
                      <InfoRow label="Total Locations" value={String(selectedBranch.totalWarehouses)} icon={Warehouse} />
                      <InfoRow label="Total Stock Units" value={selectedBranch.totalStockUnits.toLocaleString()} icon={Package} />
                    </div>
                  </div>

                  <div className="bg-white rounded-lg border border-gray-200 p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h4 className="font-semibold text-gray-900">Warehouses & Storage Locations</h4>
                      <button
                        onClick={() => setShowAddWarehouseModal(true)}
                        className="flex items-center gap-2 px-3 py-1.5 text-sm text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                      >
                        <Plus className="w-4 h-4" />
                        Add Location
                      </button>
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full">
                        <thead className="bg-gray-50 border-b border-gray-200">
                          <tr>
                            <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Name / Code</th>
                            <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
                            <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Responsible User</th>
                            <th className="text-center px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Stock Units</th>
                            <th className="text-center px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Capacity</th>
                            <th className="text-center px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                            <th className="text-right px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                          {branchWarehouses.map((warehouse) => {
                            const utilizationPct = warehouse.capacity > 0 ? Math.round((warehouse.stockUnits / warehouse.capacity) * 100) : 0;
                            return (
                              <tr key={warehouse.id} className="hover:bg-gray-50 transition-colors">
                                <td className="px-4 py-3">
                                  <div>
                                    <div className="font-medium text-gray-900">{warehouse.name}</div>
                                    <div className="text-xs text-gray-500">{warehouse.code}</div>
                                  </div>
                                </td>
                                <td className="px-4 py-3">
                                  <span className={`inline-flex items-center text-xs px-2 py-1 rounded border font-medium ${getTypeColor(warehouse.type)}`}>
                                    {getTypeLabel(warehouse.type)}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-sm text-gray-900">{warehouse.responsibleUser || '-'}</td>
                                <td className="px-4 py-3 text-center">
                                  <span className="font-semibold text-gray-900">{warehouse.stockUnits.toLocaleString()}</span>
                                </td>
                                <td className="px-4 py-3">
                                  <div className="flex flex-col items-center gap-1">
                                    <span className="text-sm font-medium text-gray-900">{warehouse.capacity.toLocaleString()}</span>
                                    <div className="w-full max-w-[100px]">
                                      <div className="w-full bg-gray-200 rounded-full h-1.5">
                                        <div
                                          className={`h-1.5 rounded-full ${utilizationPct > 85 ? 'bg-red-500' : utilizationPct > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                                          style={{ width: `${Math.min(utilizationPct, 100)}%` }}
                                        />
                                      </div>
                                    </div>
                                    <span className="text-xs text-gray-500">{utilizationPct}%</span>
                                  </div>
                                </td>
                                <td className="px-4 py-3 text-center">
                                  <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border font-medium ${getStatusColor(warehouse.status)}`}>
                                    {warehouse.status === 'active' ? <Check className="w-3 h-3" /> : <Power className="w-3 h-3" />}
                                    {warehouse.status}
                                  </span>
                                </td>
                                <td className="px-4 py-3">
                                  <div className="flex items-center justify-end gap-1">
                                    <button className="p-1.5 hover:bg-gray-100 rounded transition-colors">
                                      <Eye className="w-4 h-4 text-gray-600" />
                                    </button>
                                    <button className="p-1.5 hover:bg-gray-100 rounded transition-colors">
                                      <Edit className="w-4 h-4 text-gray-600" />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>

                      {branchWarehouses.length === 0 && (
                        <div className="py-12 text-center">
                          <Warehouse className="w-12 h-12 text-gray-400 mx-auto mb-3" />
                          <h4 className="font-medium text-gray-900 mb-1">No Locations Yet</h4>
                          <p className="text-sm text-gray-600 mb-4">Add a warehouse or room under this branch to start structuring inventory storage.</p>
                          <button
                            onClick={() => setShowAddWarehouseModal(true)}
                            className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-sm"
                          >
                            <Plus className="w-4 h-4" />
                            Add First Location
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
                  <Building2 className="w-12 h-12 text-gray-400 mx-auto mb-3" />
                  <h4 className="font-medium text-gray-900 mb-1">No Branches Found</h4>
                  <p className="text-sm text-gray-600">Branches are pulled from manager records in the users collection.</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function InfoRow({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div>
      <div className="text-sm text-gray-600 mb-1">{label}</div>
      <div className="flex items-center gap-2">
        {Icon ? <Icon className="w-4 h-4 text-gray-400" /> : null}
        <span className="font-medium text-gray-900">{value}</span>
      </div>
    </div>
  );
}

function AddWarehouseModal({
  branch,
  onClose,
  onSaved,
}: {
  branch: Branch;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [formData, setFormData] = useState({
    name: '',
    code: '',
    type: 'main-storage' as WarehouseLocation['type'],
    responsibleUser: '',
    capacity: '',
    notes: '',
    status: 'active' as 'active' | 'inactive',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const warehouseTypes: { value: WarehouseLocation['type']; label: string }[] = [
    { value: 'main-storage', label: 'Main Storage Room' },
    { value: 'room', label: 'Room' },
    { value: 'dispatch', label: 'Dispatch Area' },
    { value: 'receiving', label: 'Receiving Area' },
    { value: 'damaged', label: 'Damaged Area' },
    { value: 'returned', label: 'Returned Area' },
    { value: 'delivery-holding', label: 'Delivery Holding Area' },
  ];

  const submit = async () => {
    setSaving(true);
    setError('');
    try {
      await api<{ ok: true }>(`/api/inventory/settings/branches/${encodeURIComponent(branch.id)}/locations`, {
        method: 'POST',
        body: JSON.stringify({
          name: formData.name,
          code: formData.code,
          type: formData.type,
          responsibleUser: formData.responsibleUser,
          capacity: Number(formData.capacity || 0),
          notes: formData.notes,
          status: formData.status,
        }),
      });
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create location.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-gray-200">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold text-gray-900">Add Location / Warehouse</h3>
              <p className="text-sm text-gray-600 mt-1">Parent Branch: {branch.name}</p>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
              <X className="w-5 h-5 text-gray-600" />
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4">
          {error && (
            <div className="border border-rose-200 bg-rose-50 text-rose-700 rounded-lg px-4 py-3 text-sm">
              {error}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Field label="Location / Warehouse Name *">
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g., Room A, Main Storage"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </Field>
            <Field label="Location Code *">
              <input
                type="text"
                value={formData.code}
                onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                placeholder="e.g., KASOA-RA"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </Field>
            <Field label="Type *">
              <select
                value={formData.type}
                onChange={(e) => setFormData({ ...formData, type: e.target.value as WarehouseLocation['type'] })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              >
                {warehouseTypes.map((type) => (
                  <option key={type.value} value={type.value}>{type.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Responsible User">
              <input
                type="text"
                value={formData.responsibleUser}
                onChange={(e) => setFormData({ ...formData, responsibleUser: e.target.value })}
                placeholder="User responsible for this location"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </Field>
            <Field label="Capacity (Units)">
              <input
                type="number"
                value={formData.capacity}
                onChange={(e) => setFormData({ ...formData, capacity: e.target.value })}
                placeholder="Maximum capacity"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </Field>
            <Field label="Status">
              <select
                value={formData.status}
                onChange={(e) => setFormData({ ...formData, status: e.target.value as 'active' | 'inactive' })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </Field>
            <div className="col-span-2">
              <Field label="Notes">
                <textarea
                  value={formData.notes}
                  onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                  rows={3}
                  placeholder="Additional information about this location..."
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </Field>
            </div>
          </div>

          <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-orange-600 mt-0.5 flex-shrink-0" />
              <div>
                <h4 className="font-medium text-orange-900 mb-1">Important Rules</h4>
                <ul className="text-sm text-orange-800 space-y-1">
                  <li>- Each location must belong to an existing branch from the managers list.</li>
                  <li>- Deactivated locations should not receive new stock.</li>
                  <li>- Branches themselves are managed through manager records, not created here.</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <div className="p-6 border-t border-gray-200 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving}
            className="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:bg-indigo-300 transition-colors"
          >
            {saving ? 'Saving...' : 'Create Location'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-2">{label}</label>
      {children}
    </div>
  );
}
