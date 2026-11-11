import { useEffect, useState } from 'react';
import { BarChart3, Loader2, RefreshCw, X } from 'lucide-react';
import { InventoryDeductionModal, type DeductionJob } from './InventoryDeductionModal';

type Component = { name: string; required_quantity: number; deducted_quantity: number; remaining_quantity: number; reason?: string; total_cost: number; allocations?: { location_name: string; quantity: number }[] };
type RecordRow = { _id: string; package_id: string; deduction_batch_id: string; agent_name: string; customer_name: string; product_card: string; branch: string; status: string; confirmed_at: string; first_attempt_at?: string; total_units: number; total_cost_value: number; components: Component[] };
type Attempt = { _id: string; created_at: string; kind: string; deducted_now: number; value_now: number; actor?: { name: string }; components: Component[] };
const cash = (n: number) => `GHS ${Number(n || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const field = 'rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm';
async function get(url: string) { const response = await fetch(url, {credentials: 'same-origin'}); const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load stock records.'); return data; }

export function PackagingStockLedger() {
  const [tab, setTab] = useState<'history' | 'outstanding'>('history');
  const [rows, setRows] = useState<RecordRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [branch, setBranch] = useState('');
  const [warehouse, setWarehouse] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<RecordRow | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [attemptError, setAttemptError] = useState('');
  const [jobs, setJobs] = useState<DeductionJob[]>([]);
  const [metricsOpen, setMetricsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError('');
    const params = new URLSearchParams({page: String(page), search, branch, warehouse, outstanding: tab === 'outstanding' ? '1' : '0'});
    if (from && to) { params.set('fromDate', from); params.set('toDate', to); }
    const timer = setTimeout(() => { void get(`/api/inventory/audit/packaging-stock?${params}`).then(data => { if (current) { setRows(data.rows); setTotal(data.total); } }).catch(e => {if (current) setError(e.message);}).finally(() => {if (current) setLoading(false);}); }, 200);
    return () => { current = false; clearTimeout(timer); };
  }, [page, search, branch, warehouse, from, to, tab, revision]);
  useEffect(() => {
    let current = true;
    setAttempts([]); setAttemptError('');
    if (detail) void get(`/api/inventory/audit/packaging-stock/${detail.package_id}/attempts`).then(data => {if (current) setAttempts(data.rows);}).catch(e => {if (current) setAttemptError(e.message);});
    return () => {current = false;};
  }, [detail]);
  const settle = (row: RecordRow) => setJobs([{id: row.package_id, label: `${row.customer_name || row.agent_name} - ${row.product_card}`, url: `/api/inventory/audit/packaging-stock/${row.package_id}/settle`, body: {requestId: crypto.randomUUID()}}]);
  return <div className="space-y-4 text-slate-900">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-bold">Stock Deduction</h2><p className="text-sm text-slate-500">Stock is deducted at Packaging. Settle shortages here before final delivery.</p></div><div className="flex gap-2"><button className={field} onClick={() => setRevision(v => v + 1)}><RefreshCw className="inline h-4 w-4" /> Refresh</button><button className={field} onClick={() => setMetricsOpen(true)}><BarChart3 className="inline h-4 w-4" /> Metrics</button></div></div>
    <div className="flex gap-2">{(['history', 'outstanding'] as const).map(value => <button key={value} className={`rounded-lg px-4 py-2 text-sm font-semibold ${tab === value ? 'bg-indigo-600 text-white' : 'bg-slate-100'}`} onClick={() => {setTab(value); setPage(1); setFrom(''); setTo('');}}>{value === 'history' ? 'Deduction History' : 'Partially Delivered'}</button>)}</div>
    {tab === 'outstanding' && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Outstanding inventory components, including cards with zero deducted stock. Settlement uses the branch recorded at Packaging. Physical delivery is tracked separately.</p>}
    <div className="flex flex-wrap gap-2"><input aria-label="Search agent, customer, card or reference" placeholder="Agent, customer, card or reference" value={search} onChange={e => {setSearch(e.target.value); setPage(1);}} className={field} /><input aria-label="Branch" placeholder="Branch name" value={branch} onChange={e => {setBranch(e.target.value); setPage(1);}} className={field} /><input aria-label="Warehouse" placeholder="Warehouse name" value={warehouse} onChange={e => {setWarehouse(e.target.value); setPage(1);}} className={field} /><label className="text-xs">First packaging from <input type="date" value={from} onChange={e => {setFrom(e.target.value); setPage(1);}} className={field} /></label><label className="text-xs">To <input type="date" value={to} onChange={e => {setTo(e.target.value); setPage(1);}} className={field} /></label></div>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {loading ? <p className="flex gap-2 p-8"><Loader2 className="animate-spin" />Loading deductions...</p> : <div className="overflow-x-auto rounded-xl border"><table className="w-full text-left text-sm"><thead className="bg-slate-50"><tr>{['Reference / Date', 'Agent / Customer', 'Card / Branch', 'Deducted / Remaining', 'Value', 'Status', 'Actions'].map(h => <th className="p-3" key={h}>{h}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row._id} className="border-t"><td className="p-3">{row.deduction_batch_id}<div className="text-xs text-slate-500">{row.confirmed_at?.slice(0, 19).replace('T', ' ')}</div></td><td className="p-3">{row.agent_name || 'Historical record'}<div className="text-xs text-slate-500">{row.customer_name}</div></td><td className="p-3">{row.product_card}<div className="text-xs text-slate-500">{row.branch}</div></td><td className="p-3">{row.total_units || 0} / {row.components?.reduce((n, c) => n + c.remaining_quantity, 0) || 0}</td><td className="p-3">{cash(row.total_cost_value)}</td><td className="p-3">{row.status?.replaceAll('_', ' ')}</td><td className="space-x-2 whitespace-nowrap p-3"><button className="text-indigo-700" onClick={() => setDetail(row)}>Details</button>{['partially_deducted', 'not_deducted'].includes(row.status) && <button className="rounded bg-indigo-600 px-3 py-1 text-white" onClick={() => settle(row)}>Settle</button>}</td></tr>)}</tbody></table>{!rows.length && <p className="p-8 text-center text-slate-500">No matching deductions.</p>}</div>}
    <div className="flex items-center justify-between text-sm"><span>{total} records</span><div className="flex items-center gap-3"><button className={field} disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</button><span>Page {page}</span><button className={field} disabled={page * 25 >= total} onClick={() => setPage(p => p + 1)}>Next</button></div></div>
    {detail && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4" role="dialog" aria-modal="true" aria-label="Deduction details"><div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-2xl bg-white p-6"><div className="flex justify-between"><h3 className="text-lg font-bold">{detail.product_card || 'Deduction details'} - {detail.branch}</h3><button aria-label="Close details" onClick={() => setDetail(null)}><X /></button></div><p className="my-2 text-sm">{detail.agent_name} / {detail.customer_name}</p><Components rows={detail.components || []} /><h4 className="mt-6 font-semibold">Processing and settlement history</h4>{attemptError && <p className="text-red-700">{attemptError}</p>}{attempts.map(a => <div key={a._id} className="mt-3 rounded border p-3"><p className="text-sm">{a.created_at?.slice(0, 19).replace('T', ' ')} - {a.kind} - {a.actor?.name}</p><p className="text-sm">{a.deducted_now} units / {cash(a.value_now)}</p><Components rows={a.components || []} /></div>)}</div></div>}
    {jobs.length > 0 && <InventoryDeductionModal jobs={jobs} onClose={() => setJobs([])} onFinished={() => {setRevision(v => v + 1); setDetail(null);}} />}
    {metricsOpen && <Metrics onClose={() => setMetricsOpen(false)} />}
  </div>;
}
function Components({rows}: {rows: Component[]}) {return <div className="overflow-x-auto"><table className="mt-3 w-full text-left text-sm"><thead><tr><th>Component</th><th>Required</th><th>Deducted</th><th>Remaining</th></tr></thead><tbody>{rows.map((c, i) => <tr key={i} className="border-t"><td className="py-2">{c.name}<div className="text-xs text-slate-500">{c.reason?.replaceAll('_', ' ')} {c.allocations?.map(a => `${a.location_name}: ${a.quantity}`).join(', ')}</div></td><td>{c.required_quantity}</td><td>{c.deducted_quantity}</td><td>{c.remaining_quantity}</td></tr>)}</tbody></table></div>;}
function Metrics({onClose}: {onClose: () => void}) {
  const [period, setPeriod] = useState('today');
  const [from, setFrom] = useState(''); const [to, setTo] = useState('');
  const [data, setData] = useState<any>(null); const [error, setError] = useState('');
  useEffect(() => {let current = true; setData(null); setError(''); if (period === 'custom' && (!from || !to)) return; void get(`/api/inventory/audit/packaging-stock/metrics?${new URLSearchParams({period, fromDate: from, toDate: to})}`).then(d => {if (current) setData(d);}).catch(e => {if (current) setError(e.message);}); return () => {current = false;};}, [period, from, to]);
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/50 p-4" role="dialog" aria-modal="true" aria-label="Stock deduction metrics"><div className="max-h-[90vh] w-full max-w-5xl overflow-auto rounded-2xl bg-white p-6"><div className="flex justify-between"><h2 className="text-xl font-bold">Stock Deduction Metrics</h2><button aria-label="Close metrics" onClick={onClose}><X /></button></div><div className="my-4 flex flex-wrap gap-2"><select aria-label="Metrics period" className={field} value={period} onChange={e => setPeriod(e.target.value)}><option value="today">Today</option><option value="month">This month</option><option value="custom">Custom period</option></select>{period === 'custom' && <><input aria-label="Metrics start date" type="date" className={field} value={from} onChange={e => setFrom(e.target.value)} /><input aria-label="Metrics end date" type="date" className={field} value={to} onChange={e => setTo(e.target.value)} /></>}</div>{error && <p className="text-red-700">{error}</p>}{!data && !error && <p className="p-5">{period === 'custom' && (!from || !to) ? 'Select a date range.' : 'Loading metrics...'}</p>}{data && <>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{[['Units deducted', data.deductedUnits], ['Deducted value', cash(data.deductedValue)], ['Not deducted at Packaging', data.newShortageUnits], ['Fully deducted cards at Packaging', data.fullyDeductedCards], ['Partially deducted cards at Packaging', data.partiallyDeductedCards], ['Zero-stock cards at Packaging', data.undeductedCards], ['Outstanding units (all dates)', data.outstandingUnits], ['Outstanding cards (all dates)', data.outstandingCards], ['Outstanding estimated value', cash(data.outstandingEstimatedValue)], ['Units without valuation', data.unvaluedOutstandingUnits], ['Oldest outstanding (days)', data.oldestOutstandingDays]].map(([label, value]) => <div key={String(label)} className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-lg font-bold">{value}</div></div>)}</div>
    <p className="my-4 text-xs text-slate-500">Deductions include settlements posted during the selected period. Shortages count initial Packaging demand once. Outstanding figures reflect the current backlog across all dates. Values use stock cost, not selling price. Dates use Ghana time.</p>
    <h3 className="mt-5 font-semibold">Branch fulfilment</h3><div className="overflow-x-auto"><table className="my-3 w-full text-left text-sm"><thead><tr>{['Branch', 'Required at Packaging', 'Initially deducted', 'Fulfilment rate', 'Deductions in period', 'Current outstanding'].map(h => <th key={h} className="p-2">{h}</th>)}</tr></thead><tbody>{data.branches.map((r: any) => <tr className="border-t" key={r.branch}><td className="p-2">{r.branch}</td><td>{r.required}</td><td>{r.deductedInitially}</td><td>{r.fulfilmentRate == null ? 'No new demand' : `${r.fulfilmentRate}%`}</td><td>{r.deducted}</td><td>{r.outstanding}</td></tr>)}</tbody></table></div>
    <h3 className="mt-5 font-semibold">Warehouse activity</h3><p className="text-xs text-slate-500">Zero deductions indicate no stock movements in this period; they do not necessarily indicate a shortage.</p><table className="my-3 w-full text-left text-sm"><thead><tr><th>Warehouse</th><th>Branch</th><th>Units deducted</th><th>Value</th></tr></thead><tbody>{data.warehouses.map((r: any, i: number) => <tr key={i} className="border-t"><td className="py-2">{r.warehouse}</td><td>{r.branch}</td><td>{r.units}</td><td>{cash(r.value)}</td></tr>)}</tbody></table>
    <h3 className="mt-5 font-semibold">Frequently unavailable products</h3>{data.shortageProducts.map((r: any) => <p className="mt-2 text-sm" key={r.name}>{r.name}: {r.quantity} units</p>)}
  </>}</div></div>;
}
