import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  Boxes,
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Eye,
  Filter,
  Grid3x3,
  Layers3,
  List,
  LoaderCircle,
  MoreVertical,
  Package,
  Plus,
  Search,
  ShoppingBag,
  Users,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { PriceMask } from './PriceGuard';
import { ImageWithFallback } from './figma/ImageWithFallback';
import { ProductCardDetail, type ProductCardRecord } from './ProductCardDetail';

type ProductCard = ProductCardRecord;

type ManagerOption = {
  id: string;
  name: string;
  branch: string;
};

type InventoryItemOption = {
  key: string;
  name: string;
  price: number;
  description: string;
  imageUrl: string;
};

type BootstrapPayload = {
  managers: ManagerOption[];
  inventoryItems: InventoryItemOption[];
  productTypes: string[];
  categories: string[];
  packageNames: string[];
  installmentTerms: number[];
};

type ProductCardsResponse = {
  ok: boolean;
  card?: ProductCard;
  cards?: ProductCard[];
  error?: string;
};

type BootstrapResponse = Partial<BootstrapPayload> & {
  ok: boolean;
  error?: string;
};

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  managers: [],
  inventoryItems: [],
  productTypes: [],
  categories: [],
  packageNames: [],
  installmentTerms: [3, 6, 9, 12, 18, 24],
};

const PRODUCT_CARDS_PER_PAGE = 10;

const PRODUCT_CARD_CACHE_KEY = 'inventory.productCards.v1';
const PRODUCT_CARD_CACHE_MAX_AGE_MS = 5 * 60 * 1000;

type ProductCardsCache = {
  cachedAt: number;
  cards: ProductCard[];
  bootstrap: BootstrapPayload;
};

function normalizeBootstrap(payload: Partial<BootstrapPayload>): BootstrapPayload {
  return {
    managers: Array.isArray(payload.managers) ? payload.managers : [],
    inventoryItems: Array.isArray(payload.inventoryItems) ? payload.inventoryItems : [],
    productTypes: Array.isArray(payload.productTypes) ? payload.productTypes : [],
    categories: Array.isArray(payload.categories) ? payload.categories : [],
    packageNames: Array.isArray(payload.packageNames) ? payload.packageNames : [],
    installmentTerms: Array.isArray(payload.installmentTerms) && payload.installmentTerms.length > 0
      ? payload.installmentTerms
      : EMPTY_BOOTSTRAP.installmentTerms,
  };
}

function readProductCardsCache(): ProductCardsCache | null {
  try {
    const raw = window.localStorage.getItem(PRODUCT_CARD_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ProductCardsCache;
    if (!Array.isArray(parsed.cards) || !parsed.bootstrap || !parsed.cachedAt) return null;
    if (!parsed.cards.every((card) => Array.isArray(card?.customerRows))) return null;
    if (Date.now() - parsed.cachedAt > PRODUCT_CARD_CACHE_MAX_AGE_MS) return null;
    return {
      cachedAt: parsed.cachedAt,
      cards: parsed.cards,
      bootstrap: normalizeBootstrap(parsed.bootstrap),
    };
  } catch {
    return null;
  }
}

function writeProductCardsCache(cards: ProductCard[], bootstrap: BootstrapPayload): void {
  try {
    window.localStorage.setItem(
      PRODUCT_CARD_CACHE_KEY,
      JSON.stringify({
        cachedAt: Date.now(),
        cards,
        bootstrap,
      })
    );
  } catch {
    // Local storage may be unavailable in private browsing or locked-down devices.
  }
}

function stockReadyClass(stockReady: number): string {
  if (stockReady >= 85) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (stockReady >= 70) return 'text-amber-700 bg-amber-50 border-amber-200';
  return 'text-rose-700 bg-rose-50 border-rose-200';
}

function profitabilityClass(label: string): string {
  if (label === 'high') return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (label === 'medium') return 'text-blue-700 bg-blue-50 border-blue-200';
  return 'text-slate-700 bg-slate-100 border-slate-200';
}

function selectedCount(map: Record<string, boolean>): number {
  return Object.values(map).filter(Boolean).length;
}

function paginationRange(currentPage: number, totalPages: number): Array<number | 'ellipsis'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set([1, totalPages, currentPage, currentPage - 1, currentPage + 1]);
  const sortedPages = Array.from(pages)
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((a, b) => a - b);

  const range: Array<number | 'ellipsis'> = [];
  sortedPages.forEach((page, index) => {
    if (index > 0 && page - sortedPages[index - 1] > 1) {
      range.push('ellipsis');
    }
    range.push(page);
  });
  return range;
}

