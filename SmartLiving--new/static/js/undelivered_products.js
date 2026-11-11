(() => {
  const root = document.getElementById('undeliveredPage');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const labels = {pending:'Undelivered', preparing:'Preparing', products_in:'Products In', delivered:'Delivered'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
  let page = 1, pages = 1, rows = [], requestNumber = 0, busy = false;
  const feedback = (message, success = false) => {const box=$('undeliveredFeedback');box.textContent=message;box.className=`alert alert-${success?'success':'danger'} mt-3`;};
  function notify() {
    window.dispatchEvent(new Event('undelivered-products-updated'));
    try {localStorage.setItem('undelivered-products-updated',String(Date.now()));} catch {}
  }
  async function load() {
    if (busy || document.hidden) return;
    const number = ++requestNumber;
    const params = new URLSearchParams({status:$('statusSelect').value,page:String(page)});
    if ($('fromDate').value) params.set('from',$('fromDate').value);
    if ($('toDate').value) params.set('to',$('toDate').value);
    if ($('branchSelect')?.value) params.set('branch',$('branchSelect').value);
    try {
      const response = await fetch('/undelivered_items/data?'+params,{credentials:'same-origin'});
      const data = await response.json();
      if (!response.ok || !data.ok) throw Error(data.message || 'Unable to load undelivered products.');
      if(number !== requestNumber) return;
      rows=data.rows;page=data.page;pages=data.pages;
      $('rowsBody').innerHTML=rows.map(row=>`<tr>
        <td>${esc(row.created_at ? new Date(row.created_at).toLocaleDateString() : '')}</td><td>${esc(row.expected_delivery_date || 'Not set')}</td>
        <td>${esc(row.customer_name)}</td><td>${esc(row.agent_name)}</td><td>${esc(row.agent_branch || row.manager_branch)}</td>
        <td>${esc(row.product_name)}<div class="small text-muted">Card ${Number(row.product_index)+1}</div></td>
        <td><button type="button" class="btn btn-outline-primary btn-sm" data-view-missing="${esc(row.id)}">View products (${(row.undelivered_items || []).length})</button></td>
        <td><span class="badge badge-soft">${esc(row.status_label)}</span><div class="small mt-2">${row.inventory_sent_at ? "Sent to inventory" : ""}</div>${row.inventory_reason ? `<div class="small mt-2"><strong>Inventory reason:</strong> ${esc(row.inventory_reason)}</div>` : ""}</td>
        <td>${root.dataset.role==='manager' && row.status!=='delivered' && !row.inventory_sent_at ? `<button class="btn btn-outline-primary btn-sm mb-2" data-send-inventory="${esc(row.id)}">Send to Inventory</button>` : ""}${root.dataset.role==='inventory' && row.status!=='delivered' ? `<button class="btn btn-primary btn-sm" data-inventory-edit="${esc(row.id)}">Update with reason</button>` : row.status==='delivered'?'<span class="text-success">Delivered</span>':`<div class="d-flex gap-2"><select class="form-select form-select-sm" aria-label="Status for ${esc(row.customer_name)}" data-status-missing="${esc(row.id)}">${Object.entries(labels).map(([key,label])=>`<option value="${key}" ${row.status===key?'selected':''}>${label}</option>`).join('')}</select><button type="button" class="btn btn-primary btn-sm" data-save-missing="${esc(row.id)}">Save</button></div>`}</td></tr>`).join('') || '<tr><td colspan="9" class="text-center text-muted py-4">No records match these filters.</td></tr>';
      $('pageInfo').textContent=`${data.total} records · Page ${page} of ${pages}`;
      $('previousPage').disabled=page<=1;$('nextPage').disabled=page>=pages;
    } catch(error) {feedback(error.message);}
  }
  $('rowsBody').addEventListener('click', async event => {
    const view=event.target.closest('[data-view-missing]');
    if(view){
      const row=rows.find(row=>row.id===view.dataset.viewMissing);
      $('missingDetailsTitle').textContent=row.product_name;
      $('missingDetailsCustomer').textContent=`${row.customer_name} · ${row.agent_name || ''} · ${row.status_label}`;
      $('missingDetailsItems').innerHTML=`<table class="table"><thead><tr><th>Missing product</th><th>Quantity</th><th>Source</th></tr></thead><tbody>${(row.undelivered_items||[]).map(item=>`<tr><td>${esc(item.name)}</td><td>${esc(item.undelivered_qty || item.required_qty || 0)}</td><td>${item.source==='manual'?'Manually entered':'Inventory'}</td></tr>`).join('')}</tbody></table>`;
      $('missingDetailsNote').textContent=[row.note, row.inventory_reason ? 'Inventory reason: '+row.inventory_reason : ''].filter(Boolean).join(' ? ');
      $('missingDetailsHistory').innerHTML=(row.history||[]).map(entry=>`<div>${esc(entry.event==='sent_to_inventory'?'Sent to inventory':entry.event==='inventory_update'?'Inventory update: '+(labels[entry.status] || entry.status):labels[entry.status] || entry.status)}${entry.reason ? ' ? '+esc(entry.reason) : ''} · ${esc(entry.name || entry.role || '')} · ${esc(new Date(entry.at).toLocaleString())}</div>`).join('');
      $('undeliveredDetails').showModal();return;
    }
    const send=event.target.closest('[data-send-inventory]');
    if(send && !busy){
      busy=true;send.disabled=true;
      try {
        const response=await fetch(`/undelivered_items/${send.dataset.sendInventory}/send-to-inventory`,{method:'POST',credentials:'same-origin'});
        const data=await response.json();
        if(!response.ok || !data.ok)throw Error(data.message || 'Unable to send to inventory.');
        feedback(data.message,true);notify();
      }catch(error){feedback(error.message);}finally{busy=false;load();}
      return;
    }
    const edit=event.target.closest('[data-inventory-edit]');
    if(edit){
      const row=rows.find(row=>row.id===edit.dataset.inventoryEdit);
      $('inventoryRecordId').value=row.id;
      $('inventoryReasonError').textContent='';
      $('inventoryReason').value=row.inventory_reason || '';
      $('inventoryStatus').value=row.status;
      $('inventoryReasonDialog').showModal();return;
    }
    const button=event.target.closest('[data-save-missing]');
    if(!button || busy) return;
    const row=rows.find(row=>row.id===button.dataset.saveMissing);
    const status=[...root.querySelectorAll('[data-status-missing]')].find(input=>input.dataset.statusMissing===row.id).value;
    if(status===row.status)return;
    busy=true;button.disabled=true;
    try {
      let body,headers;
      if(status==='delivered' && root.dataset.role==='agent'){
        const photo=await window.DeliveryCamera.capture();
        if(!photo)return;
        body=new FormData();body.set('delivery_photo',photo,'delivery-camera.jpg');body.set('status',status);body.set('version',row.version);
      } else {body=JSON.stringify({status,version:row.version});headers={'Content-Type':'application/json'};}
      const response=await fetch(`/undelivered_items/${row.id}/status`,{method:'POST',credentials:'same-origin',headers,body});
      const data=await response.json();
      if(!response.ok || !data.ok)throw Error(data.message || 'Unable to update status.');
      feedback('Status saved. The agent and manager share this update.',true);notify();
    }catch(error){feedback(error.message);}finally{busy=false;button.disabled=false;load();}
  });
  $('inventoryReasonForm')?.addEventListener('submit', async event=>{
    event.preventDefault();if(busy)return;
    const row=rows.find(row=>row.id===$('inventoryRecordId').value);
    busy=true;
    const button=$('saveInventoryReason');button.disabled=true;
    try {
      const response=await fetch(`/undelivered_items/${row.id}/inventory-update`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:$('inventoryReason').value,status:$('inventoryStatus').value,version:row.version})});
      const data=await response.json();
      if(!response.ok || !data.ok)throw Error(data.message || 'Unable to save reason.');
      $('inventoryReasonDialog').close();feedback(data.message,true);notify();
    }catch(error){$('inventoryReasonError').textContent=error.message;}finally{busy=false;button.disabled=false;load();}
  });
  $('cancelInventoryReason')?.addEventListener('click',()=>$('inventoryReasonDialog').close());
  $('closeMissingDetails').onclick=()=>$('undeliveredDetails').close();
  $('filterForm').onsubmit=event=>{event.preventDefault();page=1;load();};
  $('refreshBtn').onclick=load;
  $('previousPage').onclick=()=>{page=Math.max(1,page-1);load();};
  $('nextPage').onclick=()=>{page=Math.min(pages,page+1);load();};
  window.addEventListener('focus',load);
  window.addEventListener('storage',event=>{if(event.key==='undelivered-products-updated')load();});
  load();
  if(window.smartPoll)window.smartPoll(load,30000);else setInterval(load,30000);
})();
