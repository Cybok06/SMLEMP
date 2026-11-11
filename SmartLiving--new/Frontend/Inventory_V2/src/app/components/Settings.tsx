import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  Building2,
  Check,
  Edit,
  Eye,
  KeyRound,
  Lock,
  Package,
  Plus,
  Power,
  RotateCcw,
  Save,
  Search,
  Shield,
  TrendingUp,
  Users as UsersIcon,
  X,
} from 'lucide-react';
import { BranchesWarehouses } from './BranchesWarehouses';

export type PermissionAction = 'view' | 'create' | 'edit' | 'delete' | 'approve';

export type PagePermissions = {
  visible: boolean;
  view: boolean;
  create: boolean;
  edit: boolean;
  delete: boolean;
  approve: boolean;
};

export type Role = {
  id: string;
  name: string;
  description: string;
  template?: boolean;
  permissions: Record<string, PagePermissions>;
};

export type AppUser = {
  id: string;
  username: string;
  name: string;
  email: string;
  phone: string;
  roleId: string;
  roleName?: string;
  branch: string;
  status: 'active' | 'disabled';
  lastLogin: string;
  position: string;
  location: string;
  gender: string;
  startDate: string;
  mainAdmin: boolean;
  imageUrl?: string;
};

export const SYSTEM_PAGES = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'product-cards', label: 'Product Cards' },
  { id: 'customers', label: 'Customers & Completion' },
  { id: 'inventory', label: 'Inventory' },
  { id: 'warehouse', label: 'Warehouse Operations' },
  { id: 'submitted-cards', label: 'Submitted Cards' },
  { id: 'undelivered-items', label: 'Undelivered Items' },
  { id: 'fulfillment', label: 'Fulfillment & Delivery' },
  { id: 'suppliers', label: 'Suppliers & Purchases' },
  { id: 'audit', label: 'Audit & Accountability' },
  { id: 'reports', label: 'Reports & Analytics' },
  { id: 'settings', label: 'Settings' },
];

const TABS = [
  { id: 'users', label: 'Users', icon: UsersIcon },
  { id: 'roles', label: 'Roles & Permissions', icon: Shield },
  { id: 'branches-warehouses', label: 'Branches & Warehouses', icon: Building2 },
  { id: 'inventory-rules', label: 'Inventory Rules', icon: Package },
  { id: 'forecast-rules', label: 'Forecast Rules', icon: TrendingUp },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'danger', label: 'Danger Zone', icon: AlertTriangle },
] as const;

type TabId = (typeof TABS)[number]['id'];

type Props = {
  roles: Role[];
  setRoles: (r: Role[]) => void;
  currentRoleId: string;
  setCurrentRoleId: (id: string) => void;
};

type UserFormValues = {
  username: string;
  name: string;
  email: string;
  phone: string;
  roleId: string;
  branch: string;
  status: 'active' | 'disabled';
  position: string;
  location: string;
  gender: string;
  startDate: string;
  imageUrl: string;
  password?: string;
};

const ASSIGNABLE_ROLE_IDS = ['warehouse-manager', 'inventory-user'] as const;

const emptyPerms = (): PagePermissions => ({
  visible: false,
  view: false,
  create: false,
  edit: false,
  delete: false,
  approve: false,
});

const onlyView = (): PagePermissions => ({
  visible: true,
  view: true,
  create: false,
  edit: false,
  delete: false,
  approve: false,
});

const buildPerms = (
  enabled: Record<string, Partial<PagePermissions>>
): Record<string, PagePermissions> => {
  const out: Record<string, PagePermissions> = {};
  SYSTEM_PAGES.forEach((page) => {
    out[page.id] = enabled[page.id] ? { ...emptyPerms(), ...enabled[page.id] } : emptyPerms();
  });
  return out;
};

