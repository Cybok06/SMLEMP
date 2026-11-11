(() => {
  if (window.workTimeLoaded) { window.mountWorkTime?.(); return; }
  window.workTimeLoaded = true;
  const visible = root => !root.closest('.modal') || root.closest('.modal').classList.contains('show');
  const escape = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const today = () => new Date().toLocaleDateString('en-CA',{timeZone:'Africa/Accra'});
  const dateLabel = value => value ? new Date(value).toLocaleString('en-GB',{timeZone:'Africa/Accra'}) : 'Not logged';
  const DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  async function api(url,body) {
    const res=await fetch(url,{credentials:'same-origin',...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
    const data=await res.json().catch(()=>({}));
    if(!res.ok || !data.ok)throw Error(data.message || 'Unable to load or save this record.');
    return data;
  }
  window.captureWorkLocation=()=>new Promise(resolve=>{
    if(!navigator.geolocation){resolve({location_status:'unavailable'});return;}
    let finished=false;
    const done=value=>{if(!finished){finished=true;clearTimeout(timer);resolve(value);}};
    const timer=setTimeout(()=>done({location_status:'timeout'}),12000);
    navigator.geolocation.getCurrentPosition(position=>done({location_status:'granted',location:{latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy:position.coords.accuracy}}),error=>done({location_status:error.code===1 ? 'denied' : error.code===3 ? 'timeout' : 'unavailable'}),{enableHighAccuracy:true,maximumAge:0,timeout:10000});
  });
  window.workLocationLabel=geo=>geo?.status==='granted' ? `<a target="_blank" rel="noopener noreferrer" href="https://www.google.com/maps/search/?api=1&amp;query=${encodeURIComponent(geo.latitude+','+geo.longitude)}">View current location on Google Maps</a><br><small>Accuracy: ${escape(Math.round(geo.accuracy))} m</small>` : `Location ${escape(geo?.status || 'not captured')}`;
  function metricCards(summary) {
    return [['Workdays reported','reported_days'],['Late arrivals','late_days'],['Absence occasions','approved_absence_occasions'],['Approved absence days','approved_absence_days'],['Missing clock-ins','missing_clock_in_days'],['Pending requests','pending_leave_requests']].map(([label,key])=>`<div class="wt-metric">${label}<strong>${escape(summary[key] || 0)}</strong></div>`).join('');
  }
  function note(root,text,error=false){const element=root.querySelector('.wt-message');element.textContent=text;element.classList.toggle('wt-error',error);}
  function requestCard(leave,review=false){return `<article class="wt-request" data-employee="${escape(leave.employee_id)}" data-leave="${escape(leave.id)}"><strong>${review ? escape(leave.employee_name)+' &middot; ' : ''}${escape(leave.leave_type)} &middot; ${escape(leave.status)}</strong><div>${escape(leave.start_date)} &middot; ${escape(leave.days)} calendar day(s)</div><p>${escape(leave.reason)}</p>${leave.approved_by ? `<small>Approved by ${escape(leave.approved_by)}</small>` : ''}${leave.rejected_reason ? `<small>Review: ${escape(leave.rejected_reason)}</small>` : ''}${review ? '<label>Review notes / rejection reason<textarea class="wt-review-notes" maxlength="1000"></textarea></label><div class="wt-request-actions"><button type="button" data-decision="approve">Approve</button><button type="button" data-decision="reject">Reject</button></div>' : ''}</article>`;}
  async function refreshPersonal(root) {
    try {const data=await api('/attendance/api/summary');root.querySelector('.wt-metrics').innerHTML=metricCards(data.summary);root.querySelector('.wt-period').textContent=`${data.summary.from} to ${data.summary.to} · ${data.settings.start_time}–${data.settings.end_time} (Africa/Accra)`;}
    catch(error){note(root,error.message,true);}
  }
  function mountLeave(root){
    const form=root.querySelector('form');form.elements.start_date.value=today();form.elements.start_date.min=today();
    const refresh=async()=>{try{const data=await api('/attendance/api/summary');root.querySelector('.wt-my-leaves').innerHTML=data.leaves.slice().reverse().map(l=>requestCard(l)).join('') || '<p>No leave requests yet.</p>';}catch(e){note(root,e.message,true);}};
    form.addEventListener('input',()=>delete form.dataset.requestId);
    form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await api('/attendance/api/leave',{...Object.fromEntries(new FormData(form)),request_id:form.dataset.requestId||(form.dataset.requestId=crypto.randomUUID())});delete form.dataset.requestId;note(root,'Request sent to HR. Awaiting approval.');form.reset();form.elements.start_date.value=today();await refresh();window.dispatchEvent(new Event('attendance-updated'));}catch(e){note(root,e.message,true);}finally{button.disabled=false;}};
    window.addEventListener('focus',()=>{if(root.isConnected)refresh();});refresh();
  }
  async function mountManagement(root){
    const report=root.querySelector('.wt-report-form'),config=root.querySelector('.wt-settings-form');let data=null;
    report.elements.from.value=today().slice(0,8)+'01';report.elements.to.value=today();report.elements.to.max=today();
    function renderTables(){
      if(!data)return;
      const search=root.querySelector('.wt-search').value.toLowerCase();
      const staff=data.employees.filter(e=>(e.name+' '+e.role+' '+e.branch).toLowerCase().includes(search));
      root.querySelector('.wt-staff-table').innerHTML=`<table><thead><tr><th>Staff / role / branch</th><th>Reported</th><th>Late</th><th>Approved absence</th><th>Missing clock-ins</th><th>Pending leave</th></tr></thead><tbody>${staff.map(e=>`<tr><td>${escape(e.name)}<br><small>${escape(e.role)} &middot; ${escape(e.branch)}</small></td><td>${e.reported_days}</td><td class="${e.late_days ? 'wt-late' : ''}">${e.late_days}</td><td>${e.approved_absence_days} day(s)<br><small>${e.approved_absence_occasions} occasion(s)</small></td><td>${e.missing_clock_in_days}${e.missing_dates.length ? `<details><summary>Dates</summary>${e.missing_dates.map(escape).join('<br>')}</details>` : ''}</td><td>${e.pending_leave_requests}</td></tr>`).join('') || '<tr><td colspan="6">No staff match this search.</td></tr>'}</tbody></table>`;
      const lateOnly=root.querySelector('.wt-late-only').checked;
      const records=data.records.filter(r=>(!lateOnly || r.schedule.is_late) && (r.name+' '+r.role+' '+r.branch).toLowerCase().includes(search));
      root.querySelector('.wt-report-table').innerHTML=`<table><thead><tr><th>Date / staff</th><th>Clock in</th><th>Clock out</th><th>Reporting status</th><th>Start location</th><th>End location</th></tr></thead><tbody>${records.map(r=>`<tr><td>${escape(r.attendance_date)}<br>${escape(r.name)}<br><small>${escape(r.role)}</small></td><td>${escape(dateLabel(r.report_to_work_at))}</td><td>${escape(dateLabel(r.leave_office_at))}</td><td class="${r.schedule.is_late ? 'wt-late' : ''}">${r.schedule.is_late ? 'Late by '+r.schedule.late_minutes+' min' : r.schedule.scheduled_day===false ? 'Non-working day' : r.schedule.expected_start_at ? 'On time' : 'Historical schedule unavailable'}${r.schedule.start_time ? '<br><small>Expected '+escape(r.schedule.start_time)+'</small>' : ''}</td><td>${window.workLocationLabel(r.events.find(e=>e.action==='report_to_work')?.location)}</td><td>${window.workLocationLabel(r.events.find(e=>e.action==='leave_office')?.location)}</td></tr>`).join('') || '<tr><td colspan="6">No attendance records match.</td></tr>'}</tbody></table>`;
    }
    async function refresh(){
      try{
        const values=Object.fromEntries(new FormData(report));data=await api('/attendance/api/analytics?'+new URLSearchParams(values));
        root.querySelector('.wt-metrics').innerHTML=metricCards(data.totals);root.querySelector('.wt-schedule').textContent=`Work hours ${data.settings.start_time}–${data.settings.end_time}; ${data.settings.grace_minutes} minute grace; ${data.settings.workdays.map(d=>DAYS[d]).join(', ')}; Africa/Accra. Missing-clock-in tracking: ${data.settings.tracking_from || 'not yet enabled—save company work hours to begin'}.`;
        root.querySelector('.wt-pending').innerHTML=data.leaves.map(l=>requestCard(l,true)).join('') || '<p>No requests awaiting review.</p>';
        if(config){config.elements.start_time.value=data.settings.start_time;config.elements.end_time.value=data.settings.end_time;config.elements.grace_minutes.value=data.settings.grace_minutes;config.querySelectorAll('[name=workdays]').forEach(c=>c.checked=data.settings.workdays.includes(Number(c.value)));}
        renderTables();
      }catch(e){note(root,e.message,true);}
    }
    report.onsubmit=event=>{event.preventDefault();note(root,'');refresh();};root.querySelector('.wt-search').oninput=renderTables;root.querySelector('.wt-late-only').onchange=renderTables;
    if(config)config.onsubmit=async event=>{event.preventDefault();const button=config.querySelector('button');button.disabled=true;try{await api('/attendance/api/settings',{start_time:config.elements.start_time.value,end_time:config.elements.end_time.value,grace_minutes:Number(config.elements.grace_minutes.value),workdays:Array.from(config.querySelectorAll('[name=workdays]:checked')).map(c=>Number(c.value))});note(root,'Company work hours saved.');await refresh();}catch(e){note(root,e.message,true);}finally{button.disabled=false;}};
    root.addEventListener('click',async event=>{
      const button=event.target.closest('[data-decision]');if(!button)return;
      const card=button.closest('.wt-request'),reason=card.querySelector('textarea').value.trim();
      if(button.dataset.decision==='reject'&&!reason){note(root,'Enter a rejection reason.',true);return;}
      const buttons=card.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
      try{await api(`/hr/leave/request/${encodeURIComponent(card.dataset.employee)}/${encodeURIComponent(card.dataset.leave)}/${button.dataset.decision}`,{rejected_reason:reason});note(root,'Leave request '+(button.dataset.decision==='approve' ? 'approved.' : 'rejected.'));await refresh();}catch(e){note(root,e.message,true);buttons.forEach(b=>b.disabled=false);}
    });
    await refresh();
  }
  window.mountWorkTime=()=>{
    document.querySelectorAll('.wt-personal:not([data-mounted])').forEach(root=>{if(!visible(root))return;root.dataset.mounted='true';refreshPersonal(root);});
    document.querySelectorAll('.wt-team:not([data-mounted])').forEach(root=>{if(!visible(root))return;root.dataset.mounted='true';api('/attendance/api/analytics?summary_only=1').then(data=>{root.querySelector('.wt-metrics').innerHTML=metricCards(data.totals);}).catch(error=>note(root,error.message,true));});
    document.querySelectorAll('.wt-leave:not([data-mounted])').forEach(root=>{if(!visible(root))return;root.dataset.mounted='true';mountLeave(root);});
    document.querySelectorAll('.wt-management:not([data-mounted])').forEach(root=>{if(!visible(root))return;root.dataset.mounted='true';mountManagement(root);});
  };
  window.addEventListener('attendance-updated',()=>document.querySelectorAll('.wt-personal').forEach(root=>{if(visible(root))refreshPersonal(root);}));
  window.addEventListener('focus',()=>document.querySelectorAll('.wt-personal').forEach(root=>{if(visible(root))refreshPersonal(root);}));
  document.addEventListener('shown.bs.modal', event=>{event.target.querySelectorAll('.wt-personal,.wt-team').forEach(root=>delete root.dataset.mounted);window.mountWorkTime();});
  new MutationObserver(window.mountWorkTime).observe(document.documentElement,{childList:true,subtree:true});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',window.mountWorkTime);else window.mountWorkTime();
})();
