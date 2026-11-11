# SmartLiving heavy-operations review

Date: 13 October 2026 (Africa/Accra). Investigation only; no application code, features, database indexes, deployment configuration, or production data changed. Only this report was created.

## Scope, evidence, and limits

Only the remaining SmartLiving project was scanned. `bigadom-main` is absent and was not inspected. The previous diagnostic report is also absent at `docs/performance-diagnostic-developer-review-2026-10-13.md` in the current workspace. Its findings from the conversation were the starting checklist; the findings below were checked against current source rather than assumed unchanged.

The scan covered Flask entrypoints, routes/services, customer-support and inventory React code, templates/static scripts, accounting and HR modules, startup/index declarations, and standalone utilities. High-risk paths were traced in detail. This is not a runtime certification of every endpoint or proof that every old utility is deployed. File/line references are relative to the remaining project root, as inspected on this date.

Production observations supplied by the user:

- `/api/customer-support/tasks` logged 583,338 bytes on each of 52 calls over approximately 20 minutes: 30,333,576 bytes, approximately **28.93 MiB**, of logged response bodies. Logged size is not necessarily compressed transfer size.
- Median spacing between completed calls was approximately 22 seconds. This is **not elapsed request time**, and does not prove a 22-second frontend timer.
- Manager deposits initiated `/agent/tasks/agent/tasks/count`, followed by login redirects.
- Paid Render service; CPU and memory had headroom. Neither resource exhaustion nor unusually heavy traffic is established.

**Measurement convention:** unless a finding explicitly says otherwise, its response size, production runtime, database execution time, candidate count, and concurrent-user count are **unmeasured**. Static operation counts are code-derived and can include extra MongoDB cursor `getMore` operations beyond the listed logical queries. Confidence distinguishes existence of the pattern from its contribution to the incident. No speed improvements in milliseconds or percentages are invented.

Safe checks: source searches and AST inspection, plus `python -B -m pytest -p no:cacheprovider tests/test_daily_progress.py tests/test_account_access.py tests/test_delivery_followups.py -q` with bytecode disabled. **47 tests passed**, using mocks/in-memory MongoDB; 93 existing datetime deprecation warnings. These are correctness checks, not performance benchmarks. No real app import connecting to production, production load test, index change, or authenticated production mutation was performed.

## Keep three latency paths separate

| Path | Work on the actual path | What cannot be concluded |
|---|---|---|
| Fresh anonymous GET `/login`, no cookies | Account-access hook exits when no identity exists; login obtains anonymous identity and renders form; constant/URL context processors | Bcrypt, IP geolocation, login logging, and dashboard aggregation do not directly execute here |
| GET `/login` with role session or remember cookie | Account/user restoration and linked-role queries can run; authenticated role session redirects to its dashboard | Browser final load time may include redirected dashboard; it is not necessarily form-render latency |
| POST `/login` | Candidate queries, password verification, possible migration/primary-profile lookup, role setup, external IP lookup, log insert, redirect | POST latency does not establish anonymous GET latency |
| Dashboard HTML and follow-up APIs | Shared identity/audit work, role-specific calculations, sidebar traffic, feature/insight fetches | HTML completion and later panel completion must be timed separately |

Source: `app.py:244` `load_user`, `app.py:254` `check_account_access`; `services/account_access.py:17` `enforce_account_access`; `login.py:188` `get_current_identity`, `login.py:321` `login`. The browser login geolocation timeout is separate from the server IP call; `templates/login.html:545` `gateLoginSubmit` immediately permits submission.

An anonymous login timeout remains unexplained. Shared request-thread waiting, startup/restarts, network/edge problems, or existing cookies are hypotheses requiring measurement, not verified causes.

## Findings

### H01 — Support task badge downloads the entire list and runs reconciliation

- **References/call chain:** `Frontend/Customer_Support/src/app/components/Layout.tsx:101` mounts Sidebar; `Frontend/Customer_Support/src/app/components/Sidebar.tsx:150–158` `loadCount` → GET `/api/customer-support/tasks` → `customer_support_backend/tickets_calls_api.py:730` `support_tasks` → `services/delivery_followups.py:55` `reconcile_delivery_followups` → `_task_rows` at `customer_support_backend/tickets_calls_api.py:290`, `_hydrate_tasks` at `:294`, `_task` at `:271` → JSON.
- **Trigger/frequency:** immediately and every 30 seconds in each mounted sidebar; no hidden-tab suspension, in-flight guard, request deadline, or cancellation in that effect. Cleanup removes its timer, so one mount does not itself prove a timer leak. TasksPage independently fetches the same list on mount/refresh (`Frontend/Customer_Support/src/app/components/TasksPage.tsx:29–40`) and after edits, creating overlapping sources. Multiple tabs multiply timers. A request exceeding the timer interval can overlap the next.
- **Cost/scale:** up to 200 missing-marker packages plus 200 closures inspected; eligible records cause per-record lookups/upserts/marker writes. Then all latest 1,000 tasks are loaded without source projection, up to two batched customer/user lookups, serialization, and four count passes. Task hydration is **batched, not N+1**. Observed body is 583,338 bytes; runtime unknown. Work scales with backlog, task/related-document sizes, and active tabs/users.
- **Treatment/dependencies:** reduce badge to a counts endpoint, paginate list, project summary fields, share client requests, and move historical repair to a controlled job. Keep overdue/status normalization, task scope, delivery task creation, retries, and idempotency. Removing reconciliation without preserving event-driven creation and a repair path can lose follow-ups for historical/interrupted events. Counts currently describe only 1,000 retrieved tasks; a replacement must define correct whole-scope counts.
- **Assessment:** confirmed unnecessary badge overhead; high confidence in source/log match, unmeasured incident attribution. **P1**, expected benefit: remove repeated large transfers and repair work from sidebar reads.

