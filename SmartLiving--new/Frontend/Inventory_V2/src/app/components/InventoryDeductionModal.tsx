import { useEffect, useRef, useState } from 'react';
import { Loader2, X, CheckCircle2, AlertTriangle } from 'lucide-react';

export type DeductionJob = { id: string; label: string; url: string; body: Record<string, unknown> };
type Component = { name: string; required_quantity: number; deducted_quantity: number; remaining_quantity: number; deducted_now?: number; reason?: string; allocations?: {location_name: string; quantity: number}[] };
type Result = { job: DeductionJob; error?: string; report?: { status: string; deducted_now: number; value_now: number; branch: string; components: Component[]; replayed?: boolean } };

export function InventoryDeductionModal({jobs, onClose, onFinished}: {jobs: DeductionJob[]; onClose: () => void; onFinished: () => void}) {
  const started = useRef(false);
  const finish = useRef(onFinished);
  const [results, setResults] = useState<Result[]>([]);
  const [active, setActive] = useState('');
  const [running, setRunning] = useState(true);
  const run = async (pending: DeductionJob[], previous: Result[] = []) => {
    setRunning(true);
    let completed = [...previous];
    setResults(completed);
    for (const job of pending) {
      setActive(job.label);
      try {
        const response = await fetch(job.url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(job.body)});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to confirm deduction. Retry safely.');
        completed = [...completed, {job, report: data.deduction}];
      } catch (error) {
        completed = [...completed, {job, error: error instanceof Error ? error.message : 'Unable to confirm deduction.'}];
      }
      setResults(completed);
    }
    setRunning(false);
    setActive('');
    finish.current();
  };
  useEffect(() => { if (!started.current) { started.current = true; void run(jobs); } }, []);
  const errors = results.filter(row => row.error);
  const units = results.reduce((sum, row) => sum + (row.report?.deducted_now || 0), 0);
  return <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-label="Inventory Deduction">
    <div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-2xl bg-white p-6 text-slate-900 shadow-2xl">
      <div className="flex items-center justify-between"><h2 className="text-xl font-bold">Inventory Deduction</h2><button type="button" disabled={running} onClick={onClose} aria-label="Close deduction report" className="disabled:opacity-30"><X /></button></div>
      <div className="my-4" aria-live="polite">
        <div className="mb-2 flex items-center gap-2">{running ? <Loader2 className="animate-spin" /> : <CheckCircle2 />} {running ? `Processing ${active}` : 'Processing complete'} <span className="ml-auto">{results.length} / {jobs.length} cards processed</span></div>
        <progress className="h-3 w-full" max={jobs.length || 1} value={results.length} />
        {running && <p className="mt-2 text-sm text-slate-500">Checking component requirements and branch warehouses, then saving stock movements. Keep this window open.</p>}
      </div>
      {!running && <p className="mb-4 rounded-lg bg-slate-100 p-3">{units} units deducted in these operations. {results.filter(r => r.report?.status === 'deducted').length} fully deducted cards; {results.filter(r => ['partially_deducted', 'not_deducted'].includes(r.report?.status || '')).length} cards with outstanding items; {errors.length} errors.</p>}
      {results.map(row => <div key={row.job.id} className="mb-3 rounded-xl border p-3">
        <h3 className="font-semibold">{row.job.label}</h3>
        {row.error ? <p className="mt-2 flex gap-2 text-sm text-red-700"><AlertTriangle className="h-4 w-4 shrink-0" />{row.error}</p> : <>
          <p className="text-sm text-slate-500">{row.report?.branch} - {row.report?.status?.replaceAll('_', ' ')}{row.report?.replayed ? ' (previously saved result)' : ''}</p>
          <div className="overflow-x-auto"><table className="mt-3 w-full text-left text-sm"><thead><tr><th>Product</th><th>Required</th><th>Total deducted</th><th>Remaining</th></tr></thead><tbody>
            {row.report?.components?.map((component, index) => <tr key={index} className="border-t"><td className="py-2">{component.name}<div className="text-xs text-slate-500">{component.remaining_quantity > 0 ? component.reason?.replaceAll('_', ' ') : 'Complete'}</div><div className="text-xs text-slate-500">{component.allocations?.map(a => `${a.location_name}: ${a.quantity}`).join(', ')}</div></td><td>{component.required_quantity}</td><td>{component.deducted_quantity}</td><td className={component.remaining_quantity ? 'font-semibold text-amber-700' : ''}>{component.remaining_quantity}</td></tr>)}
          </tbody></table></div>
        </>}
      </div>)}
      {!running && <div className="mt-5 flex justify-end gap-3">{errors.length > 0 && <button type="button" className="rounded-lg border px-4 py-2" onClick={() => void run(errors.map(r => r.job), results.filter(r => !r.error))}>Retry unsuccessful cards</button>}<button type="button" className="rounded-lg bg-indigo-600 px-4 py-2 text-white" onClick={onClose}>Close report</button></div>}
    </div>
  </div>;
}
