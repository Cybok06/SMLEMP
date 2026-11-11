(() => {
  const el = id => document.getElementById(`rp-${id}`);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let page = 1, selected = null, saving = false;
  async function api(path, options = {}) { const response = await fetch(`/api/agent/repeat-purchases${path}`, {credentials:'same-origin', ...options}); const data = await response.json(); if(!response.ok || !data.ok) throw new Error(data.message || 'Unable to load opportunities.'); return data; }
  async function load() {
    el('error').textContent = '';
    try { const data = await api(`?status=${encodeURIComponent(el('filter-status').value)}&q=${encodeURIComponent(el('search').value)}&page=${page}`); page = data.pagination.page;
      el('counts').textContent = Object.entries(data.counts).map(([s,n]) => `${s}: ${n}`).join(' · ');
      el('rows').innerHTML = data.opportunities.length ? `<table class="table align-middle"><thead><tr><th>Customer</th><th>Requested product</th><th>Status</th><th>Follow-up</th><th></th></tr></thead><tbody>${data.opportunities.map(r => `<tr><td>${escape(r.customer_name)}<div class="muted">${escape(r.customer_phone)}</div></td><td>${escape(r.requested_product)}</td><td>${escape(r.status)}</td><td>${escape(r.next_followup_date || 'Finished')}</td><td><button class="btn btn-sm btn-outline-primary" data-rp-id="${escape(r.id)}">View opportunity</button></td></tr>`).join('')}</tbody></table>` : '<p class="muted py-3">No opportunities match these filters.</p>';
      el('page').textContent = `Page ${page} of ${data.pagination.total_pages}`; el('prev').disabled = page <= 1; el('next').disabled = page >= data.pagination.total_pages;
    } catch(error) {el('error').textContent = error.message;}
  }
  function fields() { const status = el('status').value, open = !['Purchased','Closed'].includes(status); el('due-wrap').hidden = !open; el('due').required = open; el('purchase-wrap').hidden = status !== 'Purchased'; el('purchase').required = status === 'Purchased'; el('reason-wrap').hidden = status !== 'Closed'; el('reason').required = status === 'Closed'; }
  async function open(id) {
    try {const [data, options] = await Promise.all([api(`/${id}`), api(`/${id}/purchases`)]); selected=data.opportunity;
      el('title').textContent = selected.requested_product; el('summary').textContent = `${selected.customer_name} · ${selected.customer_phone} · ${selected.status}`; el('current-notes').textContent=selected.notes;
      el('outcome').textContent = selected.purchase ? `Purchased: ${selected.purchase.product_name} · ${selected.purchase.purchase_date}` : selected.closure_reason ? `Closure reason: ${selected.closure_reason}` : '';
      el('modal-error').textContent=''; el('status').value=selected.status; el('due').value=selected.next_followup_date; el('due').min=new Date().toISOString().slice(0,10); el('notes').value='';el('reason').value='';
      el('purchase').innerHTML = '<option value="">Select a new purchase</option>' + options.purchases.map(p=>`<option value="${p.index}">${escape(p.product_name)} · ${escape(p.purchase_date)}</option>`).join('');
      el('update').hidden=['Purchased','Closed'].includes(selected.status);fields();
      el('history').innerHTML=[...selected.history].reverse().map(h=>`<div class="border-top py-2"><strong>${escape(h.status)} · ${escape(h.actor_name)}</strong><div class="muted">${escape(new Date(h.at).toLocaleString())}</div><p style="white-space:pre-wrap">${escape(h.notes)}</p>${h.closure_reason?`<p>Closure: ${escape(h.closure_reason)}</p>`:''}</div>`).join('');
      if(!el('dialog').open) el('dialog').showModal();
    }catch(error){el('error').textContent=error.message;}
  }
  el('filter').addEventListener('submit', e=>{e.preventDefault();page=1;load();});el('prev').onclick=()=>{page--;load();};el('next').onclick=()=>{page++;load();};
  el('rows').onclick=e=>{const button=e.target.closest('[data-rp-id]');if(button)open(button.dataset.rpId);};el('close').onclick=()=>{if(!saving)el('dialog').close();};el('dialog').addEventListener('cancel',e=>{if(saving)e.preventDefault();});el('status').onchange=fields;
  el('update').addEventListener('submit',async e=>{e.preventDefault();if(saving)return;saving=true;el('save').disabled=true;el('modal-error').textContent='';try{await api(`/${selected.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:selected.version,status:el('status').value,notes:el('notes').value,next_followup_date:el('due').value,closure_reason:el('reason').value,purchase_index:el('purchase').value === '' ? null : Number(el('purchase').value)})});el('dialog').close();await load();}catch(error){el('modal-error').textContent=error.message;}finally{saving=false;el('save').disabled=false;}});
  load();
})();