### H02 — Manager deposits uses the agent sidebar, causing redundant dashboard redirects

- **References/call chain:** `templates/manager/deposits.html:47` includes `side_bar.html`; `templates/side_bar.html:791–793` schedules `refreshAgentTaskCount`, defined at `:828`, fetch at `:832` uses `url_for('agent_tasks.pending_task_count')`. `app.py:389` registers prefix `/agent/tasks`; `agent_tasks.py:53–56` declares `/agent/tasks/count` and `pending_task_count` protected by Flask-Login.
- **Trigger/frequency:** immediate and every 120 seconds per deposits page/tab, including hidden tabs. The combined path is the actual registered **`/agent/tasks/agent/tasks/count`**. It is redundant path composition, not relative URL resolution or an inherently missing route.
- **Cost/scale:** `login.py:284` `_set_role_session` logs agents into Flask-Login but logs other roles out while storing role sessions. Manager badge requests fail `login_required` before the agent-role check, redirect to login; `login.py:321` recognizes the manager session and redirects to `/manager/dashboard`. Fetch normally follows redirects, downloads HTML, fails JSON parsing, then hides the badge. HTML can invoke dashboard calculations and audit writes. Previous logs showed manager dashboard body 107,982 bytes; no chain duration measured. Scales with deposits tabs and repeated mounts.
- **Treatment/dependencies:** use manager sidebar and an appropriate manager badge, if required; use JSON 401/403 on background reads. Correct redundant route composition only with a link/bookmark compatibility plan. Agent task permissions must remain intact. Do not remove agent tasks because a manager calls them incorrectly.
- **Assessment:** high confidence; both prefix redundancy and role mismatch confirmed. Normal navigation/login redirects remain legitimate; not every 302 is a defect and a single chain is not an endless loop. **P1**, expected benefit: eliminate avoidable redirect/render chains.

### H03 — Support notifications rebuild the same four-source event list twice per GET

- **References/call chain:** `Frontend/Customer_Support/src/app/components/TopNav.tsx:119–124` `loadNotifications` → `/api/customer-support/notifications?per_page=5`; `Frontend/Customer_Support/src/app/components/NotificationsPage.tsx:18–19` `load` polls the same endpoint and dispatches `customer-support-notification-count`; TopNav responds by fetching again. Backend `customer_support_backend/tickets_calls_api.py:1045` `support_notifications` calls `_notification_events` (`:916`) once for displayed events and again for totals/unread count.
- **Trigger/frequency:** TopNav immediate/30-second poll and notification events; NotificationsPage adds its own immediate/30-second fetch while open. Its count event triggers an additional TopNav fetch. This is a confirmed duplicate refresh chain, not an assumed React timer leak. No hidden-tab or in-flight protection in these effects.
- **Cost/scale:** `_notification_events` queries complaints (limit 150), tasks (150), follow-up calls (100), packages (150), derives events and sorts in Python. Two calls mean eight logical collection reads per GET, plus notification-state and shared authentication queries. `per_page=5` only shrinks the returned slice, not event-generation work. Earlier excerpt body was 1,649 bytes; that small response does not imply cheap processing. Scale is bounded candidate windows plus users/tabs, sort/query execution, and source document size.
- **Treatment/dependencies:** build once per request; derive counts and page from that result. Consume numeric count events rather than always refetching; share/cache coordinated refreshes. Preserve read IDs, read-all-before timestamps, event IDs, ordering, scope, and freshness. A cache must not mix users' read state.
- **Assessment:** high confidence; runtime/query-plan cost unknown. **P1**, expected benefit: remove demonstrably duplicate reads and event-triggered fetches.

### H04 — Accounting budget polling repeatedly reads historical payments

- **References/call chain:** `templates/accounting/budget.html:615` `refreshLive`, API URL at `:324`, timer at `:813` → `/accounting/budget/live` → `accounting_routes/budget.py:634` `budget_live` → `_build_budget_payload` at `:457` → `_sales_actual_period` at `:174` and `_monthly_sales_series` at `:184`.
- **Trigger/frequency:** every 30 seconds while budget page is open, plus user refresh/filter/mutation flows. The refresh has no in-flight or visibility guard. Initial page calculation also builds the payload.
- **Cost/scale:** both sales helpers query non-withdrawal payments without a date restriction in MongoDB, then parse/filter dates in Python. This is at least two complete matching historical-payment traversals per payload, plus budget/category/expense/trend/manager calculations. Projection reduces fields but not the broad history read. Small selected date ranges do not avoid traversing old payments. Runtime/body unknown; scales with payment history, other accounting data, and tabs.
- **Treatment/dependencies:** reduce polling, add visibility/in-flight controls, consolidate totals and monthly buckets in one bounded calculation, normalize legacy dates before pushing ranges into queries, and cache with invalidation. Keep accounting period boundaries, mixed-date fallback semantics, approved-expense rules, and correct budget comparisons. Removing “live” work changes freshness; agree on required delay.
- **Assessment:** high confidence in repeated historical reads; unmeasured magnitude. **P1**, expected benefit: avoid recurring broad scans and duplicated calculations.

### H05 — Global identity, template context, and HTML audit work