export function ProductCards() {
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [selectedCard, setSelectedCard] = useState<ProductCard | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'low-stock'>('all');
  const [profitFilter, setProfitFilter] = useState<'all' | 'high' | 'medium' | 'low'>('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingCard, setEditingCard] = useState<ProductCard | null>(null);
  const [cards, setCards] = useState<ProductCard[]>([]);
  const [bootstrap, setBootstrap] = useState<BootstrapPayload>(EMPTY_BOOTSTRAP);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const filterRef = useRef<HTMLDivElement>(null);
  const deferredSearchQuery = useDeferredValue(searchQuery);

  const loadProductCards = async (showRefreshing = false) => {
    if (showRefreshing) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const cached = readProductCardsCache();
      if (!showRefreshing && cached) {
        setCards(cached.cards);
        setBootstrap(cached.bootstrap);
      }

      const [cardsResponse, bootstrapResponse] = await Promise.all([
        fetch('/api/inventory/product-cards', { credentials: 'same-origin' }),
        fetch('/api/inventory/product-cards/bootstrap', { credentials: 'same-origin' }),
      ]);
      const cardsPayload = (await cardsResponse.json()) as ProductCardsResponse;
      const bootstrapPayload = (await bootstrapResponse.json()) as BootstrapResponse;

      if (!cardsResponse.ok || !cardsPayload.ok) {
        throw new Error(cardsPayload.error || 'Failed to load product cards.');
      }
      if (!bootstrapResponse.ok || !bootstrapPayload.ok) {
        throw new Error(bootstrapPayload.error || 'Failed to load product-card setup data.');
      }

      const nextCards = Array.isArray(cardsPayload.cards) ? cardsPayload.cards : [];
      const nextBootstrap = normalizeBootstrap(bootstrapPayload);
      setCards(nextCards);
      setBootstrap(nextBootstrap);
      writeProductCardsCache(nextCards, nextBootstrap);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load product cards.';
      toast.error('Product cards could not load', { description: message });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadProductCards();
  }, []);

  useEffect(() => {
    if (!filterOpen) return undefined;
    const onClick = (event: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(event.target as Node)) {
        setFilterOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [filterOpen]);

  const filteredCards = useMemo(() => {
    return cards.filter((card) => {
      const haystack = [
        card.name,
        card.category,
        card.productType,
        card.packageName,
        ...(card.branches || []),
      ]
        .join(' ')
        .toLowerCase();
      const matchesSearch = deferredSearchQuery.trim()
        ? haystack.includes(deferredSearchQuery.trim().toLowerCase())
        : true;
      const matchesStatus = statusFilter === 'all' ? true : card.status === statusFilter;
      const matchesProfit = profitFilter === 'all' ? true : card.profitability === profitFilter;
      return matchesSearch && matchesStatus && matchesProfit;
    });
  }, [cards, deferredSearchQuery, profitFilter, statusFilter]);

  const activeFilterCount = (statusFilter !== 'all' ? 1 : 0) + (profitFilter !== 'all' ? 1 : 0);
  const totalPages = Math.max(1, Math.ceil(filteredCards.length / PRODUCT_CARDS_PER_PAGE));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const pageStartIndex = (safeCurrentPage - 1) * PRODUCT_CARDS_PER_PAGE;
  const paginatedCards = filteredCards.slice(pageStartIndex, pageStartIndex + PRODUCT_CARDS_PER_PAGE);
  const paginationItems = useMemo(() => paginationRange(currentPage, totalPages), [currentPage, totalPages]);
  const metrics = useMemo(
    () => ({
      cards: cards.length,
      customers: cards.reduce((sum, card) => sum + (card.customers || 0), 0),
      branches: new Set(cards.flatMap((card) => card.branches || []).filter(Boolean)).size,
    }),
    [cards]
  );

  useEffect(() => {
    setCurrentPage(1);
  }, [profitFilter, searchQuery, statusFilter, viewMode]);

  useEffect(() => {
    if (!selectedCard) return;
    const nextSelectedCard = cards.find((card) => card.id === selectedCard.id) || null;
    setSelectedCard(nextSelectedCard);
  }, [cards, selectedCard]);

  if (selectedCard) {
    return (
      <>
      <ProductCardDetail
        card={selectedCard}
        inventoryItems={bootstrap.inventoryItems}
        onBack={() => {
          setSelectedCard(null);
        }}
        onEdit={() => setEditingCard(selectedCard)}
        onCardUpdated={(updatedCard) => {
          setCards((current) =>
            current.map((card) =>
              card.id === updatedCard.id
                ? {
                    ...card,
                    name: updatedCard.name,
                    description: updatedCard.description,
                    image: updatedCard.image,
                    price: updatedCard.price,
                    cashPrice: updatedCard.cashPrice,
                    paymentRate: updatedCard.paymentRate,
                    costPrice: updatedCard.costPrice,
                    profitMarginPrice: updatedCard.profitMarginPrice,
                    itemsCount: updatedCard.itemsCount,
                    componentTypes: updatedCard.componentTypes,
                    customers: updatedCard.customers,
                    completion70: updatedCard.completion70,
                    completion80: updatedCard.completion80,
                    completion90: updatedCard.completion90,
                    stockReady: updatedCard.stockReady,
                    status: updatedCard.status,
                    profitability: updatedCard.profitability,
                    productType: updatedCard.productType,
                    category: updatedCard.category,
                    packageName: updatedCard.packageName,
                    defaultTermMonths: updatedCard.defaultTermMonths,
                    managerCount: updatedCard.managerCount,
                    branchCount: updatedCard.branchCount,
                    branches: updatedCard.branches,
                    totalSalesValue: updatedCard.totalSalesValue,
                    purchaseCount: updatedCard.purchaseCount,
                    lastPurchaseDate: updatedCard.lastPurchaseDate,
                    createdAt: updatedCard.createdAt,
                    updatedAt: updatedCard.updatedAt,
                    cfImageId: updatedCard.cfImageId,
                    changeHistory: updatedCard.changeHistory,
                  }
                : card
            )
          );
          setSelectedCard(updatedCard);
        }}
      />
      {editingCard && (
        <CreateProductCardModal
          bootstrap={bootstrap}
          card={editingCard}
          onClose={() => setEditingCard(null)}
          onCreated={async () => {
            setEditingCard(null);
            await loadProductCards(true);
          }}
        />
      )}
      </>
    );
  }

  return (
    <div className="space-y-6">
      {createOpen && (
        <CreateProductCardModal
          bootstrap={bootstrap}
          onClose={() => setCreateOpen(false)}
          onCreated={async () => {
            setCreateOpen(false);
            await loadProductCards(true);
          }}
        />
      )}

      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Product Cards</h1>
          <p className="mt-1 text-sm text-slate-600">
            Aggregate bundle performance across manager-specific product documents and customer purchases.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:w-[400px]">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              Cards <Layers3 className="h-4 w-4 text-indigo-600" />
            </div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{metrics.cards}</div>
            <div className="mt-1 text-xs text-slate-500">{filteredCards.length} in current result</div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              Customers <Users className="h-4 w-4 text-sky-600" />
            </div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">
              {metrics.customers}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              Branches <Building2 className="h-4 w-4 text-violet-600" />
            </div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">
              {metrics.branches}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="relative flex-1 lg:max-w-lg">
            <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              placeholder="Search by product, category, package, branch..."
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-4 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:bg-white focus:ring-2 focus:ring-indigo-100"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative" ref={filterRef}>
              <button
                onClick={() => setFilterOpen((open) => !open)}
                className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-3 text-sm font-medium transition ${
                  activeFilterCount > 0
                    ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                <Filter className="h-4 w-4" />
                Filter
                {activeFilterCount > 0 && (
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-semibold text-white">
                    {activeFilterCount}
                  </span>
                )}
              </button>

              {filterOpen && (
                <div className="absolute right-0 z-30 mt-2 w-72 rounded-2xl border border-slate-200 bg-white p-4 shadow-xl">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-semibold text-slate-900">Filters</div>
                    <button
                      onClick={() => {
                        setStatusFilter('all');
                        setProfitFilter('all');
                      }}
                      className="text-xs font-medium text-indigo-600 hover:underline"
                    >
                      Clear all
                    </button>
                  </div>

                  <div className="mt-4 space-y-4">
                    <div>
                      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Status</div>
                      <div className="flex flex-wrap gap-2">
                        {(['all', 'active', 'low-stock'] as const).map((value) => (
                          <button
                            key={value}
                            onClick={() => setStatusFilter(value)}
                            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                              statusFilter === value
                                ? 'border-indigo-600 bg-indigo-600 text-white'
                                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                            }`}
                          >
                            {value === 'all' ? 'All' : value === 'active' ? 'Healthy stock' : 'Low stock'}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div>
                      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Profitability</div>
                      <div className="flex flex-wrap gap-2">
                        {(['all', 'high', 'medium', 'low'] as const).map((value) => (
                          <button
                            key={value}
                            onClick={() => setProfitFilter(value)}
                            className={`rounded-full border px-3 py-1.5 text-xs font-medium capitalize transition ${
                              profitFilter === value
                                ? 'border-indigo-600 bg-indigo-600 text-white'
                                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                            }`}
                          >
                            {value}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="inline-flex items-center gap-1 rounded-2xl bg-slate-100 p-1">
              <button
                onClick={() => setViewMode('grid')}
                className={`rounded-xl p-2 transition ${
                  viewMode === 'grid' ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <Grid3x3 className="h-4 w-4" />
              </button>
              <button
                onClick={() => setViewMode('table')}
                className={`rounded-xl p-2 transition ${
                  viewMode === 'table' ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <List className="h-4 w-4" />
              </button>
            </div>

            <button
              onClick={() => setCreateOpen(true)}
              className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-indigo-700"
            >
              <Plus className="h-4 w-4" />
              Create Product Card
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex min-h-[280px] items-center justify-center rounded-3xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center gap-3 text-sm text-slate-600">
            <LoaderCircle className="h-5 w-5 animate-spin text-indigo-600" />
            Loading product cards...
          </div>
        </div>
      ) : cards.length === 0 ? (
        <div className="rounded-3xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center shadow-sm">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100">
            <Package className="h-7 w-7 text-slate-400" />
          </div>
          <h3 className="mt-4 text-lg font-semibold text-slate-900">No product cards found</h3>
          <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500">
            Try a different search or create a new card. This page now reads directly from the shared `products` and `customers`
            collections.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {viewMode === 'grid' ? (
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {paginatedCards.map((card) => (
                <div key={card.id} className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg">
                  <div className="relative h-52 overflow-hidden border-b border-slate-200 bg-[radial-gradient(circle_at_top_left,_rgba(99,102,241,0.18),_transparent_45%),radial-gradient(circle_at_bottom_right,_rgba(14,165,233,0.16),_transparent_40%),linear-gradient(180deg,_#f8fafc,_#eef2ff)]">
                    {card.image ? (
                      <ImageWithFallback src={card.image} alt={card.name} className="h-full w-full object-contain p-4" />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center">
                        <Package className="h-12 w-12 text-slate-300" />
                      </div>
                    )}
                    <div className="absolute left-4 top-4 flex items-center gap-2">
                      <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${stockReadyClass(card.stockReady)}`}>
                        Stock {card.stockReady}%
                      </span>
                      <span className={`rounded-full border px-3 py-1 text-xs font-semibold capitalize ${profitabilityClass(card.profitability)}`}>
                        {card.profitability}
                      </span>
                    </div>
                    <button className="absolute right-4 top-4 inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/70 bg-white/85 text-slate-600 shadow-sm backdrop-blur transition hover:bg-white">
                      <MoreVertical className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="space-y-4 p-5">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        {card.productType && (
                          <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-700">
                            {card.productType}
                          </span>
                        )}
                        {card.category && (
                          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-700">
                            {card.category}
                          </span>
                        )}
                      </div>
                      <h3 className="mt-3 text-lg font-semibold text-slate-900">{card.name}</h3>
                      <p className="mt-1 line-clamp-2 text-sm text-slate-500">{card.description || 'No description provided.'}</p>
                    </div>

                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                      <div className="rounded-2xl bg-slate-50 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Installment</div>
                        <div className="mt-1 text-base font-semibold text-slate-900">
                          <PriceMask value={card.price} />
                        </div>
                      </div>
                      <div className="rounded-2xl bg-slate-50 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Cash</div>
                        <div className="mt-1 text-base font-semibold text-slate-900">
                          <PriceMask value={card.cashPrice} />
                        </div>
                      </div>
                      <div className="rounded-2xl bg-amber-50 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-700">Daily rate</div>
                        <div className="mt-1 text-base font-semibold text-slate-900">
                          {card.paymentRate > 0 ? <PriceMask value={card.paymentRate} /> : '-'}
                        </div>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                      <div className="rounded-2xl border border-slate-200 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Customers</div>
                        <div className="mt-2 text-xl font-semibold text-slate-900">{card.customers}</div>
                      </div>
                      <div className="rounded-2xl border border-slate-200 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Managers</div>
                        <div className="mt-2 text-xl font-semibold text-slate-900">{card.managerCount}</div>
                      </div>
                      <div className="rounded-2xl border border-slate-200 p-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Units</div>
                        <div className="mt-2 text-xl font-semibold text-slate-900">{card.itemsCount}</div>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-2xl bg-sky-50 px-3 py-2 text-center">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-700">70%</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{card.completion70}</div>
                      </div>
                      <div className="rounded-2xl bg-violet-50 px-3 py-2 text-center">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-700">80%</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{card.completion80}</div>
                      </div>
                      <div className="rounded-2xl bg-emerald-50 px-3 py-2 text-center">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-700">90%</div>
                        <div className="mt-1 text-sm font-semibold text-slate-900">{card.completion90}</div>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
                        <Building2 className="h-3.5 w-3.5" />
                        {card.branchCount} branch{card.branchCount === 1 ? '' : 'es'}
                      </span>
                      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
                        <Boxes className="h-3.5 w-3.5" />
                        {card.componentTypes} component type{card.componentTypes === 1 ? '' : 's'}
                      </span>
                    </div>

                    <button
                      onClick={() => setSelectedCard(card)}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-indigo-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-indigo-700"
                    >
                      <Eye className="h-4 w-4" />
                      View Details
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-6 py-4 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Product card</th>
                  <th className="px-6 py-4 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Prices</th>
                  <th className="px-6 py-4 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Coverage</th>
                  <th className="px-6 py-4 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Customers</th>
                  <th className="px-6 py-4 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Completion</th>
                  <th className="px-6 py-4 text-right text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {paginatedCards.map((card) => (
                  <tr key={card.id} className="transition hover:bg-slate-50">
                    <td className="px-6 py-5">
                      <div className="flex items-center gap-4">
                        <div className="h-14 w-14 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                          {card.image ? (
                            <ImageWithFallback src={card.image} alt={card.name} className="h-full w-full object-contain p-2" />
                          ) : (
                            <div className="flex h-full w-full items-center justify-center">
                              <Package className="h-5 w-5 text-slate-300" />
                            </div>
                          )}
                        </div>
                        <div>
                          <div className="font-semibold text-slate-900">{card.name}</div>
                          <div className="mt-1 text-sm text-slate-500">
                            {[card.productType, card.category, card.packageName].filter(Boolean).join(' • ') || '-'}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-5">
                      <div className="space-y-1 text-sm">
                        <div className="font-semibold text-slate-900">
                          <PriceMask value={card.price} />
                        </div>
                        <div className="text-slate-500">
                          Cash: <PriceMask value={card.cashPrice} />
                        </div>
                        <div className="text-amber-700">
                          Daily: {card.paymentRate > 0 ? <PriceMask value={card.paymentRate} /> : '-'}
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-5">
                      <div className="space-y-2">
                        <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${stockReadyClass(card.stockReady)}`}>
                          Stock {card.stockReady}%
                        </span>
                        <div className="text-sm text-slate-500">
                          {card.managerCount} managers / {card.branchCount} branches
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-5">
                      <div className="space-y-1 text-sm">
                        <div className="font-semibold text-slate-900">{card.customers}</div>
                        <div className="text-slate-500">{card.purchaseCount} purchase record(s)</div>
                      </div>
                    </td>
                    <td className="px-6 py-5">
                      <div className="flex flex-wrap gap-2">
                        <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">70%: {card.completion70}</span>
                        <span className="rounded-full bg-violet-50 px-3 py-1 text-xs font-semibold text-violet-700">80%: {card.completion80}</span>
                        <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">90%: {card.completion90}</span>
                      </div>
                    </td>
                    <td className="px-6 py-5 text-right">
                      <button
                        onClick={() => setSelectedCard(card)}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                      >
                        <Eye className="h-4 w-4" />
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-4 rounded-3xl border border-slate-200 bg-white px-4 py-4 text-sm text-slate-500 shadow-sm lg:flex-row lg:items-center lg:justify-between">
            <div>
              Showing <span className="font-semibold text-slate-900">{pageStartIndex + 1}</span>-
              <span className="font-semibold text-slate-900">{Math.min(pageStartIndex + PRODUCT_CARDS_PER_PAGE, filteredCards.length)}</span> of{' '}
              <span className="font-semibold text-slate-900">{filteredCards.length}</span> matching product card
              {filteredCards.length === 1 ? '' : 's'}
              {filteredCards.length !== metrics.cards && <span> from {metrics.cards} total</span>}
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
              {refreshing && (
                <div className="inline-flex items-center gap-2 text-slate-500">
                  <LoaderCircle className="h-4 w-4 animate-spin text-indigo-600" />
                  Refreshing...
                </div>
              )}
              <div className="inline-flex items-center gap-1 rounded-2xl bg-slate-100 p-1">
                <button
                  onClick={() => setCurrentPage(1)}
                  disabled={currentPage === 1}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition hover:bg-white hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="First page"
                >
                  <ChevronsLeft className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                  disabled={currentPage === 1}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition hover:bg-white hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="Previous page"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                {paginationItems.map((item, index) =>
                  item === 'ellipsis' ? (
                    <span key={`ellipsis-${index}`} className="inline-flex h-9 min-w-9 items-center justify-center px-2 text-slate-400">
                      ...
                    </span>
                  ) : (
                    <button
                      key={item}
                      onClick={() => setCurrentPage(item)}
                      className={`inline-flex h-9 min-w-9 items-center justify-center rounded-xl px-3 text-sm font-semibold transition ${
                        currentPage === item
                          ? 'bg-white text-indigo-700 shadow-sm'
                          : 'text-slate-600 hover:bg-white hover:text-slate-900'
                      }`}
                    >
                      {item}
                    </button>
                  )
                )}
                <button
                  onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
                  disabled={currentPage === totalPages}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition hover:bg-white hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="Next page"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setCurrentPage(totalPages)}
                  disabled={currentPage === totalPages}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition hover:bg-white hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="Last page"
                >
                  <ChevronsRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CreateProductCardModal({
  bootstrap,
  card,
  onClose,
  onCreated,
}: {
  bootstrap: BootstrapPayload;
  card?: ProductCard;
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const isEditing = !!card;
  const [productName, setProductName] = useState(card?.name || '');
  const [productType, setProductType] = useState(card?.productType || '');
  const [category, setCategory] = useState(card?.category || '');
  const [packageName, setPackageName] = useState(card?.packageName || '');
  const [defaultTermMonths, setDefaultTermMonths] = useState(String(card?.defaultTermMonths || bootstrap.installmentTerms[1] || 6));
  const [price, setPrice] = useState(card ? String(card.price || '') : '');
  const [cashPrice, setCashPrice] = useState(card ? String(card.cashPrice || '') : '');
  const [paymentRate, setPaymentRate] = useState(card ? String(card.paymentRate || '') : '');
  const [costPrice, setCostPrice] = useState(card ? String(card.costPrice || '') : '');
  const [description, setDescription] = useState(card?.description || '');
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState(card?.image || '');
  const [componentSearch, setComponentSearch] = useState('');
  const [selectedComponents, setSelectedComponents] = useState<Record<string, number>>(
    Object.fromEntries((card?.components || []).map((component) => [component.key, component.quantity]))
  );
  const [selectedManagers, setSelectedManagers] = useState<Record<string, boolean>>(
    Object.fromEntries((card?.managers || []).map((manager) => [manager.id, true]))
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const filteredInventory = useMemo(
    () =>
      bootstrap.inventoryItems.filter((item) =>
        [item.name, item.description].join(' ').toLowerCase().includes(componentSearch.toLowerCase())
      ),
    [bootstrap.inventoryItems, componentSearch]
  );

  const allManagersSelected =
    bootstrap.managers.length > 0 && bootstrap.managers.every((manager) => selectedManagers[manager.id]);

  const componentCount = useMemo(
    () => Object.values(selectedComponents).reduce((sum, quantity) => sum + quantity, 0),
    [selectedComponents]
  );

  const componentCostTotal = useMemo(
    () =>
      Object.entries(selectedComponents).reduce((sum, [key, quantity]) => {
        const item = bootstrap.inventoryItems.find((candidate) => candidate.key === key);
        return sum + ((item?.price || 0) * quantity);
      }, 0),
    [bootstrap.inventoryItems, selectedComponents]
  );

  const priceNumber = parseFloat(price) || 0;
  const cashPriceNumber = parseFloat(cashPrice) || 0;
  const paymentRateNumber = parseFloat(paymentRate) || 0;
  const costPriceNumber = parseFloat(costPrice) || 0;
  const installmentMargin = priceNumber > 0 && costPriceNumber > 0 ? ((priceNumber - costPriceNumber) / costPriceNumber) * 100 : 0;
  const cashMargin = cashPriceNumber > 0 && costPriceNumber > 0 ? ((cashPriceNumber - costPriceNumber) / costPriceNumber) * 100 : 0;

  const toggleAllManagers = () => {
    if (allManagersSelected) {
      setSelectedManagers({});
      return;
    }
    setSelectedManagers(Object.fromEntries(bootstrap.managers.map((manager) => [manager.id, true])));
  };

  const adjustQuantity = (key: string, delta: number) => {
    setSelectedComponents((current) => {
      const next = { ...current };
      const value = (next[key] || 0) + delta;
      if (value <= 0) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const handleImageSelection = (file: File | null) => {
    setImageFile(file);
    if (!file) {
      setImagePreview('');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setImagePreview(String(reader.result || ''));
    reader.readAsDataURL(file);
  };

  const submit = async () => {
    setError('');
    if (!productName.trim()) {
      setError('Product name is required.');
      return;
    }
    if (!priceNumber && !cashPriceNumber) {
      setError('Enter at least one selling price.');
      return;
    }
    if (costPriceNumber <= 0) {
      setError('Cost price is required.');
      return;
    }
    if (paymentRateNumber <= 0) {
      setError('Payment rate must be greater than zero.');
      return;
    }
    if (componentCount === 0) {
      setError('Select at least one inventory component.');
      return;
    }

    const managerIds = Object.entries(selectedManagers)
      .filter(([, checked]) => checked)
      .map(([managerId]) => managerId);
    if (managerIds.length === 0) {
      setError('Select at least one manager.');
      return;
    }

    setSubmitting(true);
    try {
      let imageUrl = card?.image || '';
      let imageId = '';

      if (imageFile) {
        const formData = new FormData();
        formData.append('image', imageFile);
        const uploadResponse = await fetch('/products/upload_image?variant=public', {
          method: 'POST',
          body: formData,
        });
        const uploadPayload = await uploadResponse.json();
        if (!uploadResponse.ok || !uploadPayload.success) {
          throw new Error(uploadPayload.error || 'Product image upload failed.');
        }
        imageUrl = uploadPayload.image_url || '';
        imageId = uploadPayload.image_id || '';
      }

      if (!imageUrl) {
        setError('Upload a product image before saving.');
        setSubmitting(false);
        return;
      }

      const response = await fetch(isEditing ? `/api/inventory/product-cards/${encodeURIComponent(card.id)}` : '/api/inventory/product-cards', {
        method: isEditing ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: productName.trim(),
          productType: productType.trim(),
          category: category.trim(),
          packageName: packageName.trim(),
          defaultTermMonths: parseInt(defaultTermMonths, 10) || 0,
          price: priceNumber,
          cashPrice: cashPriceNumber,
          paymentRate: paymentRateNumber,
          costPrice: costPriceNumber,
          description: description.trim(),
          imageUrl,
          imageId,
          managerIds,
          components: Object.entries(selectedComponents).map(([key, quantity]) => ({ key, quantity })),
        }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Product card ${isEditing ? 'update' : 'creation'} failed.`);
      }

      toast.success(isEditing ? 'Product details changed' : 'Product card created', {
        description: isEditing ? `Updated ${payload.updatedCount || managerIds.length} manager copy/copies and ${payload.updatedPackages || 0} undelivered card(s). Settle remaining components in Stock Deduction before delivery.` : `Created for ${payload.createdCount || managerIds.length} manager(s).`,
      });

      if (Array.isArray(payload.skipped) && payload.skipped.length > 0) {
        toast.warning('Some managers were skipped', {
          description: payload.skipped[0]?.reason || 'One or more manager copies were not created.',
        });
      }

      await onCreated();
    } catch (error) {
      const message = error instanceof Error ? error.message : `Product card ${isEditing ? 'update' : 'creation'} failed.`;
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/60 p-2 backdrop-blur-sm sm:p-4" onClick={onClose}>
      <div
        className="flex max-h-[calc(100dvh-1rem)] w-full min-w-0 max-w-6xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl sm:max-h-[92vh] sm:rounded-3xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-200 px-6 py-5">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">{isEditing ? 'Change Details' : 'Create Product Card'}</h3>
            <p className="mt-1 text-sm text-slate-500">
              {isEditing ? 'Update the product details across all existing manager copies.' : 'This saves one manager-specific document per selected manager into the shared `products` collection.'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition hover:bg-slate-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-4 sm:px-6 sm:py-6">
          <div className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="min-w-0 space-y-6">
              <section className="rounded-3xl border border-slate-200 p-5">
                <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Card identity</h4>
                <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="md:col-span-2">
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Product Name</label>
                    <input
                      value={productName}
                      onChange={(event) => setProductName(event.target.value)}
                      placeholder="e.g. Family and Friends Ghc 10"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>

                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Product Type</label>
                    <input
                      list="product-card-types"
                      value={productType}
                      onChange={(event) => setProductType(event.target.value)}
                      placeholder="Choose or type"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>

                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Category</label>
                    <input
                      list="product-card-categories"
                      value={category}
                      onChange={(event) => setCategory(event.target.value)}
                      placeholder="Choose or type"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>

                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Package Name</label>
                    <input
                      list="product-card-packages"
                      value={packageName}
                      onChange={(event) => setPackageName(event.target.value)}
                      placeholder="Choose or type"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>

                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Default Installment Term</label>
                    <select
                      value={defaultTermMonths}
                      onChange={(event) => setDefaultTermMonths(event.target.value)}
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    >
                      {bootstrap.installmentTerms.map((term) => (
                        <option key={term} value={term}>
                          {term} months
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="md:col-span-2">
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Description</label>
                    <textarea
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      rows={3}
                      placeholder="Describe what is inside this product card and its positioning."
                      className="w-full resize-none rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>
                </div>
              </section>

              <section className="rounded-3xl border border-slate-200 p-5">
                <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Pricing</h4>
                <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Installment Price</label>
                    <input
                      type="number"
                      value={price}
                      onChange={(event) => setPrice(event.target.value)}
                      placeholder="0.00"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Cash Price</label>
                    <input
                      type="number"
                      value={cashPrice}
                      onChange={(event) => setCashPrice(event.target.value)}
                      placeholder="0.00"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Payment Rate</label>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={paymentRate}
                      onChange={(event) => setPaymentRate(event.target.value)}
                      placeholder="Daily customer payment"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                    <div className="mt-1 text-xs text-slate-500">Amount a customer pays each day.</div>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700">Cost Price</label>
                    <input
                      type="number"
                      value={costPrice}
                      onChange={(event) => setCostPrice(event.target.value)}
                      placeholder="0.00"
                      className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Installment margin</div>
                    <div className="mt-2 text-lg font-semibold text-slate-900">{installmentMargin.toFixed(2)}%</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Cash margin</div>
                    <div className="mt-2 text-lg font-semibold text-slate-900">{cashMargin.toFixed(2)}%</div>
                  </div>
                </div>
              </section>
            </div>

            <div className="min-w-0 space-y-6">
              <section className="rounded-3xl border border-slate-200 p-5">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Product image</h4>
                  {imageFile && <span className="text-xs text-slate-500">{imageFile.name}</span>}
                </div>
                <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-center">
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50">
                    <input
                      type="file"
                      accept="image/*"
                      className="hidden"
                      onChange={(event) => handleImageSelection(event.target.files?.[0] || null)}
                    />
                    <ShoppingBag className="h-4 w-4" />
                    Choose image
                  </label>

                  <div className="h-24 w-24 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                    {imagePreview ? (
                      <img src={imagePreview} alt="Preview" className="h-full w-full object-contain p-2" />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center">
                        <Package className="h-7 w-7 text-slate-300" />
                      </div>
                    )}
                  </div>
                </div>
              </section>

              <section className="rounded-3xl border border-slate-200 p-5">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Inventory components</h4>
                  <div className="text-xs text-slate-500">
                    {componentCount} units | GHS {componentCostTotal.toFixed(2)}
                  </div>
                </div>

                <div className="relative mt-4">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input
                    value={componentSearch}
                    onChange={(event) => setComponentSearch(event.target.value)}
                    placeholder="Search inventory components..."
                    className="w-full rounded-2xl border border-slate-200 px-4 py-3 pl-10 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                </div>

                <div className="mt-4 max-h-72 space-y-3 overflow-y-auto pr-1">
                  {filteredInventory.map((item) => {
                    const quantity = selectedComponents[item.key] || 0;
                    return (
                      <div
                        key={item.key}
                        className={`flex items-center gap-3 rounded-2xl border p-3 transition ${
                          quantity > 0 ? 'border-indigo-300 bg-indigo-50/50' : 'border-slate-200 bg-white'
                        }`}
                      >
                        <div className="h-14 w-14 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                          {item.imageUrl ? (
                            <ImageWithFallback src={item.imageUrl} alt={item.name} className="h-full w-full object-contain p-2" />
                          ) : (
                            <div className="flex h-full w-full items-center justify-center">
                              <Package className="h-5 w-5 text-slate-300" />
                            </div>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-3">
                            <div className="truncate text-sm font-semibold text-slate-900">{item.name}</div>
                            <div className="text-sm font-semibold text-slate-900">GHS {item.price.toFixed(2)}</div>
                          </div>
                          <div className="mt-1 truncate text-xs text-slate-500">
                            {item.description || 'No component description'}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => adjustQuantity(item.key, -1)}
                            disabled={quantity === 0}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            -
                          </button>
                          <div className="w-8 text-center text-sm font-semibold text-slate-900">{quantity}</div>
                          <button
                            type="button"
                            onClick={() => adjustQuantity(item.key, 1)}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 text-slate-700 transition hover:bg-slate-50"
                          >
                            +
                          </button>
                        </div>
                      </div>
                    );
                  })}

                  {filteredInventory.length === 0 && (
                    <div className="rounded-2xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500">
                      No inventory components match your search.
                    </div>
                  )}
                </div>
              </section>

              <section className="rounded-3xl border border-slate-200 p-5">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">Managers</h4>
                  <button
                    type="button"
                    onClick={toggleAllManagers}
                    disabled={isEditing}
                    className="text-xs font-semibold text-indigo-600 hover:underline disabled:cursor-not-allowed disabled:text-slate-400"
                  >
                    {allManagersSelected ? 'Deselect all' : 'Select all'}
                  </button>
                </div>

                <div className="mt-4 grid max-h-72 grid-cols-1 gap-3 overflow-y-auto pr-1 md:grid-cols-2">
                  {bootstrap.managers.map((manager) => {
                    const checked = !!selectedManagers[manager.id];
                    return (
                      <label
                        key={manager.id}
                        className={`flex cursor-pointer items-center gap-3 rounded-2xl border p-3 transition ${
                          checked ? 'border-indigo-300 bg-indigo-50/50' : 'border-slate-200 bg-white hover:bg-slate-50'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={isEditing}
                          onChange={(event) =>
                            setSelectedManagers((current) => ({ ...current, [manager.id]: event.target.checked }))
                          }
                          className="h-4 w-4 rounded accent-indigo-600"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-semibold text-slate-900">{manager.name}</div>
                          <div className="mt-1 truncate text-xs text-slate-500">{manager.branch || 'No branch'}</div>
                        </div>
                        {checked && <Check className="h-4 w-4 text-indigo-600" />}
                      </label>
                    );
                  })}
                </div>
              </section>
            </div>
          </div>

          <datalist id="product-card-types">
            {bootstrap.productTypes.map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
          <datalist id="product-card-categories">
            {bootstrap.categories.map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
          <datalist id="product-card-packages">
            {bootstrap.packageNames.map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
        </div>

        <div className="border-t border-slate-200 px-6 py-4">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-slate-500">
              Selected {selectedCount(selectedManagers)} manager(s) and {componentCount} component unit(s).
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={onClose}
                className="inline-flex items-center justify-center rounded-2xl border border-slate-200 px-4 py-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={submitting}
                className="inline-flex min-w-[180px] items-center justify-center gap-2 rounded-2xl bg-indigo-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-70"
              >
                {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                {submitting ? 'Saving...' : isEditing ? 'Save Changes' : 'Save Product Card'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
