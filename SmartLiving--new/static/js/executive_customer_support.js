(() => {
  const $ = id => document.getElementById(id);
  const apiPrefix = document.querySelector('[data-api-prefix]').dataset.apiPrefix;
  const today = new Date().toISOString().slice(0, 10);
  const labels = { calls: 'Calls', field_trips: 'Field trips', interactions: 'Customer interactions' };
  const periods = { daily: 'Daily', weekly: 'Weekly', per_trip: 'Per trip' };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const timestamp = value => new Date(value).toLocaleString('en-GB', { timeZone: 'Africa/Accra', dateStyle: 'medium', timeStyle: 'short' });
  let controller, performanceData;
  function renderPerformance() {
    const summary = performanceData?.[$('performance-period').value];
    const labels = {completed_calls:'Completed calls',field_trips:'Field trips',field_interactions:'Field interactions',recorded_interactions:'Activity interactions (all channels)',issues_resolved:'Issues resolved',repeat_opportunities:'Repeat-purchase opportunities',assigned_followups:'Assigned follow-ups',resulting_purchases:'Resulting purchases'};
    $('performance-dates').textContent = summary ? `${summary.start} to ${summary.end} (Ghana)` : 'Select an officer.';
    $('performance').innerHTML = summary ? Object.entries(labels).map(([key,label])=>`<div class="col-6 col-lg-3"><div class="bg-light rounded-3 p-3 h-100"><div class="small muted">${escape(label)}</div><strong class="fs-3">${summary[key]}</strong></div></div>`).join('') : '';
  }
  $('performance-period').addEventListener('change',renderPerformance);
  $('report-date').value = today; $('report-date').max = today;
  $('effective').value = today; $('effective').min = today;
  function message(text, success = false) { $('message').textContent = text; $('message').className = `alert alert-${success ? 'success' : 'danger'}`; }
  async function api(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || result.error || 'Unable to complete request.');
    return result;
  }
  function updatePeriods() {
    const allowed = $('metric').value === 'field_trips' ? ['weekly'] : $('metric').value === 'calls' ? ['daily', 'weekly'] : ['daily', 'weekly', 'per_trip'];
    $('period').replaceChildren(...allowed.map(key => new Option(periods[key], key)));
  }
  async function load() {
    if (!$('officer').value || !$('report-date').value) return;
    controller?.abort(); controller = new AbortController();
    $('refresh').disabled = true;
    try {
      const result = await api(`${apiPrefix}/progress?${new URLSearchParams({ officer_id: $('officer').value, date: $('report-date').value })}`, { signal: controller.signal });
      $('progress').innerHTML = result.targets.length ? result.targets.map(target => `<div class="col-md-6"><section class="card p-4 h-100"><div class="d-flex justify-content-between"><strong>${escape(labels[target.metric])}</strong><span class="badge bg-primary-subtle text-primary">${escape(periods[target.period])}</span></div><div class="muted small mt-1">${escape(target.start)} – ${escape(target.end)}</div><div class="metric-value mt-3">${target.actual} <span class="fs-6 muted">/ ${target.goal} ${target.period === 'per_trip' ? 'trips meeting target' : 'completed'}</span></div>${target.period === 'per_trip' ? `<p class="small muted">${target.amount} interactions required per trip</p>` : ''}<div class="progress mt-3" role="progressbar" aria-label="${escape(labels[target.metric])} progress" aria-valuenow="${Math.min(target.percent, 100)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar ${target.status === 'Achieved' ? 'bg-success' : ''}" style="width:${Math.min(target.percent, 100)}%"></div></div><div class="d-flex justify-content-between mt-2 small"><span>${escape(target.status)}${target.remaining ? ` · ${target.remaining} remaining` : ''}</span><strong>${target.percent}%</strong></div></section></div>`).join('') : '<div class="col-12"><div class="card p-5 text-center muted">No active targets for this reporting date. Configure a target to get started.</div></div>';
      performanceData = result.performance; renderPerformance();
      $('visits').innerHTML = result.visits.length ? result.visits.map(v => `<tr><td class="p-3 fw-semibold">${escape(v.location)}</td><td class="p-3 text-nowrap">${escape(timestamp(v.started_at))}<br>${escape(timestamp(v.ended_at))}</td><td class="p-3">${v.duration_minutes} min</td><td class="p-3 fw-bold">${v.interactions}</td><td class="p-3">${v.interaction_target ? `${v.interaction_target} · ${v.remaining ? `${v.remaining} remaining` : 'Achieved'}` : 'Not set'}</td><td class="p-3 visit-notes">${escape(v.notes)}</td></tr>`).join('') : '<tr><td colspan="6" class="p-4 text-center muted">No visits recorded for this week.</td></tr>';
      $('history').innerHTML = result.history.length ? result.history.map(row => `<tr><td class="p-3">${escape(labels[row.metric])}</td><td class="p-3">${escape(periods[row.period])}</td><td class="p-3">${row.amount || 'Disabled'}</td><td class="p-3">${escape(row.effective_from)}</td><td class="p-3">${escape(timestamp(row.created_at))}</td></tr>`).join('') : '<tr><td colspan="5" class="p-4 text-center muted">No target changes yet.</td></tr>';
    } catch (error) { if (error.name !== 'AbortError') { message(error.message); performanceData = null; renderPerformance(); $('progress').innerHTML = '<p class="muted">Progress could not be loaded. Refresh to retry.</p>'; $('visits').replaceChildren(); $('history').replaceChildren(); } }
    finally { $('refresh').disabled = false; }
  }
  $('metric').addEventListener('change', updatePeriods); updatePeriods();
  $('officer').addEventListener('change', () => { $('message').className = 'd-none'; load(); });
  $('report-date').addEventListener('change', load); $('refresh').addEventListener('click', load);
  $('target-form').addEventListener('submit', async event => {
    event.preventDefault(); $('save').disabled = true;
    try {
      await api(`${apiPrefix}/targets`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ officer_id: $('officer').value, metric: $('metric').value, period: $('period').value, amount: $('amount').value, effective_from: $('effective').value }) });
      message('Target saved successfully.', true); await load();
    } catch (error) { message(error.message); }
    finally { $('save').disabled = !$('officer').value; }
  });
  api(`${apiPrefix}/officers`).then(result => {
    $('officer').replaceChildren(...result.officers.map(o => new Option(`${o.name}${o.branch ? ` · ${o.branch}` : ''}`, o.id)));
    $('officer').disabled = !result.officers.length; $('save').disabled = !result.officers.length;
    if (result.officers.length) load();
    else { $('officer').add(new Option('No customer support officers found', '')); message('Create a customer support officer account before assigning targets.'); }
  }).catch(error => message(error.message));
})();