export const DEFAULT_ROLES: Role[] = [
  {
    id: 'admin',
    name: 'Main Admin',
    description: 'Full access to all modules and configuration',
    template: true,
    permissions: SYSTEM_PAGES.reduce(
      (acc, page) => ({ ...acc, [page.id]: { visible: true, view: true, create: true, edit: true, delete: true, approve: true } }),
      {} as Record<string, PagePermissions>
    ),
  },
  {
    id: 'inventory-user',
    name: 'Inventory User',
    description: 'Read-only dashboard and inventory access',
    template: true,
    permissions: buildPerms({
      'undelivered-items': { ...onlyView(), edit: true },
      dashboard: onlyView(),
      inventory: { ...onlyView(), edit: true },
      'submitted-cards': { visible: true, view: true, create: false, edit: true, delete: false, approve: false },
    }),
  },
  {
    id: 'warehouse-manager',
    name: 'Warehouse Manager',
    description: 'Manages warehouse operations and inventory movements',
    template: true,
    permissions: buildPerms({
      'undelivered-items': { ...onlyView(), edit: true },
      dashboard: onlyView(),
      inventory: { visible: true, view: true, create: true, edit: true, delete: false, approve: true },
      warehouse: { visible: true, view: true, create: true, edit: true, delete: true, approve: true },
      'submitted-cards': { visible: true, view: true, create: false, edit: true, delete: false, approve: true },
      fulfillment: { visible: true, view: true, create: true, edit: true, delete: false, approve: false },
    }),
  },
];

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