- **References/call chain:** any authenticated request → `app.py:254` account check → `services/account_access.py:17` → `login.py:134` `_find_identity_doc`; Flask-Login restoration → `app.py:244` → `user_model.py:56` `get_user_by_id`; role guard/template → `login.py:188` `get_current_identity` → `_linked_role_docs` at `:115`. Template processors: `routes/agent_sidebar.py:33` `inject_agent_sidebar_data`, `routes/manager_sidebar.py:44` `inject_manager_sidebar_data`, `app.py:588` `inject_identity`. After-response preparation: `app.py:581` `audit_mutations` → `services/activity_audit.py:246` `should_log_request` / `:68` `log_activity`.
- **Trigger/frequency:** authentication hook on every relevant request; processors on every template render, even if their sidebar is not included; audit insert on eligible successful HTML GETs and mutations. Login GET and JSON GET APIs are excluded from page-open auditing.
- **Cost/scale:** user lookup can repeat between hook, loader, identity helper, and view; linked-role helper separately queries group and username lists. `g` caches the identity helper only, not all earlier queries. Agent processor accepts `session.user_id` even for non-agent roles and can query complaints unnecessarily. Manager processor loads agents then counts complaints. Activity insert occurs synchronously before response leaves Flask; swallowed exceptions do not avoid waiting. Exact count depends on cookies/role/fallback state. Scales with requests and linked-role/agent lists.
- **Treatment/dependencies:** consolidate validated user data within the request, restrict role/page-specific processors, and measure audit spans. Keep lock/revocation behavior and audit durability; do not remove checks or use stale cross-request identity caching casually. If moving audit delivery, use durable retryable delivery rather than fire-and-forget threads.
- **Assessment:** high confidence in work; database latency unmeasured. **P1**, potential benefit across many authenticated pages.

### H06 — Executive modal content still fetches before it is opened

- **References/call chain:** `templates/executive_dashboard.html:733–753` embeds recruitment/personal/team content inside hidden Bootstrap modals. `static/js/recruitment_flow.js:68–76` `mountRecruitmentFlow` starts `overview` (`:14`) on DOM readiness and focus → `/api/recruitment/summary` → `hr_backend/branch_recruitment.py:158` `overview`. `static/js/work_time.js:70–79` `mountWorkTime` initializes personal/team roots even when hidden → `/attendance/api/summary` / `/attendance/api/analytics` → `routes/attendance.py:305` `personal_summary`, `:363` `work_time_analytics`.
- **Trigger/frequency:** page load/mount; personal/recruitment refresh on focus and domain events. Team analytics mounts once per root. Recruitment can also request summary for the navigation-count selector, so do not assume one recruitment request per page. These are not all interval polls.
- **Cost/scale:** recruitment counts stages separately and reads staff/recruit notifications before returning up to 50 notifications. Team analytics loads period attendance records, all eligible users, calculates summaries, and serializes **employees, records, leaves, settings** although the compact card uses only totals. It already groups attendance records once; it is not one attendance query per user. Work scales with staff, days/leaves, recruits, and attendance rows. Size/runtime unknown.
- **Treatment/dependencies:** defer fetching until a modal opens; use a totals-only summary for compact staff card; preserve badge requirements separately, refresh on meaningful events/staleness. Keep recruitment approval/notification and attendance/leave capabilities accessible. Simply moving HTML into a modal did not make data loading lazy.
- **Assessment:** high confidence; feature usage rates unknown. **P1**, benefit: avoid unused initial dashboard work without removing features.

### H07 — Agent target calculations duplicate period work and compute for unallocated agents

- **References/call chain:** `/dashboard/agent` → `dashboard_agent.py:314` `agent_dashboard` → `calculate_agent_targets` at `:175`, then four calls to `routes/executive_target.py:272` `compute_agent_target_progress` for daily/weekly/monthly/yearly → aggregation helpers at `:118`, `:135`, `:152`.
- **Trigger/frequency:** every agent dashboard render/refresh, before HTML. Legacy calculation aggregates inside its target loop; executive progress performs active-target lookup and three aggregates **before** finding the agent allocation.
- **Cost/scale:** up to sixteen logical operations in the four-active-period loop alone, even when the agent lacks allocation. Other user/task/commission/account-money/today-total queries are additional. Matching payments/customer purchase arrays and target history determine execution cost. Body/runtime unknown; scales with target count, periods, payments, purchases, and agent visits.
- **Treatment/dependencies:** check allocation first, fetch active targets together, consolidate compatible aggregates and reuse calculated totals. Keep period boundaries, quotas, commissions, exclusions, legacy target behavior, and mixed ID types. Do not remove long-period reports simply to reduce work.
- **Assessment:** high confidence; active targets/data sizes unknown. **P1**, benefit: remove needless aggregates and repeated reads.

### H08 — Manager dashboard queries payments separately for every agent

- **References/call chain:** `/manager/dashboard` → `manager_dashboard.py:353` `manager_dashboard_view` → `_daily_target_context` at `:68`, four period progress calls (`:375`), `calculate_monthly_targets` at `:265`, agent loop at `:395` → `payments.find` at `:398` → Python totals/count/customer set.
- **Trigger/frequency:** every page render, including background redirect destinations from H02. Lazy expense panels do not eliminate this initial work.
- **Cost/scale:** one payment query per agent, after overlapping daily/period totals were already calculated; payments are iterated rather than only grouped results returned. Other agents/targets/count queries are additional. Earlier observed HTML 107,982 bytes; runtime unknown. Scales with agents and today's payments, target periods and visits.
- **Treatment/dependencies:** aggregate all relevant agent IDs once, reuse totals/attendance results, consolidate periods/monthly metrics. Keep payment exclusions, distinct attended customers, per-agent activity/attendance semantics, and manager scope.
- **Assessment:** high confidence N+1-style per-agent read pattern. **P1**, benefit: fewer network round trips and repeated payment processing.

### H09 — Executive dashboard initial and insight computations remain broad

