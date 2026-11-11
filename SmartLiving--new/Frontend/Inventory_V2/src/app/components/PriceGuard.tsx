import { type ReactNode } from 'react';
import { EyeOff, ShieldOff, Lock } from 'lucide-react';
import { useAccessSafe } from '../context/RoleAccessContext';

// ─── PriceMask ─────────────────────────────────────────────────────────────────
// Inline price/value masker — renders the real value for finance roles,
// a masked placeholder for everyone else.

interface PriceMaskProps {
  value: number | string | null | undefined;
  prefix?: string;
  className?: string;
  /** When true, renders a short dash instead of the dot mask */
  compact?: boolean;
  /** Override canViewPricing (e.g. from a parent that already computed it) */
  override?: boolean;
}

export function PriceMask({ value, prefix = 'GHS ', className = '', compact = false, override }: PriceMaskProps) {
  const { canViewPricing } = useAccessSafe();
  const show = override !== undefined ? override : canViewPricing;

  if (show) {
    const formatted = typeof value === 'number' ? `${prefix}${value.toLocaleString()}` : (value ?? '—');
    return <span className={className}>{formatted}</span>;
  }

  if (compact) return <span className="text-gray-300 select-none" title="Restricted">—</span>;

  return (
    <span className="inline-flex items-center gap-1 text-gray-300 select-none" title="Access restricted to Finance / Executive roles">
      <Lock className="w-3 h-3 text-gray-300" />
      <span className="tracking-widest text-gray-300">●●●●●</span>
    </span>
  );
}

// ─── PriceColumn ───────────────────────────────────────────────────────────────
// Table-cell wrapper — renders nothing (or a locked placeholder) when restricted.

interface PriceColumnProps {
  children: ReactNode;
  className?: string;
  cellTag?: 'td' | 'th';
  align?: 'left' | 'right' | 'center';
}

export function PriceCell({ children, className = '', cellTag: Tag = 'td', align = 'right' }: PriceColumnProps) {
  const { canViewPricing } = useAccessSafe();
  const alignClass = align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';

  if (!canViewPricing) {
    return (
      <Tag className={`${alignClass} ${className} px-4 py-3`}>
        <span className="inline-flex items-center gap-1 text-gray-300 text-xs select-none">
          <Lock className="w-2.5 h-2.5" />
          <span className="tracking-widest">●●●●</span>
        </span>
      </Tag>
    );
  }

  return <Tag className={`${alignClass} ${className} px-4 py-3`}>{children}</Tag>;
}

// ─── PriceHeader ───────────────────────────────────────────────────────────────
// Table header cell — hidden for restricted users.

interface PriceHeaderProps {
  children: ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
}

export function PriceHeader({ children, className = '', align = 'right' }: PriceHeaderProps) {
  const { canViewPricing } = useAccessSafe();
  const alignClass = align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';

  if (!canViewPricing) {
    return (
      <th className={`${alignClass} ${className} px-4 py-3`}>
        <span className="inline-flex items-center gap-1 text-gray-300 text-xs select-none">
          <Lock className="w-3 h-3" /> Hidden
        </span>
      </th>
    );
  }

  return <th className={`${alignClass} ${className} px-4 py-3`}>{children}</th>;
}

// ─── PriceGuard ─────────────────────────────────────────────────────────────────
// Renders children only if the user has pricing access,
// otherwise renders a fallback placeholder.

interface PriceGuardProps {
  children: ReactNode;
  fallback?: ReactNode;
  /** Whether to hide the element entirely (no fallback) */
  hideCompletely?: boolean;
}

export function PriceGuard({ children, fallback, hideCompletely = false }: PriceGuardProps) {
  const { canViewPricing } = useAccessSafe();

  if (canViewPricing) return <>{children}</>;
  if (hideCompletely) return null;
  if (fallback) return <>{fallback}</>;
  return (
    <span className="inline-flex items-center gap-1 text-gray-300 text-xs select-none" title="Finance / Executive only">
      <Lock className="w-3 h-3" /> Restricted
    </span>
  );
}

// ─── AccessBanner ──────────────────────────────────────────────────────────────
// A contextual banner informing restricted users that some data is hidden.

interface AccessBannerProps {
  className?: string;
}

export function AccessBanner({ className = '' }: AccessBannerProps) {
  return null;
}

// ─── FinanceOnly ───────────────────────────────────────────────────────────────
// Renders children only if the user has full financial access.
// Always renders nothing (not even a fallback) for restricted roles — used for
// entire sections like "cost price" form fields.

export function FinanceOnly({ children }: { children: ReactNode }) {
  const { canViewPricing } = useAccessSafe();
  if (!canViewPricing) return null;
  return <>{children}</>;
}
