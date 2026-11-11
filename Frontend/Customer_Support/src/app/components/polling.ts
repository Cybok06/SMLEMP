// Share concurrent snapshots only; never reuse a completed response after a mutation.
const requests = new Map<string, Promise<any>>();
export function snapshot(url: string): Promise<any> {
  const pending = requests.get(url);
  if (pending) return pending;
  const work = fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then(async response => { const data = await response.json(); if (!response.ok || data.ok === false) throw Error(data.message || 'Unable to refresh'); return data; })
    .finally(() => requests.delete(url));
  requests.set(url, work);
  return work;
}

export function poll(work: () => Promise<unknown>, interval = 30000) {
  let stopped = false, active = false, failures = 0;
  let timer: ReturnType<typeof setTimeout>;
  const refresh = async () => {
    if (stopped || active || document.hidden) return;
    clearTimeout(timer); active = true;
    try { await work(); failures = 0; } catch { failures = Math.min(failures + 1, 4); }
    finally { active = false; if (!stopped) timer = setTimeout(refresh, interval * 2 ** failures); }
  };
  const visibility = () => { clearTimeout(timer); if (!document.hidden) void refresh(); };
  document.addEventListener('visibilitychange', visibility);
  void refresh();
  return { refresh, stop: () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); } };
}
