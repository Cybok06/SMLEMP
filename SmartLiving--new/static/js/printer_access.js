(() => {
  'use strict';
  const config = JSON.parse(document.getElementById('printerAccessConfig').textContent);
  const el = id => document.getElementById(id);
  const table = (scope) => el(scope === 'agent' ? 'agentRows' : 'branchRows');
  let cursor = null, branchLoaded = false, requestVersion = 0, searchTimer = null;
  function status(message, error = false) {
    el('printerAccessMessage').textContent = message;
    el('printerAccessMessage').className = 'small mb-3 ' + (error ? 'text-danger' : 'text-success');
  }
  async function readJson(response) {
    if (!(response.headers.get('content-type') || '').includes('application/json'))
      throw new Error('Your session may have expired. Refresh this page and sign in again.');
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || 'Could not load printer access.');
    return result;
  }
  function cell(row, value) {
    const td = document.createElement('td');
    td.textContent = value;
    row.appendChild(td);
    return td;
  }
  function actions(row, scope, target) {
    const td = document.createElement('td');
    td.className = 'd-flex gap-2 flex-wrap';
    for (const [action, label, kind] of [
      ['grant', 'Grant', 'success'], ['revoke', 'Revoke', 'danger'],
      ...(scope === 'agent' ? [['inherit', 'Use branch setting', 'secondary']] : [])
    ]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm btn-outline-' + kind;
      button.textContent = label;
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const body = new FormData();
          for (const [key, value] of Object.entries({csrf: config.csrf, scope, target, action})) body.append(key, value);
          await readJson(await fetch(config.pageUrl, {method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest'}, body}));
          status('Printer access updated.');
          if (scope === 'branch') await loadBranches(true);
          await loadAgents(true);
        } catch (error) { status(error.message, true); }
        finally { button.disabled = false; }
      });
      td.appendChild(button);
    }
    row.appendChild(td);
  }
  async function loadAgents(reset = false) {
    if (reset) { cursor = null; table('agent').replaceChildren(); }
    const version = ++requestVersion;
    el('loadMoreAgents').hidden = true;
    el('agentLoading').textContent = 'Loading agents...';
    const url = new URL(config.agentsUrl, location.origin);
    const term = el('agentSearch').value.trim();
    if (term) url.searchParams.set('q', term);
    if (el('branchFilter').value) url.searchParams.set('branch', el('branchFilter').value);
    if (cursor) url.searchParams.set('after', cursor);
    try {
      const result = await readJson(await fetch(url));
      if (version !== requestVersion) return;
      for (const agent of result.agents) {
        const row = document.createElement('tr');
        cell(row, agent.name);
        cell(row, agent.branch || 'Unassigned');
        const access = cell(row, agent.enabled ? 'Enabled' : 'Disabled');
        access.className = agent.enabled ? 'text-success fw-semibold' : 'text-secondary';
        cell(row, agent.override === null ? 'Branch setting' : agent.override ? 'Individual grant' : 'Individual restriction');
        actions(row, 'agent', agent.id);
        table('agent').appendChild(row);
      }
      if (!table('agent').children.length) {
        const row = document.createElement('tr');
        const empty = cell(row, 'No agents found.'); empty.colSpan = 5;
        table('agent').appendChild(row);
      }
      cursor = result.next_cursor;
      el('loadMoreAgents').hidden = !cursor;
      el('agentLoading').textContent = '';
    } catch (error) { if (version === requestVersion) el('agentLoading').textContent = error.message; }
  }
  async function loadBranches(force = false) {
    if (branchLoaded && !force) return;
    el('branchLoading').textContent = 'Loading branches...';
    try {
      const result = await readJson(await fetch(config.branchesUrl));
      branchLoaded = true;
      table('branch').replaceChildren();
      const selected = el('branchFilter').value;
      el('branchFilter').replaceChildren(new Option('All branches', ''));
      for (const branch of result.branches) {
        const row = document.createElement('tr');
        cell(row, branch.name);
        cell(row, branch.enabled ? 'Enabled' : 'Disabled');
        actions(row, 'branch', branch.name);
        table('branch').appendChild(row);
        el('branchFilter').add(new Option(branch.name, branch.name));
      }
      el('branchFilter').value = selected;
      el('branchLoading').textContent = result.branches.length ? '' : 'No branches found.';
      el('loadBranchFilters').hidden = true;
    } catch (error) { el('branchLoading').textContent = error.message; }
  }
  el('branchSection').addEventListener('toggle', () => { if (el('branchSection').open) loadBranches(); });
  el('loadBranchFilters').addEventListener('click', () => loadBranches());
  el('branchFilter').addEventListener('change', () => loadAgents(true));
  el('agentSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadAgents(true), 300);
  });
  el('loadMoreAgents').addEventListener('click', () => loadAgents());
  loadAgents(true);
})();
