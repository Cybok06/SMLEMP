import { createContext, useContext, useMemo, type ReactNode } from 'react';
import type { Role, PermissionAction } from '../components/Settings';

// ─── Types ─────────────────────────────────────────────────────────────────────

export type AccessLevel = 'full' | 'restricted';

export interface RoleAccessContextValue {
  canAccessPage: (page: string, action?: PermissionAction) => boolean;
  /** True if the current role can see cost prices, supplier pricing, profit margins */
  canViewPricing: boolean;
  /** True if the current role can see financial totals (inventory value, revenue) */
  canViewFinancials: boolean;
  /** True if the current role can see profitability labels */
  canViewProfitability: boolean;
  /** Masks a numeric or string value for restricted users */
  maskPrice: (value: number | string, prefix?: string) => string;
  /** Strips sensitive fields from a data record at the data layer */
  sanitize: <T extends Record<string, any>>(record: T, sensitiveFields: (keyof T)[]) => T;
  /** Strips sensitive fields from an array of records */
  sanitizeArray: <T extends Record<string, any>>(records: T[], sensitiveFields: (keyof T)[]) => T[];
  /** Returns the access level name for display */
  accessLevel: AccessLevel;
  /** Current role name */
  roleName: string;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

/** Fields that are always considered sensitive / financial */
export const SENSITIVE_FIELDS = [
  'unitCost', 'costPrice', 'price', 'totalCost', 'value', 'profit',
  'profitability', 'margin', 'revenue', 'supplierPrice', 'landedCost',
  'purchasePrice', 'salePrice', 'discrepancyValue',
] as const;

/** Roles that get FULL financial visibility */
const FINANCE_ROLES = ['admin', 'finance', 'executive', 'director', 'cfo', 'accountant', 'auditor'];

export function roleCanViewPricing(role: Role | undefined): boolean {
  if (!role) return false;
  const id = role.id.toLowerCase();
  return FINANCE_ROLES.some(r => id === r || id.includes(r));
}

// ─── Context ───────────────────────────────────────────────────────────────────

const RoleAccessContext = createContext<RoleAccessContextValue | null>(null);

export function RoleAccessProvider({
  children,
  currentRole,
}: {
  children: ReactNode;
  currentRole: Role | undefined;
}) {
  const value = useMemo<RoleAccessContextValue>(() => {
    const canViewPricing = roleCanViewPricing(currentRole);
    const canViewFinancials = canViewPricing;
    const canViewProfitability = canViewPricing;

    const maskPrice = (val: number | string, prefix = 'GHS '): string => {
      if (canViewPricing) {
        if (typeof val === 'number') return `${prefix}${val.toLocaleString()}`;
        return val as string;
      }
      return '● ● ● ● ●';
    };

    const sanitize = <T extends Record<string, any>>(record: T, sensitiveFields: (keyof T)[]): T => {
      if (canViewPricing) return record;
      const cleaned = { ...record };
      sensitiveFields.forEach(f => {
        if (f in cleaned) {
          (cleaned as any)[f] = undefined;
        }
      });
      return cleaned;
    };

    const sanitizeArray = <T extends Record<string, any>>(records: T[], sensitiveFields: (keyof T)[]): T[] => {
      if (canViewPricing) return records;
      return records.map(r => sanitize(r, sensitiveFields));
    };

    return {
      canAccessPage: (page, action = 'view') => {
        const permissions = currentRole?.permissions[page];
        return Boolean(permissions?.visible && permissions.view && permissions[action]);
      },
      canViewPricing,
      canViewFinancials,
      canViewProfitability,
      maskPrice,
      sanitize,
      sanitizeArray,
      accessLevel: canViewPricing ? 'full' : 'restricted',
      roleName: currentRole?.name ?? 'Unknown',
    };
  }, [currentRole]);

  return (
    <RoleAccessContext.Provider value={value}>
      {children}
    </RoleAccessContext.Provider>
  );
}

/** Hook — throws if used outside the provider */
export function useAccess(): RoleAccessContextValue {
  const ctx = useContext(RoleAccessContext);
  if (!ctx) throw new Error('useAccess must be used inside <RoleAccessProvider>');
  return ctx;
}

/** Safe hook — returns a default "full access" context if no provider found */
export function useAccessSafe(): RoleAccessContextValue {
  const ctx = useContext(RoleAccessContext);
  if (ctx) return ctx;
  // Fallback: full access (won't happen in production with the provider wrapping App)
  return {
    canAccessPage: () => false,
    canViewPricing: true,
    canViewFinancials: true,
    canViewProfitability: true,
    maskPrice: (v, p = 'GHS ') => typeof v === 'number' ? `${p}${v.toLocaleString()}` : String(v),
    sanitize: r => r,
    sanitizeArray: r => r,
    accessLevel: 'full',
    roleName: 'Admin',
  };
}