export function Settings({ roles, setRoles, currentRoleId, setCurrentRoleId }: Props) {
  const [activeTab, setActiveTab] = useState<TabId>('users');
  const [users, setUsers] = useState<AppUser[]>([]);
  const [editingRoleId, setEditingRoleId] = useState<string>('admin');
  const [showAddUser, setShowAddUser] = useState(false);
  const [editingUser, setEditingUser] = useState<AppUser | null>(null);
  const [search, setSearch] = useState('');
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadSettings = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api<{ ok: true; roles: Role[]; users: AppUser[] }>('/api/inventory/settings/bootstrap');
      setRoles(data.roles);
      setUsers(data.users);
      setEditingRoleId((current) => data.roles.some((role) => role.id === current) ? current : (data.roles[0]?.id || 'admin'));
      if (!data.roles.some((role) => role.id === currentRoleId) && data.roles[0]?.id) {
        setCurrentRoleId(data.roles[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  useEffect(() => {
    if (!roles.some((role) => role.id === editingRoleId) && roles[0]?.id) {
      setEditingRoleId(roles[0].id);
    }
  }, [roles, editingRoleId]);

  const editingRole = roles.find((role) => role.id === editingRoleId) || roles[0] || DEFAULT_ROLES[0];

  const filteredUsers = useMemo(
    () =>
      users.filter((user) => {
        const q = search.trim().toLowerCase();
        if (!q) return true;
        return (
          user.name.toLowerCase().includes(q) ||
          user.email.toLowerCase().includes(q) ||
          user.username.toLowerCase().includes(q)
        );
      }),
    [users, search]
  );

  const updateRolePermission = (
    roleId: string,
    pageId: string,
    key: keyof PagePermissions,
    value: boolean
  ) => {
    setRoles(
      roles.map((role) =>
        role.id === roleId
          ? {
              ...role,
              permissions: {
                ...role.permissions,
                [pageId]: { ...(role.permissions[pageId] || emptyPerms()), [key]: value },
              },
            }
          : role
      )
    );
    setDirty(true);
  };

  const saveRoleChanges = async () => {
    if (!editingRole) return;
    setSaving(true);
    setError('');
    try {
      const data = await api<{ ok: true; role: Role }>(`/api/inventory/settings/roles/${editingRole.id}`, {
        method: 'PUT',
        body: JSON.stringify(editingRole),
      });
      setRoles(roles.map((role) => (role.id === editingRole.id ? data.role : role)));
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save role.');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveUser = async (values: UserFormValues) => {
    setSaving(true);
    setError('');
    try {
      if (editingUser) {
        const data = await api<{ ok: true; user: AppUser }>(`/api/inventory/settings/users/${editingUser.id}`, {
          method: 'PATCH',
          body: JSON.stringify(values),
        });
        setUsers(users.map((user) => (user.id === editingUser.id ? data.user : user)));
        setEditingUser(null);
      } else {
        const data = await api<{ ok: true; user: AppUser }>('/api/inventory/settings/users', {
          method: 'POST',
          body: JSON.stringify(values),
        });
        setUsers([...users, data.user].sort((a, b) => a.name.localeCompare(b.name)));
        setShowAddUser(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save user.');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleStatus = async (userId: string) => {
    setSaving(true);
    setError('');
    try {
      const data = await api<{ ok: true; user: AppUser }>(`/api/inventory/settings/users/${userId}/toggle-status`, {
        method: 'POST',
      });
      setUsers(users.map((user) => (user.id === userId ? data.user : user)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update status.');
    } finally {
      setSaving(false);
    }
  };

  const handleResetPassword = async (user: AppUser) => {
    const password = window.prompt(`Enter a new password for ${user.name}`);
    if (!password) return;
    setSaving(true);
    setError('');
    try {
      await api<{ ok: true }>(`/api/inventory/settings/users/${user.id}/reset-password`, {
        method: 'POST',
        body: JSON.stringify({ password }),
      });
      window.alert(`Password updated for ${user.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset password.');
    } finally {
      setSaving(false);
    }
  };

  const handleAssignRole = async (userId: string, roleId: string) => {
    setSaving(true);
    setError('');
    try {
      const data = await api<{ ok: true; user: AppUser }>(`/api/inventory/settings/users/${userId}/assign-role`, {
        method: 'POST',
        body: JSON.stringify({ roleId }),
      });
      setUsers(users.map((user) => (user.id === userId ? data.user : user)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to assign role.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Settings & User Management</h1>
          <p className="text-gray-600 mt-1">
            Manage inventory users and role permissions from the live database.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => setShowAddUser(true)}
            className="flex items-center gap-2 px-3.5 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <Plus className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">Add User</span>
          </button>
          <button
            onClick={saveRoleChanges}
            disabled={!dirty || saving || activeTab !== 'roles'}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-lg shadow-sm transition-colors ${
              dirty && activeTab === 'roles' && !saving
                ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                : 'bg-indigo-300 text-white cursor-not-allowed'
            }`}
          >
            <Save className="w-4 h-4" />
            <span className="text-sm font-medium">{saving ? 'Saving...' : 'Save Changes'}</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="border border-rose-200 bg-rose-50 text-rose-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <div className="bg-gradient-to-r from-indigo-50 to-violet-50 border border-indigo-100 rounded-xl p-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-white rounded-lg border border-indigo-100">
            <Shield className="w-5 h-5 text-indigo-600" />
          </div>
          <div>
            <div className="text-sm font-semibold text-gray-900">Sidebar role preview</div>
            <div className="text-xs text-gray-600">
              The inventory sidebar reflects the selected role's visible pages.
            </div>
          </div>
        </div>
        <select
          value={currentRoleId}
          onChange={(e) => setCurrentRoleId(e.target.value)}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          {roles.map((role) => (
            <option key={role.id} value={role.id}>
              {role.name}
            </option>
          ))}
        </select>
      </div>

      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex items-center gap-1 px-2 pt-2 border-b border-gray-200 overflow-x-auto">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            const danger = tab.id === 'danger';
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg whitespace-nowrap border-b-2 -mb-px transition-colors ${
                  active
                    ? danger
                      ? 'text-rose-600 border-rose-600 bg-rose-50/40'
                      : 'text-indigo-600 border-indigo-600 bg-indigo-50/40'
                    : 'text-gray-600 border-transparent hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="p-5">
          {loading ? (
            <div className="text-sm text-gray-500">Loading settings...</div>
          ) : (
            <>
              {activeTab === 'users' && (
                <UsersTab
                  users={filteredUsers}
                  roles={roles}
                  search={search}
                  setSearch={setSearch}
                  onEdit={setEditingUser}
                  onToggleStatus={handleToggleStatus}
                  onResetPassword={handleResetPassword}
                />
              )}
              {activeTab === 'roles' && (
                <RolesTab
                  roles={roles}
                  users={users}
                  editingRoleId={editingRoleId}
                  setEditingRoleId={setEditingRoleId}
                  editingRole={editingRole}
                  updateRolePermission={updateRolePermission}
                  onAssignRole={handleAssignRole}
                />
              )}
              {activeTab === 'branches-warehouses' && <BranchesWarehouses />}
              {activeTab === 'inventory-rules' && <InventoryRulesTab />}
              {activeTab === 'forecast-rules' && <ForecastRulesTab />}
              {activeTab === 'notifications' && <NotificationsTab />}
              {activeTab === 'danger' && <DangerZoneTab />}
            </>
          )}
        </div>
      </div>

      {showAddUser && (
        <Modal title="Add Inventory User" onClose={() => setShowAddUser(false)}>
          <UserForm
            roles={roles}
            onSave={handleSaveUser}
            onCancel={() => setShowAddUser(false)}
          />
        </Modal>
      )}

      {editingUser && (
        <Modal title={`Edit ${editingUser.name}`} onClose={() => setEditingUser(null)}>
          <UserForm
            roles={roles}
            initialUser={editingUser}
            onSave={handleSaveUser}
            onCancel={() => setEditingUser(null)}
          />
        </Modal>
      )}
    </div>
  );
}

function UsersTab({
  users,
  roles,
  search,
  setSearch,
  onEdit,
  onToggleStatus,
  onResetPassword,
}: {
  users: AppUser[];
  roles: Role[];
  search: string;
  setSearch: (s: string) => void;
  onEdit: (user: AppUser) => void;
  onToggleStatus: (userId: string) => void;
  onResetPassword: (user: AppUser) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-4 gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search users..."
            className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        <span className="text-sm text-gray-500">{users.length} users</span>
      </div>

      <div className="overflow-x-auto border border-gray-200 rounded-lg">
        <table className="w-full">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {['Name', 'Username', 'Email', 'Phone', 'Role', 'Branch', 'Status', 'Last login', 'Actions'].map((heading) => (
                <th
                  key={heading}
                  className="text-left px-4 py-3 text-xs font-semibold text-gray-600 uppercase tracking-wide"
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {users.map((user) => {
              const role = roles.find((item) => item.id === user.roleId);
              return (
                <tr key={user.id} className="hover:bg-gray-50/60">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 bg-indigo-100 text-indigo-700 rounded-full flex items-center justify-center text-xs font-semibold">
                        {user.name
                          .split(' ')
                          .map((part) => part[0])
                          .slice(0, 2)
                          .join('')}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-gray-900">{user.name}</div>
                        <div className="text-xs text-gray-500">{user.mainAdmin ? 'Main admin' : user.position || user.id}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{user.username}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{user.email || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{user.phone || '-'}</td>
                  <td className="px-4 py-3">
                    <span className="inline-flex px-2 py-1 text-xs rounded-md bg-indigo-50 text-indigo-700 border border-indigo-100">
                      {role?.name || user.roleName || user.roleId}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{user.branch || '-'}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center gap-1.5 px-2 py-1 text-xs rounded-full border ${
                        user.status === 'active'
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-gray-100 text-gray-600 border-gray-200'
                      }`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${
                          user.status === 'active' ? 'bg-emerald-500' : 'bg-gray-400'
                        }`}
                      />
                      {user.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{user.lastLogin || '-'}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => window.alert(`${user.name}\n${user.email || '-'}\n${user.phone || '-'}\n${user.position || '-'}`)}
                        className="p-1.5 hover:bg-gray-100 rounded-md"
                        title="View"
                      >
                        <Eye className="w-4 h-4 text-gray-500" />
                      </button>
                      <button
                        onClick={() => onEdit(user)}
                        className="p-1.5 hover:bg-gray-100 rounded-md"
                        title="Edit"
                      >
                        <Edit className="w-4 h-4 text-gray-500" />
                      </button>
                      <button
                        onClick={() => onResetPassword(user)}
                        className="p-1.5 hover:bg-gray-100 rounded-md"
                        title="Reset password"
                      >
                        <KeyRound className="w-4 h-4 text-gray-500" />
                      </button>
                      {!user.mainAdmin && (
                        <button
                          onClick={() => onToggleStatus(user.id)}
                          className="p-1.5 hover:bg-rose-50 rounded-md"
                          title={user.status === 'active' ? 'Disable' : 'Enable'}
                        >
                          <Power className={`w-4 h-4 ${user.status === 'active' ? 'text-rose-500' : 'text-emerald-500'}`} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RolesTab({
  roles,
  users,
  editingRoleId,
  setEditingRoleId,
  editingRole,
  updateRolePermission,
  onAssignRole,
}: {
  roles: Role[];
  users: AppUser[];
  editingRoleId: string;
  setEditingRoleId: (id: string) => void;
  editingRole: Role;
  updateRolePermission: (roleId: string, pageId: string, key: keyof PagePermissions, value: boolean) => void;
  onAssignRole: (userId: string, roleId: string) => void;
}) {
  const assignedUsers = users.filter((user) => user.roleId === editingRoleId);
  const enabledPages = SYSTEM_PAGES.filter((page) => editingRole.permissions[page.id]?.visible);
  const assignableRoles = roles.filter((role) => ASSIGNABLE_ROLE_IDS.includes(role.id as (typeof ASSIGNABLE_ROLE_IDS)[number]));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-5">
      <div className="space-y-2">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1">
          Roles
        </div>
        {roles.map((role) => {
          const active = role.id === editingRoleId;
          return (
            <button
              key={role.id}
              onClick={() => setEditingRoleId(role.id)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                active ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-white hover:bg-gray-50'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium text-gray-900">{role.name}</div>
                {role.template && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                    Template
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">{role.description}</div>
            </button>
          );
        })}
      </div>

      <div className="space-y-5">
        <div className="bg-gradient-to-br from-indigo-50 to-violet-50 border border-indigo-100 rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-wide text-indigo-700 font-semibold">
                Role
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mt-1">{editingRole.name}</h3>
              <p className="text-sm text-gray-600 mt-1">{editingRole.description}</p>
            </div>
            <div className="text-xs text-indigo-700 font-medium bg-white border border-indigo-100 rounded-md px-3 py-2">
              Fixed inventory roles
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3 mt-4">
            <MetricCard label="Assigned users" value={String(assignedUsers.length)} />
            <MetricCard label="Enabled pages" value={`${enabledPages.length}/${SYSTEM_PAGES.length}`} />
            <MetricCard
              label="Allowed actions"
              value={String(
                SYSTEM_PAGES.reduce((sum, page) => {
                  const perm = editingRole.permissions[page.id] || emptyPerms();
                  return sum + ['view', 'create', 'edit', 'delete', 'approve'].filter((key) => perm[key as PermissionAction]).length;
                }, 0)
              )}
            />
          </div>
        </div>

        <div className="border border-gray-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200">
            <div className="font-semibold text-gray-900 text-sm">Permission Matrix</div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
                    Page
                  </th>
                  <th className="px-3 py-3 text-xs font-semibold text-gray-600 uppercase tracking-wide text-center">
                    Visible
                  </th>
                  {(['view', 'create', 'edit', 'delete', 'approve'] as const).map((key) => (
                    <th key={key} className="px-3 py-3 text-xs font-semibold text-gray-600 uppercase tracking-wide text-center">
                      {key}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {SYSTEM_PAGES.map((page) => {
                  const perm = editingRole.permissions[page.id] || emptyPerms();
                  return (
                    <tr key={page.id} className="hover:bg-gray-50/60">
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-gray-900">{page.label}</div>
                      </td>
                      <td className="px-3 py-3 text-center">
                        <Toggle
                          checked={perm.visible}
                          onChange={(value) => updateRolePermission(editingRole.id, page.id, 'visible', value)}
                        />
                      </td>
                      {(['view', 'create', 'edit', 'delete', 'approve'] as const).map((key) => (
                        <td key={key} className="px-3 py-3 text-center">
                          <Checkbox
                            checked={perm[key]}
                            disabled={!perm.visible}
                            onChange={(value) => updateRolePermission(editingRole.id, page.id, key, value)}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-gray-200 rounded-xl">
          <div className="px-4 py-3 border-b border-gray-200">
            <div className="font-semibold text-gray-900 text-sm">Assign User Roles</div>
            <div className="text-xs text-gray-500 mt-1">
              Change a user's sidebar role here. The new role takes effect when the user logs in again.
            </div>
          </div>
          <div className="divide-y divide-gray-100">
            {users.map((user) => (
              <div key={user.id} className="px-4 py-3 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-gray-900">{user.name}</div>
                  <div className="text-xs text-gray-500">{user.email || user.username}</div>
                </div>
                {user.mainAdmin ? (
                  <span className="inline-flex px-2.5 py-1 text-xs rounded-md bg-indigo-50 text-indigo-700 border border-indigo-100">
                    Main Admin
                  </span>
                ) : (
                  <select
                    value={user.roleId}
                    onChange={(e) => onAssignRole(user.id, e.target.value)}
                    className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 min-w-[220px]"
                  >
                    {assignableRoles.map((role) => (
                      <option key={role.id} value={role.id}>
                        {role.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="border border-gray-200 rounded-xl">
          <div className="px-4 py-3 border-b border-gray-200">
            <div className="font-semibold text-gray-900 text-sm">Assigned Users</div>
          </div>
          {assignedUsers.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-gray-500">No users assigned to this role.</div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {assignedUsers.map((user) => (
                <li key={user.id} className="px-4 py-3 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 bg-indigo-100 text-indigo-700 rounded-full flex items-center justify-center text-xs font-semibold">
                      {user.name
                        .split(' ')
                        .map((part) => part[0])
                        .slice(0, 2)
                        .join('')}
                    </div>
                    <div>
                      <div className="text-sm font-medium text-gray-900">{user.name}</div>
                      <div className="text-xs text-gray-500">{user.email || user.username}</div>
                    </div>
                  </div>
                  <span className="text-xs text-gray-500">{user.mainAdmin ? 'Main Admin' : user.roleName}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white rounded-lg p-3 border border-indigo-100">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-gray-900 mt-0.5">{value}</div>
    </div>
  );
}

function InventoryRulesTab() {
  return (
    <div className="space-y-3 max-w-3xl">
      <SettingRow title="Inventory rules">
        Inventory rules are still static in this build. Users and roles are now connected to the live inventory database.
      </SettingRow>
    </div>
  );
}

function ForecastRulesTab() {
  return (
    <div className="space-y-3 max-w-3xl">
      <SettingRow title="Forecast rules">
        Forecast rules remain unchanged for now. The current update focuses on live user and permission management.
      </SettingRow>
    </div>
  );
}

function NotificationsTab() {
  return (
    <div className="space-y-3 max-w-3xl">
      <SettingRow title="Notifications">
        Notification preferences remain unchanged in this pass.
      </SettingRow>
    </div>
  );
}

function DangerZoneTab() {
  const actions = [
    {
      title: 'Reverse stock movement',
      description: 'Roll back a confirmed stock-in or stock-out entry. Requires audit reason.',
      icon: RotateCcw,
    },
    {
      title: 'Force stock adjustment',
      description: 'Override stock counts without the standard approval workflow.',
      icon: AlertTriangle,
    },
    {
      title: 'Reset inventory',
      description: 'Wipe all current inventory data and reset to a clean baseline.',
      icon: Trash2,
    },
  ];

  return (
    <div className="space-y-4">
      <div className="border border-rose-200 bg-rose-50/50 rounded-lg p-4 flex items-start gap-3">
        <Lock className="w-5 h-5 text-rose-600 mt-0.5" />
        <div>
          <div className="text-sm font-semibold text-rose-700">Restricted to administrators</div>
          <div className="text-xs text-rose-700/80 mt-1">
            These actions are intentionally not wired into the new React settings flow yet.
          </div>
        </div>
      </div>
      <div className="space-y-3">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <div
              key={action.title}
              className="flex items-start justify-between gap-4 p-4 border border-rose-200 rounded-lg bg-white"
            >
              <div className="flex items-start gap-3">
                <div className="p-2 bg-rose-50 rounded-lg">
                  <Icon className="w-4 h-4 text-rose-600" />
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-900">{action.title}</div>
                  <div className="text-xs text-gray-600 mt-0.5">{action.description}</div>
                </div>
              </div>
              <button className="px-3 py-1.5 text-sm rounded-md border border-rose-300 text-rose-700 hover:bg-rose-50">
                Restricted
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SettingRow({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 p-4 border border-gray-200 rounded-lg bg-white">
      <div>
        <div className="text-sm font-medium text-gray-900">{title}</div>
        <div className="text-xs text-gray-500 mt-0.5">{children}</div>
      </div>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1 ${
        checked ? 'bg-indigo-600' : 'bg-gray-300'
      } ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

function Checkbox({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      disabled={disabled}
      className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${
        checked ? 'bg-indigo-600 border-indigo-600 text-white' : 'bg-white border-gray-300 hover:border-gray-400'
      } ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
    >
      {checked && <Check className="w-3.5 h-3.5" />}
    </button>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-md">
            <X className="w-5 h-5 text-gray-600" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function UserForm({
  roles,
  initialUser,
  onSave,
  onCancel,
}: {
  roles: Role[];
  initialUser?: AppUser | null;
  onSave: (values: UserFormValues) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<UserFormValues>({
    username: initialUser?.username || '',
    name: initialUser?.name || '',
    email: initialUser?.email || '',
    phone: initialUser?.phone || '',
    roleId: initialUser?.mainAdmin ? 'admin' : (initialUser?.roleId || 'inventory-user'),
    branch: initialUser?.branch || '',
    status: initialUser?.status || 'active',
    position: initialUser?.position || '',
    location: initialUser?.location || '',
    gender: initialUser?.gender || '',
    startDate: initialUser?.startDate || '',
    imageUrl: initialUser?.imageUrl || '',
    password: '',
  });

  const inputClass =
    'w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave(form);
      }}
      className="space-y-4"
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Username">
          <input
            required
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            className={inputClass}
            disabled={Boolean(initialUser?.mainAdmin)}
          />
        </Field>
        <Field label="Full name">
          <input
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className={inputClass}
          />
        </Field>
      </div>

      {!initialUser && (
        <Field label="Password">
          <input
            type="password"
            required
            value={form.password || ''}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            className={inputClass}
          />
        </Field>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Email">
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Phone">
          <input
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
            className={inputClass}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Assigned role">
          <select
            value={form.roleId}
            onChange={(e) => setForm({ ...form, roleId: e.target.value })}
            className={inputClass}
            disabled={Boolean(initialUser?.mainAdmin)}
          >
            {initialUser?.mainAdmin && (
              <option value="admin">Main Admin</option>
            )}
            {roles.filter((role) => ASSIGNABLE_ROLE_IDS.includes(role.id as (typeof ASSIGNABLE_ROLE_IDS)[number])).map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Status">
          <select
            value={form.status}
            onChange={(e) => setForm({ ...form, status: e.target.value as 'active' | 'disabled' })}
            className={inputClass}
            disabled={Boolean(initialUser?.mainAdmin)}
          >
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
        </Field>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Branch">
          <input
            value={form.branch}
            onChange={(e) => setForm({ ...form, branch: e.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Position">
          <input
            value={form.position}
            onChange={(e) => setForm({ ...form, position: e.target.value })}
            className={inputClass}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Field label="Location">
          <input
            value={form.location}
            onChange={(e) => setForm({ ...form, location: e.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Gender">
          <input
            value={form.gender}
            onChange={(e) => setForm({ ...form, gender: e.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Start date">
          <input
            type="date"
            value={form.startDate}
            onChange={(e) => setForm({ ...form, startDate: e.target.value })}
            className={inputClass}
          />
        </Field>
      </div>

      <Field label="Image URL">
        <input
          value={form.imageUrl}
          onChange={(e) => setForm({ ...form, imageUrl: e.target.value })}
          className={inputClass}
        />
      </Field>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="submit"
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
        >
          {initialUser ? 'Save User' : 'Create User'}
        </button>
      </div>
    </form>
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
      <label className="block text-sm font-medium text-gray-700 mb-1.5">{label}</label>
      {children}
    </div>
  );
}
