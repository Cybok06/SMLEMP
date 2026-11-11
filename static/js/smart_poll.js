/* Visible-tab, non-overlapping polling. Returned promises control completion. */
(() => {
  if (window.smartPoll) return;
  window.smartPoll = (work, interval = 30000) => {
    let active = false, stopped = false, failures = 0, timer;
    const refresh = async () => {
      if (stopped || active || document.hidden) return;
      clearTimeout(timer); active = true;
      try { await work(); failures = 0; } catch { failures = Math.min(failures + 1, 4); }
      finally { active = false; if (!stopped) timer = setTimeout(refresh, interval * 2 ** failures); }
    };
    const visibility = () => { clearTimeout(timer); if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visibility);
    refresh();
    return { refresh, stop: () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); } };
  };
})();
