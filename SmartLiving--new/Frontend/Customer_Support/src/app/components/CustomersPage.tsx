import { useEffect, useMemo, useState, type ElementType } from "react";
import {
  AlertCircle,
  Building2,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Phone,
  Search,
  ShieldCheck,
  UserRound,
  Wallet,
} from "lucide-react";
import { CustomerProfile } from "./CustomerProfile";
import { useTheme } from "./ThemeContext";

type Customer = {
  id: string;
  name: string;
  phone: string;
  email: string;
  branch: string;
  agent: string;
  agentInitials: string;
  products: string[];
  productCount: number;
  balance: number;
  balanceFormatted: string;
  status: string;
  segment: string;
  joinDate: string;
  lastInteraction: string;
  lastInteractionRaw: string;
  tickets: number;
  csat: number;
  accountType: string;
  city: string;
  ic: string;
  dob: string;
  gender: string;
  imageUrl?: string;
  leadStage?: string;
  totalPaid?: number;
  occupation?: string;
  followUpCount?: number;
};

type OverviewResponse = {
  ok: boolean;
  customers: Customer[];
  stats: {
    total_customers: number;
    active_count: number;
    inactive_count: number;
    overdue_count: number;
    followup_count: number;
  };
  filters: {
    branches: string[];
    agents: string[];
    statuses: string[];
  };
  pagination: {
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
    has_prev: boolean;
    has_next: boolean;
  };
};

const STATUS_CFG: Record<string, { text: string; bg: string; border: string }> = {
  Active: { text: "#166534", bg: "#ECFDF3", border: "#BBF7D0" },
  Inactive: { text: "#475467", bg: "#F8FAFC", border: "#E2E8F0" },
  Suspended: { text: "#B42318", bg: "#FEF3F2", border: "#FECDCA" },
  Overdue: { text: "#B54708", bg: "#FFFAEB", border: "#FEDF89" },
};

const CUSTOMER_FALLBACK_IMG =
  "https://imagedelivery.net/h9fmMoa1o2c2P55TcWJGOg/f1b8f81c-1aac-4580-6b1c-869ffafcb400/public";