- **References/call chain:** `/executive/dashboard` → `executive_dashboard.py:464` `executive_dashboard`: estimated count plus purchase-array-size sum; template insight fetches at `templates/executive_dashboard.html:910`, product fetch at `:1147` → `executive_dashboard.py:1229` `dashboard_insights`, `:1624` `dashboard_products_insights`. Live-page APIs at `:498` `executive_today_live_data`, `:1052` `executive_today_live_events` are separate.
- **Trigger/frequency:** dashboard entry and range changes; many panel APIs fire on initial render. Live page summary every 60 seconds, event poll every 12 seconds (`templates/executive_today_live.html:1206–1207`), plus focus/filter/event refreshes.
- **Cost/scale:** initial all-qualifying purchase-array sum; insights issue multiple sales/customer/expense/trend/leaderboard aggregates. No broad cache decorator on these primary handlers. Work scales with selected data/history and users. Runtime/body unknown. Live summary has `isFetching` guard (`:981`) and visibility checks; event poll checks hidden state (`:1118`). Do not mislabel that page as entirely unguarded.
- **Treatment/dependencies:** measure API spans separately, consolidate repeated current/comparison totals, short scoped caches or maintained summaries where warranted, cap event responses and review overlapping event polls. Keep live freshness, charts, filter/date/timezone semantics, and branch authorization.
- **Assessment:** high confidence in computation, unknown dominant query. **P2**, potentially substantial data-dependent benefit.

### H10 — Support performance is repeatedly recomputed by more than one widget

- **References/call chain:** `Frontend/Customer_Support/src/app/components/PerformanceSummary.tsx:12` 30-second timer plus three domain events; `Frontend/Customer_Support/src/app/components/TargetsPage.tsx:20–35` date-dependent fetch/30-second refresh → `/api/customer-support/targets` → `customer_support_backend/targets_api.py:142` `progress`, `:95` `performance`, `:78` `active_targets`.
- **Trigger/frequency:** widget/page mount, every 30 seconds while mounted, events/date change. AbortControllers exist, but abort support alone does not serialize interval/event fetches or pause hidden tabs. Whether both widgets coexist on a specific screen needs Network Initiator verification.
- **Cost/scale:** target/version and weekly-visit reads, batch interaction counts, target-specific counts, and daily/weekly `performance` counts. Repeat-purchase tasks are read by creator and their dates filtered in Python at `:120`, repeating history work for two periods. Visiting no-target states still requests performance. Scales with creator history, calls, tasks, target count and visits. Runtime/body unknown.
- **Treatment/dependencies:** share progress snapshots; fetch only needed sections; consolidate period metrics, server-filter relevant dates with legacy compatibility, pause/deduplicate refresh. Keep historical effective target versions, interaction counts, follow-up assignments, genuine purchased evidence, and per-officer scope.
- **Assessment:** high confidence repeated work; simultaneous widget use/data volumes unverified. **P2**.

### H11 — Other support reads reconcile or traverse all packages despite pagination

- **References/call chain:** DeliveriesPage `load` (`Frontend/Customer_Support/src/app/components/DeliveriesPage.tsx:35–43`) → `customer_support_backend/tickets_calls_api.py:810` `support_deliveries`: paginated packages, per-row `ensure_delivery_followup`, batch follow-up attachment, then **all** package status/branch rows for global counts. `_submitted_card` at `:307` can look up a product image per row when absent. Follow-up queue `Frontend/Customer_Support/src/app/components/DeliveryFollowup.tsx:71` GET `/api/customer-support/delivery-followups` → `customer_support_backend/delivery_reviews_api.py:57` `followup_queue` → reconciliation, then count and paginated tasks.
- **Trigger/frequency:** deliveries entry/filter/page/search changes with 250 ms debounce; follow-up queue navigation. No evidence these list reads are all 30-second timers. Repeated calls can repeat repairs.
- **Cost/scale:** displayed rows are capped but global package counts/branches load full projected package collection each request. Reconciliation as H01; optional image lookups per displayed card. Queue pagination is real and should be retained. Runtime/body unknown; scales with total packages/backlog/page rows and navigation.
- **Treatment/dependencies:** separate repair job/event creation, aggregate/cache global counts and branch directory, batch image fallback. Preserve missing historical follow-up repair, delivery/satisfaction links, queue status and global-versus-filtered count semantics.
- **Assessment:** high confidence; backlog/image-miss frequency unknown. **P2**.

### H12 — Inventory role retrieval performs provisioning and bulk normalization writes

- **References/call chain:** inventory App session bootstrap (`Frontend/Inventory_V2/src/app/App.tsx:476`) → `/api/inventory/session` → `Backend/inventory/dashboard_api.py:2691` `session_info` → effective role/role map → `Backend/inventory/settings_store.py:219` `get_inventory_roles` → `:142` `ensure_default_inventory_roles`. Settings bootstrap (`Frontend/Inventory_V2/src/app/components/Settings.tsx:212`, backend `Backend/inventory/settings_api.py:87`) requests roles and users, whose role-map lookup can invoke the same provisioning again.
- **Trigger/frequency:** session/bootstrap and callers of effective role/map when not supplied a map; per request/caller, not a configured background timer.
- **Cost/scale:** deletes roles outside managed IDs; upserts defaults; `users.update_many` normalizes missing/invalid inventory roles at `Backend/inventory/settings_store.py:161`. Then role list query. Even when no changes occur, write/query commands are sent. Scales with calls and affected inventory users. Runtime unknown; not necessarily expensive on small data, but reads carry business mutations.
- **Treatment/dependencies:** move provisioning/legacy normalization to explicit startup migration or controlled administration, reuse role map within request; keep defensive permission defaults in the read path. Removing provisioning abruptly may leave existing/new users without expected roles; normalization may encode policy, not disposable cleanup. Preserve executive/main-admin overrides and assigned permissions.
- **Assessment:** high confidence in side effects; frequency/affected records unknown. **P2**, benefit: predictable read-only bootstrap and less repeated setup.

### H13 — Inventory and supplier pagination does not always bound database work

