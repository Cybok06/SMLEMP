(() => {
  if (window.staffLoansLoaded) { window.mountStaffLoans?.(); return; }
  window.staffLoansLoaded = true;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cash = v => 'GHS ' + Number(v || 0).toLocaleString('en-GH', {minimumFractionDigits:2,maximumFractionDigits:2});
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
  async function api(url, body) {
    const headers = {};
    if (document.querySelector('.staff-loans[data-management="true"]') && window.location.pathname.startsWith('/hr/')) headers['X-Manager-Suite'] = 'hr';
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, {credentials:'same-origin', headers, ...(body ? {method:'POST',body:JSON.stringify(body)} : {})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.error || (response.status === 403 ? 'You do not have access to these records.' : 'Unable to load or save. Please try again.'));
    return result;
  }
  function metrics(s) { return [['Original employee debt',s.principal],['Repaid / deducted',s.paid],['Remaining balance',s.remaining],['Overdue balance',s.overdue]].map(([k,v])=>`<div class="sl-metric">${k}<strong>${cash(v)}</strong></div>`).join(''); }
  function record(d, management) {
    const e = d.employee;
    return `<article class="sl-record"><div class="sl-record-head"><div><h3>${esc(e.name)} &middot; ${esc(d.kind)}</h3><small>${esc(e.role)}${e.branch ? ' &middot; '+esc(e.branch) : ''}${e.staff_code ? ' &middot; '+esc(e.staff_code) : ''}${d.manual ? ' &middot; Manual staff' : ''}</small></div><span class="sl-status ${esc(d.status)}">${esc(d.status)}</span></div>
    <div>${window.hrAttributionBadge?.(d) || ''}</div><p>${esc(d.reason)}</p><div class="sl-figures">${[['Total cost',d.total],['Company contribution',d.company],['Original employee debt',d.principal],['Repaid / deducted',d.paid],['Remaining',d.remaining]].map(([k,v])=>`<div><span>${k}</span><strong>${cash(v)}</strong></div>`).join('')}</div>
    <p>Issued ${esc(d.issued_date)} &middot; Payment due ${esc(d.due_date)}<br><small>Recorded by ${esc(d.created_by.name)} (${esc(d.created_by.role)})</small></p>
    <details><summary>Repayment and deduction history (${d.payments.length})</summary>${d.payments.length ? `<div class="sl-table-wrap"><table><thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Reference / notes</th><th>Recorded by</th></tr></thead><tbody>${d.payments.map(p=>`<tr><td>${esc(p.date)}</td><td>${esc(p.method)}</td><td>${cash(p.amount)}</td><td>${esc(p.reference)}<br>${esc(p.notes)}</td><td>${esc(p.recorded_by.name)}</td></tr>`).join('')}</tbody></table></div>` : '<p>No repayments or deductions recorded.</p>'}</details>
    ${management && d.remaining > 0 ? `<details><summary>Record repayment / deduction</summary><form class="sl-payment-form" data-loan-id="${esc(d.id)}"><label>Amount (GHS)<input name="amount" type="number" min="0.01" max="${d.remaining}" step="0.01" required></label><label>Type<select aria-label="Type" name="method"><option>Repayment</option><option>Payroll deduction</option></select></label><label>Date<input type="date" name="date" value="${today()}" min="${esc(d.issued_date)}" max="${today()}" required></label><label>Receipt / payroll reference<input name="reference" maxlength="200" required></label><label>Notes<textarea name="notes" maxlength="1000"></textarea></label><button type="submit">Record payment</button></form></details>` : ''}</article>`;
  }
  async function mount(root) {
    const management = root.dataset.management === 'true', fixed = root.dataset.employeeId || '';
    const message = (s, error=false) => { const n=root.querySelector('.sl-message'); n.textContent=s; n.classList.toggle('sl-error',error); };
    const form = root.querySelector('.sl-loan-form'), filter=root.querySelector('.sl-filter');
    let people=[], loading=0;
    async function refresh() {
      const seq=++loading;
      try {
        const selected = fixed || filter?.value || '', person=people.find(p=>p.id===selected);
        const result = await api(management ? '/api/staff-loans'+(selected ? '?employee_id='+encodeURIComponent(selected)+'&manual='+Boolean(person?.manual) : '') : '/api/staff-loans/mine');
        if (seq !== loading || !root.isConnected) return;
        root.querySelector('.sl-summary').innerHTML=metrics(result.summary);
        root.querySelector('.sl-records').innerHTML=result.loans.map(d=>record(d,management)).join('') || '<div class="sl-record">No staff loans recorded.</div>';
      } catch(e) { message(e.message,true); root.querySelector('.sl-records').textContent='Loan records could not be loaded.'; }
    }
    root.querySelector('.sl-refresh').onclick=()=>{message('');refresh();};
    if (form) {
      form.elements.issued_date.value=today();form.elements.issued_date.max=today();form.elements.due_date.value=today();
      try {
        people=(await api('/api/staff-loans/employees')).employees;
        const options=people.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.role)})${p.staff_code ? ' - '+esc(p.staff_code) : ''}</option>`).join('');
        form.elements.employee_id.innerHTML='<option value="">Select staff...</option>'+options+'<option value="manual-new">Enter manual staff details</option>';
        if(fixed) { const found=people.find(p=>p.id===fixed); if(!found) form.elements.employee_id.insertAdjacentHTML('beforeend',`<option value="${esc(fixed)}">Profile employee (archived)</option>`); form.elements.employee_id.value=fixed;form.elements.employee_id.disabled=true; }
        if(filter) { filter.innerHTML='<option value="">All staff</option>'+options;filter.onchange=refresh; }
      } catch(e) { message(e.message,true); }
      form.elements.employee_id.onchange=()=>{const manual=form.elements.employee_id.value==='manual-new';form.querySelector('.sl-manual').hidden=!manual;['staff_name','staff_phone','staff_code'].forEach(k=>form.elements[k].required=manual);};
      const share=()=>{const value=Number(form.elements.total.value)-Number(form.elements.company.value);form.querySelector('.sl-share').textContent='Employee share: '+cash(value);form.elements.company.setCustomValidity(value<0 ? 'Company contribution cannot exceed total cost.' : '');};
      form.elements.total.oninput=share;form.elements.company.oninput=share;
      form.onsubmit=async event=>{
        event.preventDefault();const values=Object.fromEntries(new FormData(form));const selected=fixed||values.employee_id, manual=selected==='manual-new'||Boolean(people.find(p=>p.id===selected)?.manual);
        const payload={...values,employee_id:selected==='manual-new' ? undefined : selected,manual,staff:{name:values.staff_name,phone:values.staff_phone,staff_code:values.staff_code,branch:values.staff_branch},request_id:form.dataset.requestId||(form.dataset.requestId=crypto.randomUUID())};
        const button=form.querySelector('button');button.disabled=true;
        try { const saved=await api('/api/staff-loans',payload); delete form.dataset.requestId;message('Staff debt recorded.');
        if(saved.loan.manual && !people.some(p=>p.id===saved.loan.employee_id)) {
          const person={id:saved.loan.employee_id,name:saved.loan.employee.name,role:'Manual staff',manual:true,staff_code:saved.loan.employee.staff_code};people.push(person);
          const option=`<option value="${esc(person.id)}">${esc(person.name)} (Manual staff) - ${esc(person.staff_code)}</option>`;
          form.elements.employee_id.insertAdjacentHTML('beforeend',option);filter?.insertAdjacentHTML('beforeend',option);
        }
        form.reset();form.elements.issued_date.value=today();form.elements.due_date.value=today();form.elements.company.value='0';if(fixed)form.elements.employee_id.value=fixed;form.elements.employee_id.onchange();share();await refresh(); } catch(e){message(e.message,true);} finally {button.disabled=false;}
      };
      form.addEventListener('input',()=>delete form.dataset.requestId);
    }
    root.addEventListener('submit',async event=>{
      const payment=event.target;if(!payment.matches('.sl-payment-form'))return;event.preventDefault();const button=payment.querySelector('button');button.disabled=true;
      try { await api('/api/staff-loans/'+payment.dataset.loanId+'/payments',{...Object.fromEntries(new FormData(payment)),request_id:payment.dataset.requestId||(payment.dataset.requestId=crypto.randomUUID())});message('Payment recorded.');await refresh(); } catch(e){message(e.message,true);button.disabled=false;}
    });
    root.addEventListener('input',event=>{const payment=event.target.closest('.sl-payment-form');if(payment)delete payment.dataset.requestId;});
    await refresh();
  }
  window.mountStaffLoans=()=>{
    document.querySelectorAll('.staff-loans:not([data-mounted])').forEach(root=>{root.dataset.mounted='true';mount(root);});
    document.querySelectorAll('.sl-dashboard:not([data-mounted])').forEach(root=>{root.dataset.mounted='true';api('/api/staff-loans/mine').then(({summary:s})=>{root.querySelector('.sl-dashboard-values').innerHTML=`<div>Original debt<strong>${cash(s.principal)}</strong></div><div>Repaid / deducted<strong>${cash(s.paid)}</strong></div><div>Remaining<strong>${cash(s.remaining)}</strong></div><div>Overdue<strong>${cash(s.overdue)}</strong></div><div>Next payment due<strong>${esc(s.next_due || 'No outstanding payments')}</strong></div>`;}).catch(e=>{root.querySelector('.sl-dashboard-values').textContent=e.message;});});
  };
  new MutationObserver(window.mountStaffLoans).observe(document.documentElement,{childList:true,subtree:true});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',window.mountStaffLoans);else window.mountStaffLoans();
})();
