(() => {
  'use strict';
  const cfg = JSON.parse(document.getElementById('analysisConfig').textContent);
  const $ = id => document.getElementById(id);
  const charts = {};
  const fmt = new Intl.NumberFormat('en-GH');
  const money = new Intl.NumberFormat('en-GH', {style:'currency', currency:'GHS'});
  const dateTime = new Intl.DateTimeFormat('en-GH', {timeZone:'Africa/Accra', dateStyle:'medium', timeStyle:'short'});
  const day = new Intl.DateTimeFormat('sv-SE', {timeZone:'Africa/Accra', year:'numeric', month:'2-digit', day:'2-digit'});
  const today = day.format(new Date());
  const past = new Date(Date.now() - 29 * 86400000);
  $('start').value = day.format(past);
  $('end').value = today;
  $('start').max = today;
  $('end').max = today;
  let loadSerial = 0;

  function duration(seconds) {
    if (seconds == null) return '—';
    if (seconds < 60) return `${Math.round(seconds)} sec`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
    return `${(seconds / 3600).toFixed(1)} hr`;
  }
  function show(message, kind='info') {
    $('message').className = `alert alert-${kind}`;
    $('message').textContent = message;
    $('message').hidden = !message;
  }
  async function json(url) {
    const response = await fetch(url, {headers:{Accept:'application/json'}});
    const type = response.headers.get('content-type') || '';
    if (!type.includes('application/json')) throw new Error('The server returned a page instead of analysis data. Refresh and sign in again if needed.');
    const body = await response.json();
    if (!response.ok || body.ok === false) throw new Error(body.message || body.error || `Request failed (${response.status}).`);
    return body;
  }
  function chart(id, type, labels, datasets, options={}) {
    if (charts[id]) charts[id].destroy();
    if (!window.Chart) return;
    charts[id] = new Chart($(id), {type, data:{labels,datasets}, options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}}, ...options}});
  }
  function cell(row, value, className) {
    const td = document.createElement('td');
    td.textContent = value == null ? '—' : String(value);
    if (className) td.className = className;
    row.appendChild(td);
  }
  function render(data) {
    const s = data.summary;
    for (const key of ['required','printed_payments','receipts_sent','short_sent','full_sent','missed','waiting','uncertain']) $(key).textContent = fmt.format(s[key]);
    $('coverage_percent').textContent = `${s.coverage_percent}%`;
    $('median_interval_seconds').textContent = duration(s.median_interval_seconds);
    $('late_prints').textContent = `${fmt.format(s.late_prints)} printed after 15 minutes`;
    chart('dailyChart','bar',data.daily.map(x=>x.date),[
      {label:'Required',data:data.daily.map(x=>x.required),backgroundColor:'#7489b7'},
      {label:'Printed',data:data.daily.map(x=>x.printed),backgroundColor:'#23a075'},
      {label:'Missed',data:data.daily.map(x=>x.missed),backgroundColor:'#e56a68'}]);
    const states = data.status_breakdown;
    chart('statusChart','doughnut',['Printed','Waiting','Missed','Uncertain'],[
      {data:[states.printed,states.waiting,states.missed,states.uncertain],backgroundColor:['#23a075','#6d9ee9','#e56a68','#eab75b']}]);
    chart('branchChart','bar',data.branches.map(x=>x.branch),[
      {label:'Required',data:data.branches.map(x=>x.required),backgroundColor:'#7489b7'},
      {label:'Printed',data:data.branches.map(x=>x.printed),backgroundColor:'#23a075'}],
      {indexAxis:'y'});
    const buckets = data.interval_buckets;
    chart('intervalChart','bar',Object.keys(buckets),[{label:'Payments',data:Object.values(buckets),backgroundColor:'#557dd2'}],
      {plugins:{legend:{display:false}}});
    const tbody = $('paymentRows'); tbody.replaceChildren();
    for (const p of data.payments) {
      const tr = document.createElement('tr');
      cell(tr,p.payment_id || p.document_id);
      cell(tr,dateTime.format(new Date(p.recorded_at)));
      cell(tr,p.agent); cell(tr,p.branch); cell(tr,money.format(p.amount));
      cell(tr,p.status,`status ${p.status}`);
      cell(tr,p.printed_at ? dateTime.format(new Date(p.printed_at)) : '—');
      cell(tr,duration(p.interval_seconds)); cell(tr,p.short_status); cell(tr,p.full_status);
      tbody.appendChild(tr);
    }
    if (!data.payments.length) {const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=10; td.className='text-center text-secondary py-4'; td.textContent='No eligible payments match these filters.'; tr.appendChild(td); tbody.appendChild(tr);}
    show(data.summary.required ? '' : 'No payments requiring printed receipts were recorded in this selection.');
  }
  async function load() {
    const serial = ++loadSerial;
    show('Loading analysis…');
    try {
      const params = new URLSearchParams(new FormData($('filters')));
      const data = await json(`${cfg.dataUrl}?${params}`);
      if (serial === loadSerial) render(data);
    } catch (error) { if (serial === loadSerial) show(error.message, 'danger'); }
  }
  $('filters').addEventListener('submit', event => {event.preventDefault(); load();});
  async function branches() {
    try {
      const result = await json(cfg.branchesUrl);
      for (const branch of result.branches) {
        const option = document.createElement('option'); option.value=branch.name; option.textContent=branch.name;
        $('branch').appendChild(option);
      }
    } catch (_) { /* Date and status filters remain usable. */ }
  }
  let searchTimer;
  $('agentSearch').addEventListener('input', () => {
    $('agent').value='';
    clearTimeout(searchTimer);
    const term=$('agentSearch').value.trim();
    $('agentMatches').replaceChildren();
    if (term.length < 2) return;
    searchTimer=setTimeout(async () => {
      try {
        const params=new URLSearchParams({q:term});
        if ($('branch').value) params.set('branch',$('branch').value);
        const result=await json(`${cfg.agentsUrl}?${params}`);
        if ($('agentSearch').value.trim() !== term) return;
        const list=$('agentMatches'); list.replaceChildren();
        for (const agent of result.agents) {
          const button=document.createElement('button'); button.type='button'; button.className='list-group-item list-group-item-action';
          button.textContent=`${agent.name} · ${agent.branch || 'Unassigned'}`;
          button.addEventListener('click',()=>{$('agent').value=agent.id; $('agentSearch').value=agent.name; list.replaceChildren(); load();});
          list.appendChild(button);
        }
      } catch (_) { $('agentMatches').replaceChildren(); }
    },250);
  });
  $('branch').addEventListener('change',()=>{$('agent').value='';$('agentSearch').value='';$('agentMatches').replaceChildren();});
  branches(); load();
})();