- **References/call chain:** `Frontend/Inventory_V2/src/app/components/Inventory.tsx:155` fetch → `/api/inventory/products` → `Backend/inventory/dashboard_api.py:2717` `inventory_products` → distribution payload plus `Backend/inventory/products_store.py:1361` `list_inventory_products` loads/sorts/serializes every product. Supplier page `Frontend/Inventory_V2/src/app/components/SuppliersPurchases.tsx:349` fetch → `/api/inventory/suppliers/tab/<tab>` → `Backend/inventory/suppliers_api.py:658` `suppliers_tab_page` → `_tab_records` at `:638` → fetch/serialize tab records, Python status/search filtering, then slice ten rows. `list_inventory_suppliers` at `:705` uses `_build_suppliers_payload` at `:367` for complete supplier/GRN payloads.
- **Trigger/frequency:** inventory/supplier navigation, tab/filter/page changes and mutation refreshes. No blanket periodic interval established. A page-size parameter does not bound these backend source reads.
- **Cost/scale:** inventory list unbounded; supplier reads grow with suppliers/orders/receipts/cost updates, including nested line items. Python pagination shrinks JSON only after loading work. Runtime/body unmeasured.
- **Treatment/dependencies:** server pagination/projection, typed search/filter queries, summaries distinct from detail/receipt items, cache stable branch/location metadata. Preserve nested-item search, totals, procurement approval, funding/receipts, and location filters. Do not replace full nested search with a narrower query unnoticed.
- **Assessment:** high confidence; size/material impact unknown. **P2**.

### H14 — Paginated customer pages still calculate account-wide metrics; old lists have N+1 reads

- **References/call chain:** `/view/customers` → `view.py:182` `_build_customer_listing` loads all agent customer IDs and aggregates latest product-payment dates before slicing displayed customers (`:299`); page payment totals are batched. Manager/admin customer AJAX paths `customers.py:504` `customers_data`, `:943` `admin_customers_data` have additional filter/totals logic and Python payment traversals. Agent task link in `templates/side_bar.html:482` → `agent_tasks.py:16` `view_tasks` loads matching tasks then queries customers/senders per task (`:31`).
- **Trigger/frequency:** each list entry/search/filter/page change. Earlier `/view/customers` bodies included 97,045 bytes and a searched 58,689 bytes; no corresponding runtimes.
- **Cost/scale:** pagination does not isolate all summary work. Case-insensitive substring regex can remain costly even with standard field indexes. Agent task list is unbounded and genuinely has N+1 related-document reads, unlike support task hydration. Scales with customer/payment history or matching tasks.
- **Treatment/dependencies:** measure summary spans, maintain/cache activity-state summaries where appropriate, batch task relations, paginate task history; use stable searchable normalized fields after compatibility review. Keep favorites, active/no-payment status, lead/customer stages, last-payment definitions and sender/customer display.
- **Assessment:** high confidence in patterns; endpoint plans/cardinality unknown. **P2**.

### H15 — Accounting dashboard fan-out overlaps broad financial calculations despite existing caches

- **References/call chain:** `static/js/accounting-dashboard.js:623–633` fires nine section fetches → `accounting_routes/dashboard.py:1074` `accounting_dashboard_kpis`, `:1092` bank/cash, `:1182` top customers, `:1200` top suppliers, and other sections. `_compute_kpis` at `:1040` recomputes bank/cash, AR/AP, period sales/expenses, net assets and year-to-date `compute_profit_loss`. Separate sections recompute some of those data sources.
- **Trigger/frequency:** dashboard entry/range changes and retry/refresh flows; **not confirmed to be a periodic dashboard timer**. Individual handlers already cache section payloads for 30–180 seconds and log elapsed times; cache misses can occur concurrently and per worker.
- **Cost/scale:** financial helper loops traverse matching payment/invoice/bill/expense/asset collections; separate caches do not automatically share intermediate computations. Profit/loss KPI uses current-year dates even when another dashboard range is selected: validate intended semantics before altering. Body/runtime unknown; existing `dashboard.api.*` log timings are valuable missing evidence. Scales with financial history, accounts and cache-miss concurrency.
- **Treatment/dependencies:** retain caching, reuse intermediate snapshots or consolidated grouped queries, avoid simultaneous duplicate cold-cache work, profile before rewriting financial calculations. Keep outstanding balances, aging, expenses, net profit and period rules; these are essential business computations, not candidates for feature deletion.
- **Assessment:** high confidence overlap, unknown cache effectiveness. **P2/measure first**.

### H16 — Sidebar badges multiply background reads across role pages

- **References/call chain:** executive sidebar `templates/executive_sidebar.html:429–433` schedules five refreshes → deposits `executive_deposits.py:267` `unconfirmed_count`, unresolved complaints `routes/complaints.py:926`, issues `routes/issues.py:787`, due undelivered `routes/undelivered_items.py:371`, and expense count (URL in sidebar `:452–455`). Manager sidebar `:811–815`: five 120-second polls; inventory sidebar `:342–346`: four 120-second polls plus supplier at 180 seconds; HR shell `:761–763`: three 45-second polls; admin sidebar `:362–366`: three 120-second polls.
- **Trigger/frequency:** immediate loads plus independent intervals per rendered role sidebar/tab. Visibility listeners typically refresh on returning, rather than suspending interval requests; inspect each before treating all as identical. Shared authentication overhead precedes these reads.
- **Cost/scale:** usually small count responses, not demonstrated huge payloads, but multiple requests and identity/count queries. Five executive timers imply up to ten scheduled badge requests/minute per sidebar, before events/other traffic; this is a code rate, not observed production throughput. Counts may traverse matching records depending on indexes. Runtime unknown.
- **Treatment/dependencies:** coordinate badge snapshot requests, use correctly scoped short caches, pause hidden tabs, avoid overlapping calls, agree on freshness. Keep urgent approvals/issues/delivery notifications; removal could hide operational work.
- **Assessment:** high confidence timers, unknown incidence/dominant query. **P2**, reduce unnecessary background work without calling it proven “heavy traffic.”

