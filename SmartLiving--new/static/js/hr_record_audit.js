(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
  window.hrAttributionBadge = record => {
    const author = record?.hr_recorded_by || {}, editor = record?.hr_last_updated_by || {};
    const badge = (person, label, color) => `<span class="hr-record-badge badge bg-${color}-subtle text-${color}-emphasis" title="${escape(person.branch)} · ${escape(person.at)}">${label} by Manager · ${escape(person.name || 'Manager')}</span>`;
    const origin = author.role === 'manager' ? badge(author, 'Recorded', 'info') : ['hr','admin','executive'].includes(author.role) ? `<span class="hr-record-badge badge bg-secondary-subtle text-secondary-emphasis" title="${escape(author.at)}">Recorded by ${author.role === 'hr' ? 'Main HR' : escape(author.role)} · ${escape(author.name)}</span>` : '';
    return origin +
      (editor.role === 'manager' && (editor.user_id !== author.user_id || editor.at !== author.at) ? badge(editor, 'Updated', 'warning') : '');
  };
  let lastPath = '', pending = 0;
  async function refresh() {
    const panel = document.getElementById('hr-manager-context');
    if (!panel || location.pathname === lastPath) return;
    lastPath = location.pathname;
    const version = ++pending;
    const target = panel.querySelector('[data-hr-manager-records]');
    try {
      const response = await fetch('/hr/activity-logs/context?' + new URLSearchParams({path: lastPath}), {credentials: 'same-origin'});
      if (!response.ok) throw Error('Unable to load manager activity.');
      const data = await response.json();
      if (version !== pending) return;
      panel.querySelector('[data-hr-manager-count]').textContent = data.records.length === 10 ? 'Latest 10' : String(data.records.length);
      target.innerHTML = data.records.map(record => `<div class="py-2 border-bottom"><span class="badge bg-info-subtle text-info-emphasis">${record.operation === 'created' ? 'Recorded' : record.operation === 'deleted' ? 'Deleted' : 'Updated'} by Manager · ${escape(record.actor.name)}</span> <strong>${escape(record.record_label)}</strong><div class="text-muted mt-1">${escape(record.action)} · ${escape(record.branch)} · ${escape(new Date(record.timestamp).toLocaleString('en-GB', {timeZone:'Africa/Accra'}))}</div></div>`).join('') || '<p class="text-muted mb-0">No manager activity has been recorded here yet.</p>';
    } catch (error) {
      target.textContent = error.message;
      panel.querySelector('[data-hr-manager-count]').textContent = 'Unavailable';
      lastPath = '';
    }
  }
  function start() {
    refresh();
    // HR uses partial-page navigation. Observe navigation changes without
    // attaching handlers to individual page implementations.
    new MutationObserver(() => { if (location.pathname !== lastPath) refresh(); }).observe(document.getElementById('hr-main-content') || document.body, {childList:true, subtree:true});
    window.addEventListener('popstate', refresh);
    window.addEventListener('focus', () => { lastPath = ''; refresh(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
