import { useEffect, useState } from 'react';

type MissingRecord = {
  id: string; version: number; customer_name: string; agent_name: string; agent_branch: string;
  product_name: string; status: string; status_label: string; created_at: string;
  inventory_reason: string; inventory_updated_by_name: string;
  undelivered_items: {name: string; undelivered_qty: number}[];
};

export function UndeliveredItems() {
  const [rows, setRows] = useState<MissingRecord[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [filter, setFilter] = useState('open');
  const [editing, setEditing] = useState<MissingRecord | null>(null);
  const [reason, setReason] = useState('');
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/undelivered_items/data?inventory_queue=1&status=${filter}&page=${page}`, {credentials: 'same-origin', signal: controller.signal})
      .then(async response => {const data = await response.json(); if (!response.ok || !data.ok) throw Error(data.message || 'Unable to load items.'); return data;})
      .then(data => {setRows(data.rows); setPages(data.pages); setPage(data.page); setError('');})
      .catch(err => {if (err.name !== 'AbortError') setError(err.message);});
    return () => controller.abort();
  }, [page, filter, refresh]);
  useEffect(() => {
    const update = () => {if (!document.hidden) setRefresh(value => value + 1);};
    const timer = window.setInterval(update, 30000);
    window.addEventListener('focus', update);
    return () => {window.clearInterval(timer); window.removeEventListener('focus', update);};
  }, []);
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); if (!editing || busy) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/undelivered_items/${editing.id}/inventory-update`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reason, status, version: editing.version})});
      const data = await response.json();
      if (!response.ok || !data.ok) throw Error(data.message || 'Unable to save update.');
      setEditing(null); setMessage('Inventory reason and status saved. The manager and agent can see this update.'); setRefresh(value => value + 1);
    } catch (err) {setError(err instanceof Error ? err.message : 'Unable to save update.');}
    finally {setBusy(false);}
  };
  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="text-2xl font-semibold">Undelivered Items</h1><p className="text-muted-foreground">Missing products sent by managers. Record a reason or availability update for the manager and agent.</p></div>
      <button className="sl-button px-4 py-2 border rounded-lg" onClick={() => setRefresh(value => value + 1)}>Refresh</button>
    </div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {message && <p role="status" className="text-green-600">{message}</p>}
    <label className="flex gap-3 items-center">Status <select className="border rounded-lg p-2 bg-background" value={filter} onChange={event => {setFilter(event.target.value); setPage(1);}}>
      <option value="open">All undelivered</option><option value="pending">Undelivered</option><option value="preparing">Preparing</option><option value="products_in">Products In</option><option value="delivered">Delivered</option><option value="all">All statuses</option>
    </select></label>
    <div className="overflow-auto border rounded-xl"><table className="w-full text-sm text-left"><thead className="bg-muted"><tr>{['Submitted', 'Customer / Agent', 'Product / Missing items', 'Status', 'Inventory reason', 'Action'].map(label => <th key={label} className="p-3">{label}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} className="border-t">
        <td className="p-3">{row.created_at ? new Date(row.created_at).toLocaleDateString() : ''}</td>
        <td className="p-3">{row.customer_name}<div className="text-muted-foreground">{row.agent_name} · {row.agent_branch}</div></td>
        <td className="p-3"><strong>{row.product_name}</strong><ul>{row.undelivered_items.map((item, index) => <li key={index}>{item.name} × {item.undelivered_qty}</li>)}</ul></td>
        <td className="p-3">{row.status_label}</td><td className="p-3 whitespace-pre-wrap">{row.inventory_reason || 'Awaiting inventory update'}<div className="text-muted-foreground">{row.inventory_updated_by_name}</div></td>
        <td className="p-3">{row.status !== 'delivered' && <button className="border rounded-lg px-3 py-2" onClick={() => {setEditing(row);setReason(row.inventory_reason || '');setStatus(row.status);setError('');}}>Update with reason</button>}</td>
      </tr>)}{!rows.length && <tr><td colSpan={6} className="p-6 text-center">No forwarded items match these filters.</td></tr>}</tbody>
    </table></div>
    <div className="flex justify-between items-center"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    {editing && <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={event => {if (event.target === event.currentTarget && !busy) setEditing(null);}}>
      <form role="dialog" aria-modal="true" aria-labelledby="inventory-reason-heading" className="bg-background border rounded-xl p-6 space-y-4 w-full max-w-lg max-h-[90vh] overflow-auto" onSubmit={save}>
        <h2 id="inventory-reason-heading" className="text-xl font-semibold">Update undelivered items</h2><p>{editing.customer_name} · {editing.product_name}</p>
        <label className="block">Status<select aria-label="Status" className="block w-full border rounded-lg p-2 bg-background" value={status} onChange={event => setStatus(event.target.value)}><option value="pending">Undelivered</option><option value="preparing">Preparing</option><option value="products_in">Products In</option></select></label>
        <label className="block">Reason / inventory update<textarea required maxLength={2000} rows={4} className="block w-full border rounded-lg p-2 bg-background" value={reason} onChange={event => setReason(event.target.value)} /></label>
        <p className="text-sm text-muted-foreground">Explain why the items are unavailable or give an availability update.</p>
        {error && <p role="alert" className="text-red-600">{error}</p>}
        <div className="flex gap-3"><button disabled={busy} type="submit" className="border rounded-lg px-4 py-2">{busy ? 'Saving…' : 'Save update'}</button><button disabled={busy} type="button" onClick={() => setEditing(null)}>Cancel</button></div>
      </form>
    </div>}
  </section>;
}