function StatCard({
  label,
  value,
  icon: Icon,
  tint,
}: {
  label: string;
  value: string;
  icon: ElementType;
  tint: string;
}) {
  return (
    <div
      className="rounded-[24px] p-5"
      style={{
        background: "linear-gradient(180deg, rgba(255,255,255,0.98), rgba(246,248,252,0.92))",
        border: "1px solid rgba(214,219,229,0.9)",
        boxShadow: "0 18px 42px rgba(15, 23, 42, 0.06)",
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p style={{ fontSize: "0.75rem", color: "#667085", fontWeight: 600, letterSpacing: "0.03em" }}>{label}</p>
          <p style={{ fontSize: "1.85rem", fontWeight: 700, color: "#101828", marginTop: "10px", lineHeight: 1 }}>
            {value}
          </p>
        </div>
        <div
          className="flex h-11 w-11 items-center justify-center rounded-2xl"
          style={{ background: `${tint}18`, color: tint }}
        >
          <Icon size={20} strokeWidth={1.8} />
        </div>
      </div>
    </div>
  );
}

function SelectField({
  value,
  options,
  onChange,
  label,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <label className="flex min-w-[170px] flex-col gap-1.5">
      <span style={{ fontSize: "0.72rem", color: "#667085", fontWeight: 600 }}>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-2xl px-4 outline-none transition-all"
        style={{
          height: "46px",
          border: "1px solid #D0D5DD",
          background: "#FFFFFF",
          fontSize: "0.9rem",
          color: "#101828",
          boxShadow: "0 1px 2px rgba(16,24,40,0.03)",
        }}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function CustomerCard({ customer, onOpen }: { customer: Customer; onOpen: () => void }) {
  const statusCfg = STATUS_CFG[customer.status] ?? STATUS_CFG.Active;
  const imageSrc = customer.imageUrl?.trim() || CUSTOMER_FALLBACK_IMG;

  return (
    <button
      onClick={onOpen}
      className="group w-full overflow-hidden rounded-[26px] text-left transition-all"
      style={{
        background: "linear-gradient(180deg, rgba(255,255,255,0.99), rgba(246,248,251,0.96))",
        border: "1px solid rgba(221, 227, 236, 0.95)",
        boxShadow: "0 20px 44px rgba(15, 23, 42, 0.08)",
      }}
    >
      <div
        className="relative p-5"
        style={{
          background:
            "radial-gradient(circle at top right, rgba(107,114,255,0.12), transparent 34%), linear-gradient(180deg, rgba(255,255,255,0.96), rgba(249,250,251,0.92))",
        }}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <img
              src={imageSrc}
              alt={customer.name}
              className="h-14 w-14 rounded-[18px] object-cover"
              style={{ border: "1px solid rgba(255,255,255,0.85)", boxShadow: "0 10px 22px rgba(15,23,42,0.12)" }}
              onError={(event) => {
                const target = event.currentTarget;
                if (target.src !== CUSTOMER_FALLBACK_IMG) {
                  target.src = CUSTOMER_FALLBACK_IMG;
                }
              }}
            />
            <div className="min-w-0">
              <p className="truncate" style={{ fontSize: "0.98rem", fontWeight: 700, color: "#101828" }}>
                {customer.name}
              </p>
              <p className="truncate" style={{ fontSize: "0.78rem", color: "#667085", marginTop: "4px" }}>
                {customer.phone || "No phone number"}
              </p>
            </div>
          </div>
        </div>
        {!!customer.followUpCount && <span className="absolute right-5 top-16 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-800">Follow Up · {customer.followUpCount}</span>}

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span
            className="rounded-full px-3 py-1"
            style={{
              fontSize: "0.74rem",
              fontWeight: 700,
              color: statusCfg.text,
              background: statusCfg.bg,
              border: `1px solid ${statusCfg.border}`,
            }}
          >
            {customer.status}
          </span>
          <span
            className="rounded-full px-3 py-1"
            style={{
              fontSize: "0.74rem",
              fontWeight: 600,
              color: "#344054",
              background: "#F2F4F7",
            }}
          >
            {customer.accountType}
          </span>
          <span
            className="rounded-full px-3 py-1"
            style={{
              fontSize: "0.74rem",
              fontWeight: 600,
              color: "#344054",
              background: "#F9FAFB",
            }}
          >
            {customer.productCount} product{customer.productCount === 1 ? "" : "s"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-2xl p-3.5" style={{ background: "#FFFFFF", border: "1px solid #EEF2F6" }}>
            <div className="mb-1.5 flex items-center gap-2" style={{ color: "#667085" }}>
              <Building2 size={14} />
              <span style={{ fontSize: "0.73rem", fontWeight: 600 }}>Branch</span>
            </div>
            <p className="truncate" style={{ fontSize: "0.86rem", fontWeight: 700, color: "#101828" }}>
              {customer.branch || "Unassigned"}
            </p>
          </div>
          <div className="rounded-2xl p-3.5" style={{ background: "#FFFFFF", border: "1px solid #EEF2F6" }}>
            <div className="mb-1.5 flex items-center gap-2" style={{ color: "#667085" }}>
              <UserRound size={14} />
              <span style={{ fontSize: "0.73rem", fontWeight: 600 }}>Agent</span>
            </div>
            <p className="truncate" style={{ fontSize: "0.86rem", fontWeight: 700, color: "#101828" }}>
              {customer.agent || "Unassigned"}
            </p>
          </div>
        </div>

        <div className="mt-4 rounded-[22px] p-4" style={{ background: "#0F172A", color: "#FFFFFF" }}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.72)", fontWeight: 600 }}>Total Paid</p>
              <p style={{ fontSize: "1.15rem", fontWeight: 700, marginTop: "6px" }}>
                {`GHS ${Number(customer.totalPaid ?? 0).toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`}
              </p>
            </div>
            <div className="rounded-2xl p-2.5" style={{ background: "rgba(255,255,255,0.08)" }}>
              <Wallet size={18} />
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.62)", fontWeight: 600 }}>Recent activity</p>
              <p className="truncate" style={{ fontSize: "0.78rem", color: "#FFFFFF", marginTop: "4px" }}>
                {customer.lastInteraction}
              </p>
            </div>
            <span
              className="rounded-full px-3 py-1"
              style={{ fontSize: "0.72rem", fontWeight: 700, background: "rgba(255,255,255,0.12)", color: "#FFFFFF" }}
            >
              Open
            </span>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {customer.products.slice(0, 3).map((product) => (
            <span
              key={product}
              className="rounded-full px-3 py-1"
              style={{
                fontSize: "0.73rem",
                fontWeight: 600,
                color: "#475467",
                background: "#F8FAFC",
                border: "1px solid #E4E7EC",
              }}
            >
              {product}
            </span>
          ))}
          {customer.products.length > 3 && (
            <span
              className="rounded-full px-3 py-1"
              style={{
                fontSize: "0.73rem",
                fontWeight: 700,
                color: "#175CD3",
                background: "#EFF8FF",
              }}
            >
              +{customer.products.length - 3} more
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

export function CustomersPage() {
  const { t } = useTheme();
  const [selected, setSelected] = useState<Customer | null>(null);
  const [search, setSearch] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  useEffect(() => { const apply = (event?: Event) => { let payload: any = event ? (event as CustomEvent).detail : null; if (!payload) { try { payload = JSON.parse(window.sessionStorage.getItem("customer-support-global-search") || "null"); } catch { payload = null; } } if (payload?.page === "customers") { setSearch(payload.query || ""); setSearchTerm(payload.query || ""); setPage(1); window.sessionStorage.removeItem("customer-support-global-search"); } }; apply(); window.addEventListener("customer-support-global-search", apply); return () => window.removeEventListener("customer-support-global-search", apply); }, []);
  const [branch, setBranch] = useState("All Branches");
  const [agent, setAgent] = useState("All Agents");
  const [status, setStatus] = useState("All Statuses");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [payload, setPayload] = useState<OverviewResponse | null>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setPage(1);
      setSearchTerm(search.trim());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({
          page: String(page),
          per_page: "20",
        });
        if (searchTerm) params.set("q", searchTerm);
        if (branch !== "All Branches") params.set("branch", branch);
        if (agent !== "All Agents") params.set("agent", agent);
        if (status !== "All Statuses") params.set("status", status);
        const response = await fetch(`/api/customer-support/customers?${params.toString()}`, {
          credentials: "same-origin",
          signal: controller.signal,
        });
        const data = (await response.json()) as OverviewResponse;
        if (!response.ok || !data.ok) {
          throw new Error("Unable to load customers.");
        }
        if (!cancelled) {
          setPayload(data);
          window.dispatchEvent(new CustomEvent("customer-support-followup-count", { detail: data.stats?.followup_count || 0 }));
        }
      } catch (fetchError) {
        if (!cancelled && !(fetchError instanceof DOMException && fetchError.name === "AbortError")) {
          setError(fetchError instanceof Error ? fetchError.message : "Unable to load customers.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [agent, branch, page, searchTerm, status]);

  const branchOptions = useMemo(
    () => ["All Branches", ...(payload?.filters.branches ?? [])],
    [payload],
  );
  const agentOptions = useMemo(
    () => ["All Agents", ...(payload?.filters.agents ?? [])],
    [payload],
  );
  const statusOptions = useMemo(
    () => ["All Statuses", ...(payload?.filters.statuses ?? [])],
    [payload],
  );
  if (selected) {
    return <CustomerProfile customer={selected} onBack={() => setSelected(null)} />;
  }

  const customers = payload?.customers ?? [];
  const stats = payload?.stats;
  const pagination = payload?.pagination;

  return (
    <div className="h-full overflow-y-auto" style={{ background: t.pageBg }}>
      <div className="mx-auto w-full max-w-[1480px] px-4 py-6 sm:px-6 lg:px-8">
        <section className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 style={{ fontSize: "1.4rem", fontWeight: 700, color: "#101828" }}>Customers</h1>
            <p style={{ fontSize: "0.84rem", color: "#667085", marginTop: "4px" }}>
              {(stats?.total_customers ?? 0).toLocaleString()} total customers
            </p>
          </div>
          <div className="rounded-[20px] px-4 py-3" style={{ background: "#FFFFFF", border: "1px solid #E4E7EC" }}>
            <p style={{ fontSize: "0.74rem", color: "#667085", fontWeight: 700 }}>Page</p>
            <p style={{ fontSize: "1rem", fontWeight: 700, color: "#111827", marginTop: "2px" }}>
              {pagination ? `${pagination.page}/${Math.max(pagination.total_pages, 1)}` : "1/1"}
            </p>
          </div>
        </section>

        <section className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <StatCard label="Total Customers" value={String(stats?.total_customers ?? 0)} icon={UserRound} tint="#175CD3" />
          <StatCard label="Active Accounts" value={String(stats?.active_count ?? 0)} icon={ShieldCheck} tint="#16A34A" />
          <StatCard label="Overdue Accounts" value={String(stats?.overdue_count ?? 0)} icon={AlertCircle} tint="#F59E0B" />
        </section>

        <section
          className="mt-6 rounded-[30px] p-5 sm:p-6"
          style={{
            background: "rgba(255,255,255,0.92)",
            border: "1px solid rgba(223, 227, 235, 0.94)",
            boxShadow: "0 16px 40px rgba(15, 23, 42, 0.06)",
          }}
        >
          <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
            <div className="flex-1">
              <label className="flex flex-col gap-1.5">
                <span style={{ fontSize: "0.74rem", color: "#667085", fontWeight: 700 }}>Search customers</span>
                <div className="relative">
                  <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2" style={{ color: "#98A2B3" }} />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search by customer name, phone number, or email"
                    className="w-full rounded-[22px] pl-12 pr-4 outline-none transition-all"
                    style={{
                      height: "52px",
                      border: "1px solid #D0D5DD",
                      background: "#FFFFFF",
                      fontSize: "0.92rem",
                      color: "#101828",
                    }}
                  />
                </div>
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <SelectField value={branch} options={branchOptions} onChange={(value) => { setPage(1); setBranch(value); }} label="Branch" />
              <SelectField value={agent} options={agentOptions} onChange={(value) => { setPage(1); setAgent(value); }} label="Agent" />
              <SelectField value={status} options={statusOptions} onChange={(value) => { setPage(1); setStatus(value); }} label="Status" />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p style={{ fontSize: "0.82rem", color: "#667085" }}>
              {loading ? "Loading customers..." : `${customers.length} customer card${customers.length === 1 ? "" : "s"} on this page`}
            </p>
            <button
              onClick={() => {
                setSearch("");
                setSearchTerm("");
                setBranch("All Branches");
                setAgent("All Agents");
                setStatus("All Statuses");
                setPage(1);
              }}
              className="rounded-full px-4 py-2 transition-all"
              style={{
                background: "#F8FAFC",
                border: "1px solid #E4E7EC",
                color: "#344054",
                fontSize: "0.8rem",
                fontWeight: 700,
              }}
            >
              Reset filters
            </button>
          </div>
        </section>

        <section className="mt-6">
          {loading && (
            <div
              className="flex min-h-[340px] items-center justify-center rounded-[30px]"
              style={{
                background: "rgba(255,255,255,0.92)",
                border: "1px solid rgba(223, 227, 235, 0.94)",
              }}
            >
              <div className="flex flex-col items-center gap-3">
                <div
                  className="flex h-16 w-16 items-center justify-center rounded-full"
                  style={{ background: "rgba(29,78,216,0.08)", color: "#1D4ED8" }}
                >
                  <Loader2 size={28} className="animate-spin" />
                </div>
                <p style={{ fontSize: "0.92rem", color: "#344054", fontWeight: 600 }}>Loading customer cards</p>
              </div>
            </div>
          )}

          {!loading && error && (
            <div
              className="rounded-[28px] p-6"
              style={{ background: "#FEF3F2", border: "1px solid #FECDCA", color: "#B42318" }}
            >
              <p style={{ fontSize: "0.92rem", fontWeight: 700 }}>{error}</p>
            </div>
          )}

          {!loading && !error && customers.length === 0 && (
            <div
              className="rounded-[30px] p-10 text-center"
              style={{
                background: "rgba(255,255,255,0.92)",
                border: "1px solid rgba(223, 227, 235, 0.94)",
              }}
            >
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full" style={{ background: "#F2F4F7", color: "#667085" }}>
                <Phone size={28} />
              </div>
              <p style={{ fontSize: "1rem", fontWeight: 700, color: "#101828", marginTop: "16px" }}>No customers matched the current filters</p>
              <p style={{ fontSize: "0.84rem", color: "#667085", marginTop: "8px" }}>Adjust the search or filters and reload the page results.</p>
            </div>
          )}

          {!loading && !error && customers.length > 0 && (
            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {customers.map((customer) => (
                <CustomerCard key={customer.id} customer={customer} onOpen={() => setSelected(customer)} />
              ))}
            </div>
          )}
        </section>

        {!loading && !error && (pagination?.total_pages ?? 1) > 1 && (
          <section
            className="mt-6 flex flex-col gap-3 rounded-[26px] p-4 sm:flex-row sm:items-center sm:justify-between"
            style={{
              background: "rgba(255,255,255,0.92)",
              border: "1px solid rgba(223, 227, 235, 0.94)",
            }}
          >
            <p style={{ fontSize: "0.84rem", color: "#667085" }}>
              Page {pagination?.page ?? 1} of {pagination?.total_pages ?? 1}
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((current) => Math.max(current - 1, 1))}
                disabled={!pagination?.has_prev}
                className="flex items-center gap-2 rounded-full px-4 py-2 transition-all disabled:cursor-not-allowed disabled:opacity-50"
                style={{ background: "#FFFFFF", border: "1px solid #D0D5DD", color: "#344054", fontWeight: 700, fontSize: "0.82rem" }}
              >
                <ChevronLeft size={16} />
                Prev
              </button>
              <button
                onClick={() => setPage((current) => current + 1)}
                disabled={!pagination?.has_next}
                className="flex items-center gap-2 rounded-full px-4 py-2 transition-all disabled:cursor-not-allowed disabled:opacity-50"
                style={{ background: "#0F172A", border: "1px solid #0F172A", color: "#FFFFFF", fontWeight: 700, fontSize: "0.82rem" }}
              >
                Next
                <ChevronRight size={16} />
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
