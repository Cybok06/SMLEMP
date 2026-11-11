(() => {
  const fmtMoney = (n) => 'GHS ' + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const state = {
    page: 1,
    limit: 25,
    sort: 'newest',
    q: '',
    branch: '',
    manager_id: '',
    agent_id: '',
    start: '',
    end: ''
  };

  const els = {
    kpiRange: document.getElementById('kpiRange'),
    kpiToday: document.getElementById('kpiToday'),
    kpiWeek: document.getElementById('kpiWeek'),
    kpiMonth: document.getElementById('kpiMonth'),
    kpiTotal: document.getElementById('kpiTotal'),
    dailyChart: document.getElementById('dailyChart'),
    branchChart: document.getElementById('branchChart'),
    managerChart: document.getElementById('managerChart'),
    fBranch: document.getElementById('fBranch'),
    fManager: document.getElementById('fManager'),
    fAgent: document.getElementById('fAgent'),
    fStart: document.getElementById('fStart'),
    fEnd: document.getElementById('fEnd'),
    sortSelect: document.getElementById('sortSelect'),
    searchInput: document.getElementById('searchInput'),
    historyBody: document.getElementById('historyBody'),
    historyMeta: document.getElementById('historyMeta'),
    rangeMeta: document.getElementById('rangeMeta'),
    pagination: document.getElementById('pagination'),
    applyFilters: document.getElementById('applyFilters'),
    exportBtn: document.getElementById('exportBtn'),
    openRecordModal: document.getElementById('openRecordModal'),
    recManager: document.getElementById('recManager'),
    recAgent: document.getElementById('recAgent'),
    recAmount: document.getElementById('recAmount'),
    recDate: document.getElementById('recDate'),
    recNote: document.getElementById('recNote'),
    recBtn: document.getElementById('recBtn'),
    recordSpinner: document.getElementById('recordSpinner'),
    recordLabel: document.getElementById('recordLabel'),
    toast: document.getElementById('toast'),
    toastBody: document.getElementById('toastBody')
  };

  let dailyChart = null;
  let branchChart = null;
  let managerChart = null;
  let searchTimer = null;

  function showToast(msg){
    if(!els.toast || !els.toastBody) return;
    els.toastBody.textContent = msg;
    const t = bootstrap.Toast.getOrCreateInstance(els.toast);
    t.show();
  }

  function initDates(){
    const now = new Date();
    const start = new Date();
    start.setDate(now.getDate() - 30);
    const startStr = start.toISOString().slice(0,10);
    const endStr = now.toISOString().slice(0,10);
    els.fStart.value = startStr;
    els.fEnd.value = endStr;
    state.start = startStr;
    state.end = endStr;
  }

  function buildParams(extra = {}){
    const params = new URLSearchParams({
      branch: state.branch,
      manager_id: state.manager_id,
      agent_id: state.agent_id,
      start: state.start,
      end: state.end,
      q: state.q,
      page: state.page,
      limit: state.limit,
      sort: state.sort,
      ...extra
    });
    return params;
  }

  function renderPagination(pages, current){
    if(!els.pagination) return;
    const items = [];
    const add = (label, page, disabled=false, active=false) => {
      items.push(`<li class="page-item ${disabled ? 'disabled' : ''} ${active ? 'active' : ''}"><a class="page-link" href="#" data-page="${page}">${label}</a></li>`);
    };

    add('Prev', Math.max(1, current-1), current<=1, false);

    if(pages <= 7){
      for(let i=1;i<=pages;i++) add(i, i, false, i===current);
    }else{
      const show = new Set([1,2,3,current-1,current,current+1,pages-2,pages-1,pages]);
      let last = 0;
      for(let i=1;i<=pages;i++){
        if(!show.has(i)) continue;
        if(i - last > 1) items.push('<li class="page-item disabled"><span class="page-link">…</span></li>');
        add(i, i, false, i===current);
        last = i;
      }
    }

    add('Next', Math.min(pages, current+1), current>=pages, false);
    els.pagination.innerHTML = items.join('');
  }

  async function loadFilters(){
    const params = new URLSearchParams({ branch: els.fBranch.value, manager_id: els.fManager.value });
    const res = await fetch(`/executive/surplus-cash/api/filters?${params.toString()}`);
    const data = await res.json();
    if(!data || !data.ok) return;

    const branches = data.branches || [];
    const managers = data.managers || [];
    const agents = data.agents || [];

    els.fBranch.innerHTML = '<option value="">All branches</option>' + branches.map(b => `<option value="${b}">${b}</option>`).join('');
    els.fManager.innerHTML = '<option value="">All managers</option>' + managers.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
    els.fAgent.innerHTML = '<option value="">All agents</option>' + agents.map(a => `<option value="${a.id}">${a.name}</option>`).join('');

    els.recManager.innerHTML = '<option value="">Select manager</option>' + managers.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
    els.recAgent.innerHTML = '<option value="">Select agent</option>' + agents.map(a => `<option value="${a.id}">${a.name}</option>`).join('');

    if(window.jQuery && window.jQuery.fn.select2){
      window.jQuery('#fBranch').select2({ width:'100%', placeholder:'All branches', allowClear:true });
      window.jQuery('#fManager').select2({ width:'100%', placeholder:'All managers', allowClear:true });
      window.jQuery('#fAgent').select2({ width:'100%', placeholder:'All agents', allowClear:true });
      window.jQuery('#recManager').select2({ width:'100%', placeholder:'Select manager' });
      window.jQuery('#recAgent').select2({ width:'100%', placeholder:'Select agent' });
    }
  }

  async function loadMetrics(){
    const params = new URLSearchParams({
      branch: state.branch,
      manager_id: state.manager_id,
      agent_id: state.agent_id,
      start: state.start,
      end: state.end
    });
    const res = await fetch(`/executive/surplus-cash/api/metrics?${params.toString()}`);
    const data = await res.json();
    if(!data || !data.ok) return;

    els.kpiRange.textContent = fmtMoney(data.totals.range || 0);
    els.kpiToday.textContent = fmtMoney(data.totals.today || 0);
    els.kpiWeek.textContent = fmtMoney(data.totals.week || 0);
    els.kpiMonth.textContent = fmtMoney(data.totals.month || 0);
    els.kpiTotal.textContent = fmtMoney(data.totals.total || 0);

    const series = data.series || [];
    const sLabels = series.map(r => r.date);
    const sVals = series.map(r => r.total);
    if(dailyChart) dailyChart.destroy();
    dailyChart = new Chart(els.dailyChart, {
      type:'line',
      data:{ labels:sLabels, datasets:[{ label:'Surplus', data:sVals, borderColor:'#0d6efd', backgroundColor:'rgba(13,110,253,.12)', tension:.3, fill:true }] },
      options:{ responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
    });

    const bm = data.by_manager || [];
    if(managerChart) managerChart.destroy();
    managerChart = new Chart(els.managerChart, {
      type:'bar',
      data:{ labels: bm.map(r => r.manager_name), datasets:[{ label:'Managers', data: bm.map(r => r.total), backgroundColor:'#22c55e' }] },
      options:{ responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
    });

    const bb = data.by_branch || [];
    if(branchChart) branchChart.destroy();
    branchChart = new Chart(els.branchChart, {
      type:'bar',
      data:{ labels: bb.map(r => r.branch), datasets:[{ label:'Branches', data: bb.map(r => r.total), backgroundColor:'#f59e0b' }] },
      options:{ responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
    });

    els.exportBtn.href = `/executive/surplus-cash/export.csv?${params.toString()}`;
  }

  async function loadHistory(){
    const res = await fetch(`/executive/surplus-cash/api/history?${buildParams().toString()}`);
    const data = await res.json();
    if(!data || !data.ok){
      els.historyBody.innerHTML = '<tr><td colspan="7" class="text-muted">Unable to load.</td></tr>';
      return;
    }

    const total = Number(data.total || 0);
    const pages = Number(data.pages || 1);
    const page = Number(data.page || 1);
    const limit = Number(data.limit || state.limit);

    const startIdx = total === 0 ? 0 : (page - 1) * limit + 1;
    const endIdx = Math.min(page * limit, total);
    els.rangeMeta.textContent = `Showing ${startIdx}–${endIdx} of ${total}`;
    els.historyMeta.textContent = `${total} records`;

    if(!data.items || !data.items.length){
      els.historyBody.innerHTML = '<tr><td colspan="7" class="text-muted">No records found.</td></tr>';
    }else{
      els.historyBody.innerHTML = data.items.map(r => `
        <tr>
          <td>${r.date} ${r.time || ''}</td>
          <td>${r.manager?.name || ''}</td>
          <td>${r.agent?.name || ''}${r.agent?.phone ? ` <span class="text-muted">(${r.agent.phone})</span>` : ''}</td>
          <td>${r.manager?.branch || ''}</td>
          <td>${r.note || ''}</td>
          <td class="text-end">${fmtMoney(r.amount)}</td>
          <td>${r.recorded_by_role || ''}</td>
        </tr>
      `).join('');
    }

    renderPagination(pages, page);
  }

  async function recordSurplus(){
    const managerId = els.recManager.value;
    const agentId = els.recAgent.value;
    const amount = els.recAmount.value;
    if(!managerId || !agentId || !amount){
      showToast('Manager, agent and amount are required.');
      return;
    }

    els.recBtn.disabled = true;
    els.recordSpinner.classList.remove('d-none');
    els.recordLabel.textContent = 'Saving...';

    try{
      const payload = {
        manager_id: managerId,
        agent_id: agentId,
        amount,
        note: els.recNote.value,
        date: els.recDate.value
      };
      const res = await fetch('/executive/surplus-cash/record', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if(data && data.ok){
        bootstrap.Modal.getOrCreateInstance(document.getElementById('recordModal')).hide();
        els.recAmount.value = '';
        els.recNote.value = '';
        showToast('Surplus recorded successfully');
        await loadMetrics();
        await loadHistory();
      }else{
        showToast(data.message || 'Failed to record surplus.');
      }
    }catch(e){
      showToast('Failed to record surplus.');
    }finally{
      els.recBtn.disabled = false;
      els.recordSpinner.classList.add('d-none');
      els.recordLabel.textContent = 'Record Surplus';
    }
  }

  function bindEvents(){
    els.applyFilters.addEventListener('click', () => {
      state.branch = els.fBranch.value;
      state.manager_id = els.fManager.value;
      state.agent_id = els.fAgent.value;
      state.start = els.fStart.value;
      state.end = els.fEnd.value;
      state.sort = els.sortSelect.value;
      state.page = 1;
      loadMetrics();
      loadHistory();
    });

    [els.fBranch, els.fManager, els.fAgent].forEach(el => {
      el.addEventListener('change', () => {
        state.branch = els.fBranch.value;
        state.manager_id = els.fManager.value;
        state.agent_id = els.fAgent.value;
        state.page = 1;
        loadFilters();
        loadMetrics();
        loadHistory();
      });
    });

    els.sortSelect.addEventListener('change', () => {
      state.sort = els.sortSelect.value;
      state.page = 1;
      loadHistory();
    });

    els.searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.q = e.target.value.trim();
        state.page = 1;
        loadHistory();
      }, 250);
    });

    document.querySelectorAll('[data-preset]').forEach(btn => {
      btn.addEventListener('click', () => {
        const preset = btn.getAttribute('data-preset');
        const now = new Date();
        let start = new Date();
        if(preset === 'today') start = now;
        if(preset === 'week') start.setDate(now.getDate() - now.getDay());
        if(preset === 'month') start = new Date(now.getFullYear(), now.getMonth(), 1);
        els.fStart.value = start.toISOString().slice(0,10);
        els.fEnd.value = now.toISOString().slice(0,10);
        state.start = els.fStart.value;
        state.end = els.fEnd.value;
        state.page = 1;
        loadMetrics();
        loadHistory();
      });
    });

    els.pagination.addEventListener('click', (e) => {
      const link = e.target.closest('a[data-page]');
      if(!link) return;
      e.preventDefault();
      const p = Number(link.getAttribute('data-page'));
      if(!p || p === state.page) return;
      state.page = p;
      loadHistory();
    });

    els.openRecordModal.addEventListener('click', () => {
      bootstrap.Modal.getOrCreateInstance(document.getElementById('recordModal')).show();
    });

    els.recBtn.addEventListener('click', recordSurplus);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    initDates();
    await loadFilters();
    bindEvents();
    loadMetrics();
    loadHistory();
  });
})();

