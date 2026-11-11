(() => {
  const badge=document.getElementById('agentMissingProductsBadge');
  if(!badge || badge.dataset.bound)return;
  badge.dataset.bound='1';let pending=false;
  async function refresh(){
    if(document.hidden || pending)return;
    pending=true;
    try{
      const response=await fetch('/undelivered_items/count_due',{credentials:'same-origin'});
      const data=await response.json();
      if(!response.ok || !data.ok)return;
      const count=Number(data.pending_total||0);
      badge.textContent=count;badge.classList.toggle('d-none',count<=0);
      badge.setAttribute('aria-label',`${count} open undelivered product records`);
    }catch{}finally{pending=false;}
  }
  window.addEventListener('undelivered-products-updated',refresh);
  window.addEventListener('focus',refresh);
  window.addEventListener('storage',event=>{if(event.key==='undelivered-products-updated')refresh();});
  document.addEventListener('visibilitychange',refresh);
  refresh();
  if(window.smartPoll)window.smartPoll(refresh,30000);else setInterval(refresh,30000);
})();