### H17 — Chat rereads whole conversations and writes on GET; unread poll every five seconds

- **References/call chain:** `templates/chat.html:256` fetch selected conversation → `chat.py:42` `get_messages`: loads complete two-way message history, sorts, calls `update_many` to mark received messages read, serializes history. `templates/chat.html:296–308` polls `/unread_counts` every 5 seconds → `chat.py:90` `unread_counts` / `:96` `get_unread_count_map`. Chat page `chat.py:22` loads other users and sorts in Python.
- **Trigger/frequency:** conversation selection and after send; no continuous conversation-history polling verified. Unread counts every five seconds without visibility/in-flight guard.
- **Cost/scale:** user lookup plus unread aggregate per poll; complete history body and read-state write when fetching conversation. Scales with users, unread messages, conversation length, tabs. Runtime/body unknown. Read-state mutation is intentional business work, not accidental repair.
- **Treatment/dependencies:** paginate/cursor conversation history, incremental after-cursor updates, visibility-aware counts, explicit read acknowledgments if restructuring GET. Preserve delivery/read status, order, unseen count correctness and access scope. Do not remove read marking without equivalent acknowledgement.
- **Assessment:** high confidence; chat utilization/history unknown. **P2/P3** based on measured usage.

### H18 — Login POST performs external enrichment and synchronous logging

- **References/call chain:** login form → `login.py:321` `login`: live/Exited candidate queries and sort, bcrypt checks per candidate, legacy rounds-12 migration, optional primary lookup, `_set_role_session` at `:284`, `get_location` at `:37` (call at `:430`) → HTTP ip-api.com `timeout=5`, device parsing, login-log insert → redirect to dashboard.
- **Trigger/frequency:** authentication attempts; external location only after successful verification. Multiple candidate profiles or invalid passwords can cause multiple bcrypt checks. None of this executes directly in fresh anonymous GET.
- **Cost/scale:** candidate count/hash cost; external wait; log write; destination dashboard separately. Request timeout is connection/read behavior, not a precise total deadline. Body/runtime unmeasured. Essential verification/account flags/audit versus optional synchronous IP enrichment.
- **Treatment/dependencies:** measure spans, asynchronously enrich/cached IP data, preserve reliable audit, account revocation and password strength. Do not lower bcrypt rounds or remove Exited/linked-role eligibility to mask delay. Keep location semantics if operations rely on them.
- **Assessment:** high confidence direct POST delay opportunity; external wait magnitude unknown. **P1 for sign-in**, cannot explain anonymous GET by itself.

### H19 — Exports, PDF generation, uploads and external business calls occupy request capacity

- **References/call chain:** authorized GET `/customers/export/all.pdf` → `customers.py:1528` `export_all_customers_pdf` loads all customer summaries, aggregates last payments (`:1547`), constructs ReportLab table/PDF (`:1588`). Inventory detail export `Frontend/Inventory_V2/src/app/components/InventoryDetail.tsx:416` → `Backend/inventory/dashboard_api.py:3063` `inventory_product_detail_export`. Upload route `add_inventory.py:136` `upload_inventory_image` makes provider calls (`:151`, `:163`) with 20/60-second timeouts; `product_profile.py:47` `upload_to_cloudflare` uses 25/60 seconds. Payment flow `payment.py:282` `add_payment`: provider request `:480` timeout 25, SMS `:661` timeout 12; OTP `:788` timeout 25. AI user send `static/js/executive-ai-assistant.js:14` endpoint → `Backend/ai_assistant/routes.py:128` `ai_chat` → `Backend/ai_assistant/deepseek_client.py:83` `_request` at `:99`, effective timeout at least 45 seconds.
- **Trigger/frequency:** explicit user actions/downloads, not automatically on every login GET. Use actual access logs to determine concurrency. Large PDFs can use CPU/RAM transiently; API/upload calls occupy threads while CPU stays low.
- **Cost/scale:** export memory/query/render cost grows with customers/payments/report rows; uploads with file/provider latency; AI with requested analysis/provider latency. Runtime/size unknown.
- **Treatment/dependencies:** keep, bound export scope, paginate/query in batches, use controlled durable export jobs and provider budgets where measured. Payments, notifications, stock evidence and audit correctness are essential. Do not retry financial writes blindly or silently drop SMS/upload failures. File uploads require validation and completion feedback.
- **Assessment:** high confidence synchronous work; incident involvement unverified. **P3/measure first**, promote if concurrency/spans implicate it.

### H20 — Import setup and process-local background jobs complicate startup and waiting

- **References/call chain:** Render starts app → `app.py` many route imports → `db.py:19` shared MongoClient, `:32` immediate ping, import/registration index checks e.g. `executive_dashboard.py:39` `_ensure_exec_indexes`, `manager_dashboard.py:18` `_ensure_indexes`, `services/login_audit.py:12/22`. Supplier request `_ensure_indexes` (`Backend/inventory/suppliers_api.py:30`) has a per-process `_indexes_ready` guard: it does **not** issue index commands on every subsequent request, although calls appear in many handlers. Failure is swallowed and readiness set true, so “ready” does not prove success.
- **Trigger/frequency:** process import/restart; supplier first use per process. Existing index checks require commands but do not imply rebuilding all indexes. Historical thread jobs: `inventory_products.py:1804` `stock_close` launches daemon thread (`:1849`); `:1898` `start_inventory_closing` launches `:1936` thread → `_process_closing_job` (`:612`) with per-target reads/writes. `templates/inventory_products.html:1701` polls stock-close status every 700 ms until terminal status. Status reads are separate HTTP work; thread work continues outside request threads in the same process.
- **Cost/scale:** import/setup networking on restarts; jobs grow with products/closing targets. Short status intervals multiply reads while jobs run. No general durable job queue identified. No measured startup duration, queue length or concurrent jobs.
- **Treatment/dependencies:** explicit observable index provisioning after compatibility review; durable job ownership/retry/concurrency limits for closing; adaptive visibility-aware status polling. Keep closing snapshots, transactional integrity, recovery and progress visibility. A worker restart can abandon daemon work; moving jobs requires idempotency and state recovery.
- **Assessment:** high confidence architecture; startup/closing activity during slowness unverified. **P2/P3**, not evidence that paid hosting sleeps.

