(function(){
  "use strict";

  // Async section loading while preserving existing dashboard layout.
  const rangeSelector = document.getElementById("rangeSelector");
  const apiBase = document.body ? document.body.getAttribute("data-acc-api-base") : "";
  const rangeLabelEl = document.getElementById("rangeLabel");
  const recentCountEl = document.getElementById("recentActivityCount");
  const bankAccountsList = document.getElementById("bankAccountsList");
  const topCustomersList = document.getElementById("topCustomersList");
  const topSuppliersList = document.getElementById("topSuppliersList");
  const recentActivityBody = document.getElementById("recentActivityBody");

  const charts = {
    bankCash: null,
    salesExpense: null,
    cashFlow: null,
    arAging: null,
    apDue: null
  };

  const controllers = new Map();
  let pendingLoads = 0;

  document.addEventListener("click", function(ev){
    const a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    if (!a) return;
    if (a.target === "_blank" || a.hasAttribute("download")) return;
    const href = a.getAttribute("href") || "";
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
    if (a.getAttribute("data-no-loader") === "1") return;
    if (window.showPageLoader) window.showPageLoader();
  });

  function setError(key, message) {
    const el = document.querySelector(`[data-acc-error="${key}"]`);
    if (!el) return;
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function clearError(key) {
    const el = document.querySelector(`[data-acc-error="${key}"]`);
    if (!el) return;
    el.classList.add("hidden");
  }

  function fetchSection(key, url, onSuccess) {
    clearError(key);
    const existing = controllers.get(key);
    if (existing) existing.abort();

    const controller = new AbortController();
    controllers.set(key, controller);

    pendingLoads += 1;

    fetch(url, {
      method: "GET",
      headers: { "Accept": "application/json" },
      signal: controller.signal
    })
      .then((res) => res.json())
      .then((payload) => {
        if (!payload || payload.ok !== true) {
          throw new Error(payload && payload.error ? payload.error : "Request failed");
        }
        onSuccess(payload.data || {}, payload.range_key || "");
      })
      .catch((err) => {
        if (err && err.name === "AbortError") return;
        setError(key, err && err.message ? err.message : "Unable to load section.");
      })
      .finally(() => {
        pendingLoads = Math.max(0, pendingLoads - 1);
      });
  }

  function updateUrlRange(rangeKey) {
    const url = new URL(window.location.href);
    url.searchParams.set("range", rangeKey);
    window.history.replaceState({}, "", url.toString());
  }

  function formatMoneySafe(value) {
    if (window.formatMoney) return window.formatMoney(value);
    const v = Number(value || 0);
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(v);
  }

  function formatNumberSafe(value) {
    const v = Number(value || 0);
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(v);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function updateKpis(data) {
    const kpis = data.kpis || {};
    const rangeLabel = data.range_label;

    const mapping = {
      cash_balance: formatMoneySafe(kpis.cash_balance),
      ar_total: formatMoneySafe(kpis.ar_total),
      ap_total: formatMoneySafe(kpis.ap_total),
      net_profit: formatMoneySafe(kpis.net_profit),
      expenses_total: formatMoneySafe(kpis.expenses_total),
      ar_overdue_pct: (kpis.ar_overdue_pct == null ? "0" : kpis.ar_overdue_pct),
      net_book_value: formatMoneySafe(kpis.net_book_value),
      unreconciled_count: formatNumberSafe(kpis.unreconciled_count),
      draft_journals: formatNumberSafe(kpis.draft_journals)
    };

    Object.keys(mapping).forEach((key) => {
      const el = document.querySelector(`[data-acc-kpi="${key}"]`);
      if (el) el.textContent = mapping[key];
    });

    const profitEl = document.querySelector('[data-acc-kpi="net_profit"]');
    if (profitEl && profitEl.parentElement) {
      const parent = profitEl.parentElement;
      parent.classList.remove("text-red-600", "text-navy", "dark:text-white");
      if (Number(kpis.net_profit || 0) < 0) {
        parent.classList.add("text-red-600");
      } else {
        parent.classList.add("text-navy", "dark:text-white");
      }
    }

    if (rangeLabelEl && rangeLabel) {
      rangeLabelEl.textContent = rangeLabel;
    }
  }

  function updateBankCash(data) {
    const breakdown = data.breakdown || {};
    const totals = {
      bank: formatMoneySafe(breakdown.bank),
      mobile_money: formatMoneySafe(breakdown.mobile_money),
      cash: formatMoneySafe(breakdown.cash)
    };

    const bankEl = document.querySelector('[data-acc-bank="bank"]');
    const momoEl = document.querySelector('[data-acc-bank="mobile_money"]');
    if (bankEl) bankEl.textContent = totals.bank;
    if (momoEl) momoEl.textContent = totals.mobile_money;

    const totalBalanceEl = document.querySelector('[data-acc-bank="total_balance"]');
    const bankTotalEl = document.querySelector('[data-acc-bank="bank_total"]');
    const momoTotalEl = document.querySelector('[data-acc-bank="mobile_money_total"]');
    const cashTotalEl = document.querySelector('[data-acc-bank="cash_total"]');

    if (totalBalanceEl) totalBalanceEl.textContent = formatMoneySafe(data.cash_balance);
    if (bankTotalEl) bankTotalEl.textContent = totals.bank;
    if (momoTotalEl) momoTotalEl.textContent = totals.mobile_money;
    if (cashTotalEl) cashTotalEl.textContent = totals.cash;

    if (bankAccountsList) {
      const accounts = Array.isArray(data.accounts) ? data.accounts : [];
      if (!accounts.length) {
        bankAccountsList.innerHTML = "<p class=\"text-xs text-gray-500 dark:text-gray-400\">No bank or mobile money accounts configured yet.</p>";
      } else {
        const rows = accounts.map((acc) => {
          const typeKey = acc.type_key || "bank";
          let colorClass = "text-navy dark:text-gray-100";
          if (typeKey === "mobile_money") colorClass = "text-emerald-700 dark:text-emerald-300";
          if (typeKey === "cash") colorClass = "text-amber-700 dark:text-amber-300";

          const name = escapeHtml(acc.name || "Account");
          const provider = acc.provider ? ` -  ${escapeHtml(acc.provider)}` : "";
          const number = acc.number ? ` -  ${escapeHtml(acc.number)}` : "";

          return (
            `<div class=\"flex items-center justify-between rounded-lg px-3 py-2 bg-gray-50/70 dark:bg-slate-900/60\">` +
              `<div class=\"flex flex-col\">` +
                `<span class=\"text-sm font-medium text-navy dark:text-white\">${name}</span>` +
                `<span class=\"text-[11px] text-gray-500 dark:text-gray-400\">${escapeHtml(acc.type_label || "Account")}${provider}${number}</span>` +
              `</div>` +
              `<span class=\"text-sm font-semibold ${colorClass}\">GH₵ ${formatMoneySafe(acc.balance)}</span>` +
            `</div>`
          );
        });
        bankAccountsList.innerHTML = rows.join("");
      }
    }

    const bankCashCtx = document.getElementById("bankCashChart");
    if (bankCashCtx && window.Chart) {
      const labels = ["Bank", "Mobile Money", "Cash on Hand"];
      const vals = [Number(breakdown.bank || 0), Number(breakdown.mobile_money || 0), Number(breakdown.cash || 0)];

      if (!charts.bankCash) {
        charts.bankCash = new Chart(bankCashCtx, {
          type: "doughnut",
          data: {
            labels: labels,
            datasets: [{
              data: vals,
              backgroundColor: ["#0f172a", "#16a34a", "#f59e0b"],
              borderWidth: 0
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                display: true,
                position: "bottom",
                labels: { usePointStyle: true, boxWidth: 8 }
              },
              tooltip: {
                callbacks: {
                  label: (ctx) => `${ctx.label}: GH₵ ${formatMoneySafe(ctx.parsed || 0)}`
                }
              }
            },
            cutout: "65%"
          }
        });
      } else {
        charts.bankCash.data.labels = labels;
        charts.bankCash.data.datasets[0].data = vals;
        charts.bankCash.update();
      }
    }
  }

  function updateSalesExpense(data) {
    const labels = data.labels || [];
    const sales = data.sales || [];
    const expenses = data.expenses || [];
    const stock = data.stock || [];
    const profit = data.profit || [];
    const activeLiability = data.active_liability || [];

    const revExpCtx = document.getElementById("revenueExpensesChart");
    if (!revExpCtx || !window.Chart) return;

    if (!charts.salesExpense) {
      charts.salesExpense = new Chart(revExpCtx, {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Sales",
              data: sales,
              tension: 0.35,
              borderColor: "#16a34a",
              backgroundColor: "#16a34a33",
              borderWidth: 2,
              fill: true,
              pointRadius: 3,
              pointHoverRadius: 4
            },
            {
              label: "Expenses",
              data: expenses,
              tension: 0.35,
              borderColor: "#e11d48",
              backgroundColor: "#e11d4826",
              borderWidth: 2,
              fill: true,
              pointRadius: 3,
              pointHoverRadius: 4
            },
            {
              label: "Stock",
              data: stock,
              tension: 0.35,
              borderColor: "#0284c7",
              backgroundColor: "#0284c71A",
              borderWidth: 2,
              fill: false,
              pointRadius: 3,
              pointHoverRadius: 4
            },
            {
              label: "Profit",
              data: profit,
              tension: 0.35,
              borderColor: "#14b8a6",
              backgroundColor: "rgba(20,184,166,0.18)",
              borderWidth: 2,
              fill: false,
              pointRadius: 3,
              pointHoverRadius: 4
            },
            {
              label: "Active Liability",
              data: activeLiability,
              tension: 0.35,
              borderColor: "#f59e0b",
              backgroundColor: "rgba(245,158,11,0.14)",
              borderWidth: 2,
              fill: false,
              pointRadius: 3,
              pointHoverRadius: 4,
              yAxisID: "y1"
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: true, labels: { usePointStyle: true, boxWidth: 8 } },
            tooltip: {
              callbacks: {
                label: (ctx) => `${ctx.dataset.label}: GH₵ ${formatMoneySafe(ctx.parsed.y || 0)}`
              }
            }
          },
          scales: {
            x: { ticks: { color: "#64748b" }, grid: { display: false } },
            y: {
              ticks: { color: "#64748b", callback: (val) => formatNumberSafe(val) },
              grid: { color: "rgba(148,163,184,0.2)" }
            },
            y1: {
              position: "right",
              ticks: { color: "#b45309", callback: (val) => formatNumberSafe(val) },
              grid: { drawOnChartArea: false }
            }
          }
        }
      });
    } else {
      charts.salesExpense.data.labels = labels;
      charts.salesExpense.data.datasets[0].data = sales;
      charts.salesExpense.data.datasets[1].data = expenses;
      charts.salesExpense.data.datasets[2].data = stock;
      charts.salesExpense.data.datasets[3].data = profit;
      charts.salesExpense.data.datasets[4].data = activeLiability;
      charts.salesExpense.update();
    }
  }

  function updateCashFlow(data) {
    const labels = data.labels || [];
    const cashIn = data.cash_in || [];
    const cashOut = data.cash_out || [];
    const net = data.net || [];
    const cashInSales = data.cash_in_sources ? data.cash_in_sources.sales || [] : [];
    const cashInIncome = data.cash_in_sources ? data.cash_in_sources.income || [] : [];
    const cashOutExpenses = data.cash_out_sources ? data.cash_out_sources.expenses || [] : [];
    const cashOutWithdrawals = data.cash_out_sources ? data.cash_out_sources.withdrawals || [] : [];

    const cashCtx = document.getElementById("cashFlowChart");
    if (!cashCtx || !window.Chart) return;

    if (!charts.cashFlow) {
      charts.cashFlow = new Chart(cashCtx, {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Cash In",
              data: cashIn,
              backgroundColor: "#0284c7CC",
              borderColor: "#0284c7",
              borderWidth: 1,
              borderRadius: 6
            },
            {
              label: "Cash Out",
              data: cashOut,
              backgroundColor: "#f59e0bCC",
              borderColor: "#f59e0b",
              borderWidth: 1,
              borderRadius: 6
            },
            {
              type: "line",
              label: "Net Cash",
              data: net,
              borderColor: "#16a34a",
              backgroundColor: "#16a34a1A",
              borderWidth: 2,
              pointRadius: 3,
              pointHoverRadius: 4,
              tension: 0.3,
              fill: false
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: true, labels: { usePointStyle: true, boxWidth: 8 } },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const val = ctx.parsed.y || 0;
                  const i = ctx.dataIndex;
                  if (ctx.dataset.label === "Cash In") {
                    return [
                      `Cash In: GHS ${formatMoneySafe(val)}`,
                      `Sales: GHS ${formatMoneySafe(cashInSales[i] || 0)}`,
                      `Investment + Discount: GHS ${formatMoneySafe(cashInIncome[i] || 0)}`
                    ];
                  }
                  if (ctx.dataset.label === "Cash Out") {
                    return [
                      `Cash Out: GHS ${formatMoneySafe(val)}`,
                      `Expenses: GHS ${formatMoneySafe(cashOutExpenses[i] || 0)}`,
                      `Withdrawals: GHS ${formatMoneySafe(cashOutWithdrawals[i] || 0)}`
                    ];
                  }
                  return `${ctx.dataset.label}: GHS ${formatMoneySafe(val)}`;
                }
              }
            }
          },
          scales: {
            x: { stacked: false, ticks: { color: "#64748b" }, grid: { display: false } },
            y: { stacked: false, ticks: { color: "#64748b", callback: (val) => formatNumberSafe(val) }, grid: { color: "rgba(148,163,184,0.2)" } }
          }
        }
      });
    } else {
      charts.cashFlow.data.labels = labels;
      charts.cashFlow.data.datasets[0].data = cashIn;
      charts.cashFlow.data.datasets[1].data = cashOut;
      charts.cashFlow.data.datasets[2].data = net;
      charts.cashFlow.update();
    }
  }

  function updateArAging(data) {
    const labels = ["0-30", "31-60", "61-90", "90+ days"];
    const vals = [data.b0_30 || 0, data.b31_60 || 0, data.b61_90 || 0, data.b90_plus || 0];

    const arCtx = document.getElementById("arAgingChart");
    if (!arCtx || !window.Chart) return;

    if (!charts.arAging) {
      charts.arAging = new Chart(arCtx, {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{
            label: "Outstanding",
            data: vals,
            backgroundColor: ["#16a34aCC", "#0284c7CC", "#f59e0bCC", "#e11d48CC"],
            borderWidth: 0,
            borderRadius: 8
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => `Outstanding: GH₵ ${formatMoneySafe(ctx.parsed.y || 0)}`
              }
            }
          },
          indexAxis: "y",
          scales: {
            x: { ticks: { color: "#64748b" }, grid: { color: "rgba(148,163,184,0.2)" } },
            y: { ticks: { color: "#64748b" }, grid: { display: false } }
          }
        }
      });
    } else {
      charts.arAging.data.datasets[0].data = vals;
      charts.arAging.update();
    }
  }

  function updateApDue(data) {
    const labels = ["Due Today", "Next 7 days", "Next 30 days", "Overdue"];
    const vals = [data.due_today || 0, data.next_7 || 0, data.next_30 || 0, data.overdue || 0];

    const apCtx = document.getElementById("apDueChart");
    if (!apCtx || !window.Chart) return;

    if (!charts.apDue) {
      charts.apDue = new Chart(apCtx, {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{
            label: "Amount",
            data: vals,
            backgroundColor: ["#16a34aCC", "#0284c7CC", "#f59e0bCC", "#e11d48CC"],
            borderRadius: 8,
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => `Amount: GH₵ ${formatMoneySafe(ctx.parsed.y || 0)}`
              }
            }
          },
          scales: {
            x: { ticks: { color: "#64748b" }, grid: { color: "rgba(148,163,184,0.2)" } },
            y: { ticks: { color: "#64748b", callback: (val) => (val.toFixed ? val.toFixed(0) : val) }, grid: { display: false } }
          }
        }
      });
    } else {
      charts.apDue.data.datasets[0].data = vals;
      charts.apDue.update();
    }
  }

  function updateTopCustomers(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    if (!topCustomersList) return;
    if (!items.length) {
      topCustomersList.innerHTML = "<p class=\"text-xs text-gray-500 dark:text-gray-400\">No receivable data yet.</p>";
      return;
    }

    const rows = items.map((c) => (
      `<div class=\"flex items-center justify-between rounded-lg px-3 py-2 bg-gray-50/70 dark:bg-slate-900/60\">` +
        `<div class=\"flex flex-col\">` +
          `<span class=\"text-sm font-medium text-navy dark:text-white\">${escapeHtml(c.name)}</span>` +
          `<span class=\"text-xs text-gray-500 dark:text-gray-400\">Outstanding receivable</span>` +
        `</div>` +
        `<span class=\"text-sm font-semibold text-navy dark:text-gray-100\">GH₵ ${formatMoneySafe(c.outstanding)}</span>` +
      `</div>`
    ));
    topCustomersList.innerHTML = rows.join("");
  }

  function updateTopSuppliers(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    if (!topSuppliersList) return;
    if (!items.length) {
      topSuppliersList.innerHTML = "<p class=\"text-xs text-gray-500 dark:text-gray-400\">No payable data yet.</p>";
      return;
    }

    const rows = items.map((s) => (
      `<div class=\"flex items-center justify-between rounded-lg px-3 py-2 bg-gray-50/70 dark:bg-slate-900/60\">` +
        `<div class=\"flex flex-col\">` +
          `<span class=\"text-sm font-medium text-navy dark:text-white\">${escapeHtml(s.name)}</span>` +
          `<span class=\"text-xs text-gray-500 dark:text-gray-400\">Outstanding payable</span>` +
        `</div>` +
        `<span class=\"text-sm font-semibold text-navy dark:text-gray-100\">GH₵ ${formatMoneySafe(s.outstanding)}</span>` +
      `</div>`
    ));
    topSuppliersList.innerHTML = rows.join("");
  }

  function badgeClass(type) {
    if (type === "invoice") return "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-200";
    if (type === "payment") return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200";
    if (type === "bill") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200";
    return "bg-gray-100 text-gray-700 dark:bg-slate-800 dark:text-gray-200";
  }

  function badgeIcon(type) {
    if (type === "invoice") return "request_quote";
    if (type === "payment") return "payments";
    if (type === "bill") return "receipt_long";
    return "info";
  }

  function updateRecentActivity(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    if (recentCountEl) recentCountEl.textContent = items.length.toString();
    if (!recentActivityBody) return;

    if (!items.length) {
      recentActivityBody.innerHTML = "<tr><td colspan=\"4\" class=\"px-4 py-4 text-xs text-gray-500 dark:text-gray-400\">No recent events found.</td></tr>";
      return;
    }

    const rows = items.map((ev) => {
      const label = escapeHtml(ev.label);
      const type = escapeHtml(ev.type);
      const amount = ev.amount ? `GH₵ ${formatMoneySafe(ev.amount)}` : "&mdash;";
      const ts = ev.ts || "";
      const dateText = ts ? `${ts.slice(0, 10)} ${ts.slice(11, 16)}` : "--";

      return (
        `<tr class=\"hover:bg-gray-50/80 dark:hover:bg-slate-800/60\">` +
          `<td class=\"px-4 py-2\">` +
            `<span class=\"inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badgeClass(type)}\">` +
              `<span class=\"material-symbols-outlined text-xs\">${badgeIcon(type)}</span>` +
              `<span class=\"capitalize\">${type}</span>` +
            `</span>` +
          `</td>` +
          `<td class=\"px-4 py-2 text-gray-800 dark:text-gray-100\">${label}</td>` +
          `<td class=\"px-4 py-2 text-right text-gray-800 dark:text-gray-100\">${amount}</td>` +
          `<td class=\"px-4 py-2 text-right text-gray-500 dark:text-gray-400 text-xs\">${dateText}</td>` +
        `</tr>`
      );
    });

    recentActivityBody.innerHTML = rows.join("");
  }

  function loadAllSections(rangeKey) {
    const baseParams = new URLSearchParams({ range: rangeKey });
    const base = (apiBase || "/accounting/dashboard").replace(/\/+$/, "");

    fetchSection("kpis", `${base}/api/kpis?${baseParams}`, updateKpis);
    fetchSection("bank-cash", `${base}/api/bank-cash?${baseParams}`, updateBankCash);
    fetchSection("sales-expense", `${base}/api/sales-expense?${baseParams}`, updateSalesExpense);
    fetchSection("cash-flow", `${base}/api/cash-flow?${baseParams}`, updateCashFlow);
    fetchSection("ar-aging", `${base}/api/ar-aging?${baseParams}`, updateArAging);
    fetchSection("ap-due", `${base}/api/ap-due?${baseParams}`, updateApDue);
    fetchSection("top-customers", `${base}/api/top-customers?${baseParams}`, updateTopCustomers);
    fetchSection("top-suppliers", `${base}/api/top-suppliers?${baseParams}`, updateTopSuppliers);
    fetchSection("recent-activity", `${base}/api/recent-activity?${baseParams}`, updateRecentActivity);
  }

  if (rangeSelector) {
    rangeSelector.addEventListener("change", function(){
      const val = rangeSelector.value || "this_month";
      updateUrlRange(val);
      loadAllSections(val);
    });
  }

  if (window.formatAllNumbers) {
    window.formatAllNumbers();
  }

  const initialRange = rangeSelector ? (rangeSelector.value || "this_month") : "this_month";
  loadAllSections(initialRange);
})();
