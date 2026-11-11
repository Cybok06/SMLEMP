(() => {
  const fmtMoney = (n) => 'GHS ' + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const state = {
    page: 1,
    limit: 25,
    sort: 'newest',
    q: '',
    agent_id: '',
    start: '',
    end: ''
  };

  const els = {
    kpiToday: document.getElementById('kpiToday'),
    kpiWeek: document.getElementById('kpiWeek'),
    kpiMonth: document.getElementById('kpiMonth'),
    kpiTotal: document.getElementById('kpiTotal'),
    dailyChart: document.getElementById('dailyChart'),
    agentChart: document.getElementById('agentChart'),
    filterStart: document.getElementById('filterStart'),
    filterEnd: document.getElementById('filterEnd'),
    filterAgent: document.getElementById('filterAgent'),
    sortSelect: document.getElementById('sortSelect'),
    searchInput: document.getElementById('searchInput'),
    historyBody: document.getElementById('historyBody'),
    historyMeta: document.getElementById('historyMeta'),
    rangeMeta: document.getElementById('rangeMeta'),
    pagination: document.getElementById('pagination'),
    applyFilters: document.getElementById('applyFilters'),
    recordBtn: document.getElementById('recordBtn'),
    recordSpinner: document.getElementById('recordSpinner'),
    recordLabel: document.getElementById('recordLabel'),
    agentSelect: document.getElementById('agentSelect'),
    amountInput: document.getElementById('amountInput'),
    noteInput: document.getElementById('noteInput'),
    dateInput: document.getElementById('dateInput'),
    openRecordModal: document.getElementById('openRecordModal'),
    toast: document.getElementById('toast'),
    toastBody: document.getElementById('toastBody')
  };

  let dailyChart = null;
  let agentChart = null;
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
    els.filterStart.value = startStr;
    els.filterEnd.value = endStr;
    state.start = startStr;
    state.end = endStr;
  }

  function buildParams(extra = {}){
    const params = new URLSearchParams({
      start: state.start,
      end: state.end,
      agent_id: state.agent_id,
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

  async function loadMetrics(){
    const params = new URLSearchParams({ start: state.start, end: state.end, agent_id: state.agent_id });
    const res = await fetch(`/manager/surplus-cash/api/metrics?${params.toString()}`);
    const data = await res.json();
    if(!data || !data.ok) return;

    els.kpiToday.textContent = fmtMoney(data.totals.today || 0);
    els.kpiWeek.textContent = fmtMoney(data.totals.week || 0);
    els.kpiMonth.textContent = fmtMoney(data.totals.month || 0);
    els.kpiTotal.textContent = fmtMoney(data.totals.total || 0);

    const labels = (data.series || []).map(r => r.date);
    const values = (data.series || []).map(r => r.total);
    if(dailyChart) dailyChart.destroy();
    dailyChart = new Chart(els.dailyChart, {
      type: 'line',
      data: { labels, datasets: [{ label:'Surplus', data: values, borderColor:'#0d6efd', backgroundColor:'rgba(13,110,253,.12)', tension:.3, fill:true }] },
      options: { responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
    });

    const alabels = (data.by_agent || []).map(r => r.agent_name);
    const avals = (data.by_agent || []).map(r => r.total);
    if(agentChart) agentChart.destroy();
    agentChart = new Chart(els.agentChart, {
      type: 'bar',
      data: { labels: alabels, datasets: [{ label:'By Agent', data: avals, backgroundColor:'#f59e0b' }] },
      options: { responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
    });
  }

  async function loadHistory(){
    const res = await fetch(`/manager/surplus-cash/api/history?${buildParams().toString()}`);
    const data = await res.json();
    if(!data || !data.ok){
      els.historyBody.innerHTML = '<tr><td colspan="5" class="text-muted">Unable to load.</td></tr>';
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
      els.historyBody.innerHTML = '<tr><td colspan="5" class="text-muted">No records found.</td></tr>';
    }else{
      els.historyBody.innerHTML = data.items.map(r => `
        <tr>
          <td>${r.date} ${r.time || ''}</td>
          <td>${r.agent?.name || ''}${r.agent?.phone ? ` <span class="text-muted">(${r.agent.phone})</span>` : ''}</td>
          <td>${r.note || ''}</td>
          <td class="text-end">${fmtMoney(r.amount)}</td>
          <td>${r.recorded_by_role || ''}</td>
        </tr>
      `).join('');
    }

    renderPagination(pages, page);
  }

  async function recordSurplus(){
    const agentId = els.agentSelect.value;
    const amount = els.amountInput.value;
    if(!agentId || !amount){
      showToast('Agent and amount are required.');
      return;
    }

    els.recordBtn.disabled = true;
    els.recordSpinner.classList.remove('d-none');
    els.recordLabel.textContent = 'Saving...';

    try{
      const payload = {
        agent_id: agentId,
        amount,
        note: els.noteInput.value,
        date: els.dateInput.value
      };
      const res = await fetch('/manager/surplus-cash/record', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if(data && data.ok){
        bootstrap.Modal.getOrCreateInstance(document.getElementById('recordModal')).hide();
        els.amountInput.value = '';
        els.noteInput.value = '';
        showToast('Surplus recorded successfully');
        await loadMetrics();
        await loadHistory();
      }else{
        showToast(data.message || 'Failed to record surplus.');
      }
    }catch(e){
      showToast('Failed to record surplus.');
    }finally{
      els.recordBtn.disabled = false;
      els.recordSpinner.classList.add('d-none');
      els.recordLabel.textContent = 'Record Surplus';
    }
  }

  function bindEvents(){
    els.applyFilters.addEventListener('click', () => {
      state.start = els.filterStart.value;
      state.end = els.filterEnd.value;
      state.agent_id = els.filterAgent.value;
      state.sort = els.sortSelect.value;
      state.page = 1;
      loadMetrics();
      loadHistory();
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
        els.filterStart.value = start.toISOString().slice(0,10);
        els.filterEnd.value = now.toISOString().slice(0,10);
        state.start = els.filterStart.value;
        state.end = els.filterEnd.value;
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

    els.recordBtn.addEventListener('click', recordSurplus);
  }

  document.addEventListener('DOMContentLoaded', () => {
    if(window.jQuery && window.jQuery.fn.select2){
      window.jQuery('#agentSelect').select2({ width:'100%', placeholder:'Select agent' });
      window.jQuery('#filterAgent').select2({ width:'100%', placeholder:'All agents', allowClear:true });
    }
    initDates();
    bindEvents();
    loadMetrics();
    loadHistory();
  });
})();