## Server and connection interpretation

`Procfile:1` specifies `gunicorn app:app --workers 2 --threads 4 --timeout 120`. Render's effective Start Command and environment overrides have not been verified. If effective, sync selection with multiple threads uses **gthread**, nominally two processes/four request threads each. Individual blocking calls can occupy threads; one blocked request does not necessarily block all users. Enough simultaneous long waits can queue unrelated requests despite low CPU. Gunicorn's timeout is worker-silence handling, not a reliable per-request deadline for threaded workers. [Gunicorn settings](https://gunicorn.org/reference/settings/), [Render Start Command](https://render.com/docs/web-services).

`db.py:19` shares a module-level client per process with defaults maxPoolSize 100, minPoolSize 0, connection timeout 10 seconds, socket timeout 30 seconds, server selection timeout 10 seconds, retries enabled. No explicit wait-queue timeout is set there. Pool defaults and eight nominal request threads alone do not prove pool exhaustion. Client/server pools are separate per process/server and open on demand. [PyMongo pools](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-options/connection-pools/).

Render-to-Atlas region/round-trip latency, MongoDB plans/cardinality, retry behavior, and checkout waits are unmeasured. No index existence or absence is inferred from declarations. Some declarations swallow failures; verify catalog/representative bounded execution plans before proposing index changes. Do not increase worker processes without measuring per-worker RSS and transient use against the 512 MB limit; first reduce unnecessary work and identify wait destinations.

## Cross-application observations and exclusions

- `app.py:535` `inventory_react_app` and `:554` `operations_management_react_app` check full identity before serving each SPA asset; global account checks also run for authenticated static requests. Asset request fan-out can add role/profile lookups. Consider independent delivery of genuinely public hashed assets later; protect data/business routes. Deployed cache/compression headers and bundle sizes remain unknown.
- Login external CSS/fonts/images can delay browser paint independently of HTML TTFB (`templates/login.html:11–20`). No new browser waterfall was measured. Package dependency lists do not prove oversized deployed bundles; support Layout already lazy-loads feature pages.
- `services/work_time.py:68` summary calculates scheduled days and leave ranges; whole-staff analytics is real business work. It is the **placement/response scope** on unopened dashboard modals that is unnecessary, not attendance reporting itself.
- `accounting_routes/prepayments.py:121` `recognition_generate` invokes candidate generation from **POST**, not from the recognition-page GET. The GET loads queue rows; do not claim it automatically posts accounting recognition. Likewise, `services/profit_loss_service.py` backfill utility definitions alone do not prove live requests run them.
- Executive user-list aggregates (`executive_users.py:288` `_list_users_with_metrics`) iterate **aggregate results for page IDs**, not a DB query per user. Their list pagination/projection/batch aggregation are useful existing patterns; raw “query inside loop” search results need review, not automatic N+1 classification.
- Activation runtime one-second timers (`templates/activations_admin_list.html:156`, `templates/activations_admin_detail.html:273`), call recording animation (`Frontend/Customer_Support/src/app/components/CallDetails.tsx:70`), and private-ledger countdown (`templates/accounting/private_ledger.html:675`) update local display state, rather than necessarily fetching every tick. They are not counted as background API traffic. The call animation interval lifecycle could deserve UI review, but is not a confirmed backend load source.
- `view_agents.py` constructs a localhost MongoClient but is not the `agent.py` blueprint imported by `app.py`. `export_data.py:86` runs its PDF generator only under `__main__`. These are **unused candidates**, not confirmed runtime overhead; standalone operations may still use them. No feature/module is recommended for deletion on naming alone.
- Cleaned logs/cache files are absent; deleting such files does not fix query/runtime latency. Built frontend dist assets are unavailable locally, so deployed build weight cannot be measured from this tree.

## Ranked table: heaviest or most wasteful candidates

Ranking reflects demonstrated waste, breadth, and potential work growth—not measured request-duration order. H01, H03, H04 and H06 are especially concrete opportunities; H05 is broad but needs span attribution.

| Rank | Finding | Trigger | Main scaling factor | Treatment | Expected benefit | Confidence / evidence gap |
|---|---|---|---|---|---|---|
| 1 | H01 full tasks for badge + repairs | 30 s/tab + list entry | tasks/backlog/tabs | Counts endpoint, pagination, repair job | Remove repeated 570 KiB bodies and repairs from badge path | High; runtime/backlog unknown |
| 2 | H02 wrong-role sidebar redirects | Entry + 120 s | deposits tabs/dashboard cost | Correct sidebar/API contract | Stop redundant login/dashboard renders | High; count of attributable chains unknown |
| 3 | H03 duplicate notification reconstruction/events | 30 s + count events | 4 source windows/tabs | Reuse event snapshot, coordinate client | Fewer duplicate reads/requests | High; plans/runtime unknown |
| 4 | H04 accounting budget historical reads | 30 s + refresh | historical payments | Consolidate, bounded queries, caching | Reduce broad recurring history traversal | High; history volume unknown |
| 5 | H05 shared identity/context/audit | Most authenticated pages | requests/roles/agents | Reuse per-request data; scope processors | Reduce cross-page DB waits | High pattern; spans needed |
| 6 | H06 unopened executive modal fetches | Initial load/focus | staff/recruits/attendance | Lazy fetch, totals-only API | Avoid work for unopened features | High; modal usage unknown |
| 7 | H07/H08 agent/manager dashboards | Each render | periods/agents/payments | Consolidate aggregate work | Fewer repeated round trips | High; execution time unknown |
| 8 | H18 sign-in location enrichment | Successful POST | provider/database latency | Decouple enrichment; keep audit | Remove optional external wait from login POST | High; no GET attribution |
| 9 | H11/H13 delivery/inventory/supplier lists | Navigation/filter | total packages/products/orders | True server pagination; summary cache | Bound reads and reduce serialization | High; current sizes unknown |
| 10 | H10 repeated support performance | 30 s/events | officer history/periods | Share/consolidate progress | Avoid duplicate metric calculations | High; mounted-widget concurrency unknown |
| 11 | H12 role provisioning on reads | Session/settings/bootstrap | caller count/affected users | Controlled migration and role-map reuse | Remove repeated setup writes from reads | High; affected count unknown |
| 12 | H09/H15 executive/accounting insights | Entry/range/cache misses | business history | Profile, consolidate, scope caches | Less redundant financial/report work | High; existing cache hit rates unknown |
| 13 | H14/H17 customer/task/chat history | Page/search/chat | history size | Paginate, batch, summary reuse | Bound growth and history responses | High; utilization unknown |
| 14 | H16 many role badge timers | 30–180 s | tabs/count queries | Coordinate visibility-aware snapshots | Less idle background traffic | High; not proof of overload |
| 15 | H19/H20 external/export/closing/startup | Explicit action/restart | export/job size/remote waits | Controlled jobs and budgets where measured | Predictable concurrency/recovery | High architecture; incident involvement unknown |

## Proposed first batch for review — not implemented

1. Add sampled request/DB/external/audit timing and confirm deployed Start Command/revision. Record endpoints, role without personal data, bytes, worker PID, duration, operation counts and pool checkout waits. Avoid sensitive query/body logging.
2. Correct manager deposits sidebar usage; make background authorization return JSON. Preserve task permissions and old-link compatibility if route paths change.
3. Split support counts from paginated task details; take historical reconciliation off recurring reads while retaining event-driven creation and a controlled repair job. Define status/overdue/scope/count semantics before rollout.
4. Reuse the notification event list once per GET and consume count-change events without redundant TopNav fetches. Add one coordinated, visibility-aware, non-overlapping refresh mechanism for support shell reads.
5. Defer executive recruitment/attendance/staff API work until modal open; retain independently required notification badges. Use totals-only staff data for the compact modal card.
6. Profile budget polling and shared identity/audit spans, then choose bounded/consolidated data reads and freshness rules. Do not rewrite financial logic or reduce security checks based only on static suspicion.

Business validation: regression-check delivery task creation/repair, overdue transitions, unread/read-all state, role switches and revocation, staff analytics totals, archived logins, dashboard period totals and payment exclusions. Existing 47 fixture checks are a starting set, not complete implementation coverage. No proposed feature removal in this batch.

## Measurement required before deciding

- Anonymous login: incognito/no-cookie GET with redirect following disabled; measure server handling versus edge/queue/network and asset waterfall. Repeat authenticated-cookie GET separately. Do not invoke production POST just to gather benchmarks without an authorized test account.
- Request durations: access logs with elapsed duration, middleware/view/template/after-request spans, median/p95 and sample sizes. Completed-call gaps cannot substitute.
- Background ownership: browser Network Initiator and deployed bundle revision, tabs, visibility state, route transitions and in-flight requests; explain 22-second task completion spacing without guessing timer values.
- MongoDB: Render-region round trip, Atlas region/tier, query execution/keys/docs examined, index catalog, cursor batches, pool checkout waits, retries and query deadlines. Use bounded representative diagnostics, not broad production load tests.
- Repair backlog: counts/types of missing-marker packages/closures, invalid candidates revisited, repair writes per read, concurrency/idempotency. Required before relocating or stopping repair.
- Server: active gthread requests and waiting destinations, startup/worker restarts, per-worker memory/headroom and job concurrency. Low CPU/RAM alone neither proves nor excludes request waiting.
- Financial workloads: budget date/schema compatibility, historical payment counts, accounting cache hits and `dashboard.api.*` timing logs; accurate financial totals take precedence over cosmetic speed gains.
- Bodies/assets: actual compressed wire bytes, response JSON fields, page-specific rows, JS/CSS build sizes/cache headers, browser long tasks and HTML versus panel timings. Only observed task/log sizes are currently quantified.
- Feature utilization: unopened modal rates, chat/export usage, legacy route consumers and operational scripts. Confirm usage and dependencies before recommending removal.

## Short summary to send to ChatGPT

> We reviewed only the remaining SmartLiving source and made no application or production changes. Confirmed waste includes full task-list polling with synchronous repair work, an agent sidebar on manager deposits causing login/dashboard redirect chains, notification events rebuilt twice per GET plus redundant client refreshes, historical-payment traversals on 30-second budget polls, and API loading for unopened executive modals. Shared identity/context/audit work and repeated agent/manager dashboard aggregates can amplify database waits. Some inventory reads also provision roles, and supplier pagination occurs after loading entire source lists. Forty-seven isolated business checks passed; they are not performance timings. Paid Render CPU/RAM headroom does not establish overload, and anonymous login GET is separate from POST geolocation/password/logging. Proposed first work is timing instrumentation, correct-role sidebar/count APIs, pagination and controlled repair jobs, coordinated notification polling, and lazy modal fetches. Actual request/DB/queue durations, deployed settings, index plans and browser waterfalls remain needed before selecting broader changes. No changes have been implemented; review the report first.
