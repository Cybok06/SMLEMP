(function () {
  const cfg = window.cardsTrackerConfig || {};
  if (!cfg.role) return;

  const qs = (sel) => document.querySelector(sel);

  const productSearch = qs('#cardsProductSearch');
  const productGrid = qs('#cardsProductGrid');
  const stockForm = qs('#cardsStockForm');
  const transferModal = qs('#cardsTransferModal');
  const transferForm = qs('#cardsTransferForm');
  const transferTitle = qs('#cardsTransferTitle');
  const transferManager = qs('#cardsTransferManager');
  const transferAgent = qs('#cardsTransferAgent');
  const adjustModal = qs('#cardsAdjustModal');
  const adjustForm = qs('#cardsAdjustForm');
  const adjustTitle = qs('#cardsAdjustTitle');

  const kpiStock = qs('#kpiTotalStock');
  const kpiTransferred = qs('#kpiTransferred');
  const kpiSold = qs('#kpiSold');
  const kpiRemaining = qs('#kpiRemaining');

  const kpiManagerReceived = qs('#kpiManagerReceived');
  const kpiManagerTransferred = qs('#kpiManagerTransferred');
  const kpiManagerSold = qs('#kpiManagerSold');
  const kpiManagerRemaining = qs('#kpiManagerRemaining');

  const managersTableBody = qs('#cardsManagersTableBody');
  const agentsTableBody = qs('#cardsAgentsTableBody');
  const agentProductsTableBody = qs('#cardsAgentProductsBody');
  const ledgerTableBody = qs('#cardsLedgerTableBody');
  const toastContainer = qs('#cardsToastContainer');
  const managerSelect = qs('#cardsManagerSelect');
  const managerSearch = qs('#cardsManagerSearch');
  const managerGrid = qs('#cardsManagerGrid');
  const managerTotalReceived = qs('#managerTotalReceived');
  const managerTotalSold = qs('#managerTotalSold');
  const managerTotalLeft = qs('#managerTotalLeft');

  let selectedProductKey = '';
  let productsCache = [];
  let selectedProduct = null;
  let managerGridCache = { id: '', cards: [], totals: {} };

  function fmtInt(val) {
    return Number(val || 0).toLocaleString();
  }

  function showToast(message, isError) {
    if (!toastContainer) {
      alert(message);
      return;
    }
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center text-bg-${isError ? 'danger' : 'success'} border-0`;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');
    toastEl.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    `;
    toastContainer.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 3000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
  }

  function progressClass(pct) {
    if (pct >= 100) return 'bg-success';
    if (pct >= 50) return 'bg-warning';
    return 'bg-danger';
  }

  function renderProgress(pct, title) {
    const displayPct = Math.min(200, Math.max(0, Number(pct || 0)));
    return `
      <div class="d-flex flex-column gap-1" title="${title || ''}">
        <div class="progress ct-progress">
          <div class="progress-bar ${progressClass(displayPct)}" style="width:${displayPct}%"></div>
        </div>
        <small class="ct-subtext">${displayPct.toFixed(1)}%</small>
      </div>
    `;
  }

  function renderProductGrid(list) {
    if (!productGrid) return;
    const query = (productSearch ? productSearch.value : '').trim().toLowerCase();
    const rows = (list || []).filter((p) => !query || String(p.name || '').toLowerCase().includes(query));
    if (!rows.length) {
      productGrid.innerHTML = '<div class="text-muted">No cards found.</div>';
      return;
    }
    productGrid.innerHTML = rows.map((p) => {
      const isActive = p.product_key === selectedProductKey;
      const qtyVal = Number(
        cfg.role === 'executive'
          ? (p.stock_available ?? p.available_qty ?? p.remaining ?? 0)
          : (p.available_qty ?? p.remaining ?? p.stock_available ?? 0)
      );
      const qtyText = `QTY: ${fmtInt(qtyVal)}`;
      const lowClass = qtyVal <= 0 ? 'ct-qty-out' : (qtyVal < 10 ? 'ct-qty-low' : '');
      let metrics = '';
      if (cfg.role === 'executive') {
        metrics = `
          <div class="ct-product-metrics">
            <span>Stock: ${fmtInt(p.stock_total)}</span>
            <span>Transferred: ${fmtInt(p.transferred_total)}</span>
            <span>Sold: ${fmtInt(p.sold_total)}</span>
            <span>Remaining: ${fmtInt(p.stock_available)}</span>
          </div>
        `;
      } else if (cfg.role === 'manager') {
        metrics = `
          <div class="ct-product-metrics">
            <span>Received: ${fmtInt(p.received)}</span>
            <span>To Agents: ${fmtInt(p.transferred_to_agents)}</span>
            <span>Sold: ${fmtInt(p.sold_total)}</span>
            <span>Remaining: ${fmtInt(p.remaining)}</span>
          </div>
        `;
      }
      const last = p.last_transfer || {};
      const lastInfo = last && (last.transfer_date || last.qty) ? `
        <div class="ct-subtext">Last transfer: ${last.transfer_date || '-'} • ${fmtInt(last.qty || 0)} to ${last.to_label || '-'}</div>
      ` : '';
      return `
        <div class="col-md-6 col-lg-4">
          <div class="ct-product-card ${isActive ? 'active' : ''}" data-product-key="${p.product_key}">
            ${qtyVal <= 0 ? '<div class="ct-out-overlay">Out of Stock</div>' : ''}
            <img src="${p.image_url || 'https://via.placeholder.com/80x80?text=Card'}" alt="${p.name}">
            <div class="flex-grow-1">
              <div class="fw-semibold">${p.name}</div>
              <div class="ct-qty-badge ${lowClass}">${qtyText}</div>
              ${metrics}
              ${lastInfo}
              <div class="ct-card-actions">
                <button class="btn btn-sm btn-outline-primary" data-action="transfer" data-product-key="${p.product_key}">
                  <i class="bi bi-send"></i> Transfer
                </button>
                <button class="btn btn-sm btn-outline-success" data-action="adjust-add" data-product-key="${p.product_key}">
                  <i class="bi bi-plus-circle"></i> Add
                </button>
                <button class="btn btn-sm btn-outline-danger" data-action="adjust-sub" data-product-key="${p.product_key}">
                  <i class="bi bi-dash-circle"></i> Subtract
                </button>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  async function fetchData() {
    if (!cfg.endpoints || !cfg.endpoints.data) return;
    const qsParam = selectedProductKey ? `?product_key=${encodeURIComponent(selectedProductKey)}` : '';
    const url = cfg.endpoints.data + qsParam;
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json();
    if (!data.ok) {
      return;
    }

    productsCache = data.products || productsCache;
    if (!selectedProductKey) {
      selectedProductKey = (data.product && data.product.product_key) || (productsCache[0] && productsCache[0].product_key) || '';
    }
    selectedProduct = productsCache.find((p) => p.product_key === selectedProductKey) || productsCache[0] || null;
    renderProductGrid(productsCache);

    if (cfg.role === 'executive') {
      renderExecutive(data);
    } else if (cfg.role === 'manager') {
      renderManager(data);
    } else {
      renderAgent(data);
    }
  }

  function renderManagerGrid(cards, totals) {
    if (!managerGrid) return;
    const query = (managerSearch ? managerSearch.value : '').trim().toLowerCase();
    const rows = (cards || []).filter((c) => !query || String(c.product_name || '').toLowerCase().includes(query));
    if (managerTotalReceived) managerTotalReceived.textContent = fmtInt((totals && totals.received) || 0);
    if (managerTotalSold) managerTotalSold.textContent = fmtInt((totals && totals.sold) || 0);
    if (managerTotalLeft) managerTotalLeft.textContent = fmtInt((totals && totals.available) || 0);
    if (!rows.length) {
      managerGrid.innerHTML = '<div class="text-muted">No cards found.</div>';
      return;
    }
    managerGrid.innerHTML = rows.map((c) => {
      const pct = Math.min(200, Math.max(0, Number(c.sold_pct || 0)));
      const lowClass = c.available_qty <= 0 ? 'ct-qty-out' : (c.available_qty < 10 ? 'ct-qty-low' : '');
      return `
        <div class="col-md-6 col-lg-4">
          <div class="ct-product-card">
            ${c.available_qty <= 0 ? '<div class="ct-out-overlay">Out of Stock</div>' : ''}
            <img src="${c.image_url || 'https://via.placeholder.com/80x80?text=Card'}" alt="${c.product_name}">
            <div class="flex-grow-1">
              <div class="fw-semibold">${c.product_name}</div>
              <div class="ct-qty-badge ${lowClass}">QTY: ${fmtInt(c.available_qty)}</div>
              <div class="ct-product-metrics">
                <span>Received: ${fmtInt(c.received_qty)}</span>
                <span>Sold: ${fmtInt(c.sold_qty)}</span>
                <span>Left: ${fmtInt(c.available_qty)}</span>
                <span>Sold %: ${pct.toFixed(1)}%</span>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  async function fetchManagerGrid(managerId) {
    if (!cfg.endpoints || !cfg.endpoints.managerGrid || !managerId) return;
    const url = `${cfg.endpoints.managerGrid}/${encodeURIComponent(managerId)}/grid`;
    try {
      const res = await fetch(url, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.ok) {
        showToast(data.message || 'Failed to load manager cards', true);
        return;
      }
      managerGridCache = { id: managerId, cards: data.cards || [], totals: data.totals || {} };
      renderManagerGrid(data.cards || [], data.totals || {});
    } catch (err) {
      showToast('Failed to load manager cards', true);
    }
  }

  function renderExecutive(data) {
    const kpis = data.kpis || {};
    if (kpiStock) kpiStock.textContent = fmtInt(kpis.total_stock || 0);
    if (kpiTransferred) kpiTransferred.textContent = fmtInt(kpis.transferred_to_managers || 0);
    if (kpiSold) kpiSold.textContent = fmtInt(kpis.sold_total_today ?? kpis.sold_total ?? 0);
    if (kpiRemaining) kpiRemaining.textContent = fmtInt(kpis.remaining_exec || 0);

    if (managersTableBody) {
      const rows = data.managers || [];
      managersTableBody.innerHTML = rows.length ? rows.map((row) => {
        const title = `Sold ${fmtInt(row.sold_by_agents)} / Received ${fmtInt(row.received)}`;
        return `
          <tr class="${row.target_hit ? 'ct-hit-row' : ''}">
            <td>${row.manager_name || 'Manager'}</td>
            <td>${row.branch || '-'}</td>
            <td class="text-end">${fmtInt(row.received)}</td>
            <td class="text-end">${fmtInt(row.given_to_agents)}</td>
            <td class="text-end">${fmtInt(row.sold_by_agents)}</td>
            <td class="text-end">${fmtInt(row.remaining)}</td>
            <td class="text-end">${renderProgress(row.progress_pct, title)}</td>
            <td class="text-end">
              <button class="btn btn-sm btn-outline-primary" data-transfer="manager" data-id="${row.manager_id}">
                Transfer
              </button>
            </td>
          </tr>
        `;
      }).join('') : '<tr><td colspan="8" class="text-muted">No managers found.</td></tr>';
    }

    renderLedger(data.ledger || []);
  }

  function renderManager(data) {
    const summary = data.summary || {};
    if (kpiManagerReceived) kpiManagerReceived.textContent = fmtInt(summary.received_total || 0);
    if (kpiManagerTransferred) kpiManagerTransferred.textContent = fmtInt(summary.transferred_total || 0);
    if (kpiManagerSold) kpiManagerSold.textContent = fmtInt(summary.sold_total || 0);
    if (kpiManagerRemaining) kpiManagerRemaining.textContent = fmtInt(summary.remaining_total || 0);

    if (agentsTableBody) {
      const rows = data.agents || [];
      agentsTableBody.innerHTML = rows.length ? rows.map((row) => {
        const title = `Sold ${fmtInt(row.sold)} / Given ${fmtInt(row.given)}`;
        return `
          <tr class="${row.target_hit ? 'ct-hit-row' : ''}">
            <td>${row.agent_name || 'Agent'}</td>
            <td>${row.branch || '-'}</td>
            <td class="text-end">${fmtInt(row.given)}</td>
            <td class="text-end">${fmtInt(row.sold)}</td>
            <td class="text-end">${fmtInt(row.left)}</td>
            <td class="text-end">${renderProgress(row.progress_pct, title)}</td>
            <td class="text-end">
              <button class="btn btn-sm btn-outline-primary" data-transfer="agent" data-id="${row.agent_id}">
                Transfer
              </button>
            </td>
          </tr>
        `;
      }).join('') : '<tr><td colspan="7" class="text-muted">No agents found.</td></tr>';
    }

    renderLedger(data.ledger || []);
  }

  function renderAgent(data) {
    if (!agentProductsTableBody) return;
    const rows = data.products || [];
    agentProductsTableBody.innerHTML = rows.length ? rows.map((row) => {
      const title = `Sold ${fmtInt(row.sold)} / Given ${fmtInt(row.given)}`;
      return `
        <tr>
          <td>${row.product_name || 'Card'}</td>
          <td class="text-end">${fmtInt(row.given)}</td>
          <td class="text-end">${fmtInt(row.sold)}</td>
          <td class="text-end">${fmtInt(row.left)}</td>
          <td class="text-end">${renderProgress(row.progress_pct, title)}</td>
        </tr>
      `;
    }).join('') : '<tr><td colspan="5" class="text-muted">No cards assigned yet.</td></tr>';
  }

  function renderLedger(rows) {
    if (!ledgerTableBody) return;
    ledgerTableBody.innerHTML = rows.length ? rows.map((row) => {
      const ts = row.created_at ? new Date(row.created_at).toLocaleString() : '-';
      const from = `${row.from_type || ''} ${row.from_id || ''}`.trim();
      const to = `${row.to_type || ''} ${row.to_id || ''}`.trim();
      return `
        <tr>
          <td>${row.transfer_id || ''}</td>
          <td class="text-end">${fmtInt(row.qty)}</td>
          <td>${from}</td>
          <td>${to}</td>
          <td>${row.note || ''}</td>
          <td>${row.transfer_date || ''}</td>
          <td>${ts}</td>
        </tr>
      `;
    }).join('') : '<tr><td colspan="7" class="text-muted">No transfers yet.</td></tr>';
  }

  function openTransferModal(targetType, targetId) {
    if (!transferModal) return;
    transferModal.dataset.targetType = targetType;
    transferModal.dataset.targetId = targetId;
    if (transferTitle) {
      transferTitle.textContent = targetType === 'manager' ? 'Transfer to Manager' : 'Transfer to Agent';
    }
    if (transferManager && targetType === 'manager') {
      transferManager.value = targetId || transferManager.value;
    }
    if (transferAgent && targetType === 'agent') {
      transferAgent.value = targetId || transferAgent.value;
    }
    const dateInput = transferForm ? transferForm.querySelector('input[name="transfer_date"]') : null;
    if (dateInput && !dateInput.value) {
      const today = new Date().toISOString().slice(0, 10);
      dateInput.value = today;
    }
    const modal = new bootstrap.Modal(transferModal);
    modal.show();
  }

  function openAdjustModal(deltaSign) {
    if (!adjustModal) return;
    adjustModal.dataset.deltaSign = String(deltaSign);
    if (adjustTitle) {
      adjustTitle.textContent = deltaSign > 0 ? 'Add Cards' : 'Subtract Cards';
    }
    const dateInput = adjustForm ? adjustForm.querySelector('input[name="date"]') : null;
    if (dateInput && !dateInput.value) {
      const today = new Date().toISOString().slice(0, 10);
      dateInput.value = today;
    }
    const modal = new bootstrap.Modal(adjustModal);
    modal.show();
  }

  async function submitStockForm(e) {
    e.preventDefault();
    const form = new FormData(stockForm);
    const payload = Object.fromEntries(form.entries());
    payload.product_key = selectedProductKey;
    const res = await fetch(cfg.endpoints.setStock, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      await fetchData();
      stockForm.reset();
    } else {
      alert(data.error || 'Failed to update stock');
    }
  }

  async function submitTransferForm(e) {
    e.preventDefault();
    if (!transferModal) return;
    const form = new FormData(transferForm);
    const payload = Object.fromEntries(form.entries());
    payload.product_key = selectedProductKey;
    payload.product_name = (selectedProduct && selectedProduct.name) || '';
    payload.image_url = (selectedProduct && selectedProduct.image_url) || '';
    const targetType = transferModal.dataset.targetType;
    const targetId = transferModal.dataset.targetId;

    if (targetType === 'manager') payload.manager_id = payload.manager_id || targetId;
    if (targetType === 'agent') payload.agent_id = payload.agent_id || targetId;

    const submitBtn = transferForm.querySelector('button[type="submit"]');
    const originalLabel = submitBtn ? submitBtn.innerHTML : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Transferring...';
    }
    try {
      const res = await fetch(cfg.endpoints.transfer, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        showToast(data.message || 'Transfer successful');
        await fetchData();
        transferForm.reset();
        bootstrap.Modal.getInstance(transferModal).hide();
      } else {
        showToast(`Transfer failed: ${data.message || 'Unknown error'}`, true);
      }
    } catch (err) {
      showToast('Transfer failed: Network error', true);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalLabel || 'Confirm';
      }
    }
  }

  async function submitAdjustForm(e) {
    e.preventDefault();
    if (!adjustModal || !cfg.endpoints || !cfg.endpoints.adjust) return;
    const form = new FormData(adjustForm);
    const payload = Object.fromEntries(form.entries());
    const deltaSign = Number(adjustModal.dataset.deltaSign || '1') || 1;
    const qty = Number(payload.qty || 0);
    if (!qty || qty <= 0) {
      alert('Quantity must be positive');
      return;
    }
    payload.delta_qty = Math.round(qty) * deltaSign;
    payload.product_key = selectedProductKey;
    payload.product_name = (selectedProduct && selectedProduct.name) || '';
    payload.image_url = (selectedProduct && selectedProduct.image_url) || '';
    const submitBtn = adjustForm.querySelector('button[type="submit"]');
    const originalLabel = submitBtn ? submitBtn.innerHTML : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
    }
    try {
      const res = await fetch(cfg.endpoints.adjust, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        showToast(data.message || 'Adjustment saved');
        await fetchData();
        adjustForm.reset();
        bootstrap.Modal.getInstance(adjustModal).hide();
      } else {
        showToast(`Adjustment failed: ${data.message || 'Unknown error'}`, true);
      }
    } catch (err) {
      showToast('Adjustment failed: Network error', true);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalLabel || 'Save';
      }
    }
  }

  function bindEvents() {
    if (productSearch) {
      productSearch.addEventListener('input', () => renderProductGrid(productsCache));
    }
    if (stockForm) {
      stockForm.addEventListener('submit', submitStockForm);
    }
    if (transferForm) {
      transferForm.addEventListener('submit', submitTransferForm);
    }
    if (adjustForm) {
      adjustForm.addEventListener('submit', submitAdjustForm);
    }
    if (managerSelect) {
      managerSelect.addEventListener('change', () => {
        const id = managerSelect.value;
        if (!id) {
          if (managerGrid) managerGrid.innerHTML = '<div class="text-muted">Select a manager to load cards.</div>';
          if (managerTotalReceived) managerTotalReceived.textContent = '0';
          if (managerTotalSold) managerTotalSold.textContent = '0';
          if (managerTotalLeft) managerTotalLeft.textContent = '0';
          return;
        }
        fetchManagerGrid(id);
      });
    }
    if (managerSearch) {
      managerSearch.addEventListener('input', () => {
        const id = managerSelect ? managerSelect.value : '';
        if (id && managerGridCache.id === id) {
          renderManagerGrid(managerGridCache.cards, managerGridCache.totals);
        } else if (id) {
          fetchManagerGrid(id);
        }
      });
    }

    document.addEventListener('click', (e) => {
      const actionBtn = e.target.closest('[data-action]');
      if (actionBtn) {
        const key = actionBtn.dataset.productKey;
        if (key) {
          selectedProductKey = key;
          selectedProduct = productsCache.find((p) => p.product_key === key) || selectedProduct;
        }
        if (actionBtn.dataset.action === 'transfer') {
          openTransferModal(cfg.role === 'executive' ? 'manager' : 'agent');
        } else if (actionBtn.dataset.action === 'adjust-add') {
          openAdjustModal(1);
        } else if (actionBtn.dataset.action === 'adjust-sub') {
          openAdjustModal(-1);
        }
        return;
      }
      const card = e.target.closest('.ct-product-card');
      if (card && card.dataset.productKey) {
        selectedProductKey = card.dataset.productKey;
        fetchData();
        return;
      }
      const btn = e.target.closest('[data-transfer]');
      if (!btn) return;
      openTransferModal(btn.dataset.transfer, btn.dataset.id);
    });
  }

  bindEvents();
  fetchData();
})();
