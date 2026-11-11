# SmartLiving performance implementation report

**Date:** 2026-10-13  
**Scope:** implementation of the approved items from `heavy-operations-review.md`, using local isolated data only. No production data, Render configuration, deployment, or MongoDB indexes were changed.

## Result

The approved application changes are implemented and the final offline suite passed: **351 passed in 322.21 seconds**. The suite blocks external network access (except local fixtures), so this result verifies application behaviour without contacting Render or production MongoDB.

The work removes frequent full support-task downloads from the current customer-support UI, makes task-related polling visibility-aware and non-overlapping, fixes the duplicated agent task-count URL, moves login IP enrichment out of the login request, removes recurring delivery reconciliation from read paths, and makes production index changes explicit and reviewable.

It does not prove a particular production response-time improvement. That needs Render request timing and MongoDB profiling after deployment.

## Completed changes

| Review item | Status | Implementation |
|---|---|---|
| H01 support task payload and polling | Fixed | `services/support_task_queries.py` provides filtered, paginated task data and compact counts. `tickets_calls_api.py` exposes `/api/customer-support/tasks/counts`; the customer-support React app now uses paged data. `polling.ts` prevents overlap, pauses in hidden tabs, backs off after failure, and cleans up timers. |
| H02 duplicated agent task count URL | Fixed | `agent_tasks.py` uses canonical `/agent/tasks/count`; the legacy doubled route is retained only for old bookmarks. Manager deposits now uses the manager sidebar instead of the agent endpoint. API/count unauthenticated requests return JSON rather than login HTML. |
| H03 login enrichment | Fixed | `login.py` records successful-login audit data with pending enrichment and does not synchronously call geolocation. `services/login_enrichment.py` and `scripts/enrich_login_locations.py` process leased, bounded retry work. |
| H04 repeated notification work | Fixed | Customer-support notification reads build the event list once per request and reuse it for total, unread, and filtered output. |
| H05 request identity, audit, and assets | Partial | Request identity is memoized, account checks/audits are timed, and hashed public React assets bypass unnecessary authorization with long-lived caching. First-request session work and audit persistence remain required business work. |
| H06 executive attendance/recruitment modals | Fixed | Hidden modal roots no longer fetch on page load. Attendance supports `summary_only=1`; detailed work begins when the modal opens. |
| H07 target allocations | Fixed | Agent allocation lookup is performed before the target aggregate, and request memoization is used for safe repeated reads. |
| H08 manager dashboard payment lookups | Fixed | Per-agent payment reads were replaced by a grouped aggregation. |
| H09 executive dashboard breadth | Partial | The most immediate hidden-modal work was removed. Wider executive aggregates remain because their business requirements and freshness rules need production measurement before consolidation. |
| H10 budget sales snapshots | Fixed | Budget period and monthly views share one request-scoped sales snapshot. |
| H11 delivery reconciliation/read paths | Fixed | Recurring follow-up repair is removed from read routes. `services/delivery_followups.py` now supplies bounded, idempotent repair, and `scripts/repair_delivery_followups.py` runs it deliberately. Delivery views also batch product-image and count work. |
| H12 inventory role provisioning | Fixed | Inventory role reads no longer provision/write. `scripts/initialize_inventory_roles.py` performs explicit initialization. |
| H13 inventory and supplier lists | Partial | Products and direct supplier/procurement tabs use database pagination. Legacy nested-field supplier search remains compatible because its broad serialized-field semantics need a reviewed indexed design. |
| H14 customers and agent tasks | Partial | Agent tasks are paged and related identities are batch-hydrated. Existing customer/account views retain some broad legacy queries pending usage and index measurements. |
| H15 dashboard request coalescing | Partial | `services/singleflight.py` coalesces simultaneous identical dashboard reads within a process. Cross-process caching and invalidation are intentionally not introduced without a durable freshness policy. |
| H16 role/sidebar background calls | Fixed | Shared sidebar polling uses the common visibility-aware polling helper and avoids agent counts for non-agent roles. |
| H17 chat history | Fixed | `chat.py` supports cursor pagination; the chat client loads older messages in batches and refreshes incrementally. Only returned recipient messages are marked read. |
| H18 target widgets | Fixed | Target and performance widgets use the shared safe polling behaviour. |
| H19 exports and long jobs | Partial | Delivery task export streams batches rather than silently limiting at 1,000. PDF, upload, payment, and other business jobs were not moved to a new queue because that requires a deployed worker and operational ownership. |
| H20 MongoDB indexes/startup DDL | Fixed | `services/index_management.py` records requested indexes instead of running automatic DDL in app processes. `scripts/manage_indexes.py` discovers and applies only a reviewed index plan. |

## Measured local comparison

`scripts/validate_task_pagination.py` seeded an isolated local MongoDB with 1,503 mixed-ID support tasks. These are local synthetic figures, not production measurements.

| Operation | Median observed time | Response size | Database commands |
|---|---:|---:|---:|
| Legacy task-list sample | 46.85–53.09 ms | 699,383 bytes | 5 |
| New task-count request | 24.826–32.192 ms | 83 bytes | 1 |
| New 25-item task page | 44.294–61.071 ms | 17,610 bytes | 3 |

The detailed sample data is saved in `docs/performance-local-measurements.json`. The paged request reduces transfer substantially while retaining filtering, status normalization, related-name search, and complete pagination.

## Operational steps before or with deployment

1. Build both frontend bundles as part of the normal Render build. Customer Support and Inventory production builds completed locally; both emitted existing bundle-size warnings, so code-splitting remains a follow-up.
2. Review `docs/index-plan.json`, then apply indexes from a controlled one-off environment:

   ```powershell
   python -m scripts.manage_indexes --apply docs/index-plan.json
   ```

   This is deliberately separate from normal web-process startup. Back up and review the plan first; do not run it automatically during deploy.
3. Schedule the two bounded maintenance commands using a Render Cron Job or owned operations schedule:

   ```powershell
   python -m scripts.repair_delivery_followups --limit 200
   python -m scripts.enrich_login_locations --limit 100
   ```

   Run each with appropriate frequency after observing queue size and MongoDB impact. The repair job records progress/retries so it is safe to repeat.
4. Set `PERFORMANCE_SAMPLE_RATE` to a low nonzero value only while gathering post-deploy evidence; use `0` to disable timing samples. Do not put secrets, cookies, authorization headers, or raw request bodies in logs.
5. Monitor separately: anonymous `GET /login`, login `POST`, dashboard routes, task counts, task page requests, MongoDB command time, queue depth, and worker waiting time.

## Cache, invalidation, and rollback

Request memoization lasts only for one request. Dashboard single-flight only joins concurrent matching requests in one web process; it does not serve stale cross-request data. Pagination reads current data and does not add a response cache.

To roll back, deploy the prior application revision. If investigation logging itself needs to be disabled first, set `PERFORMANCE_SAMPLE_RATE=0`. Do not restore automatic index creation: use the index management command with a reviewed plan if indexes need to be changed.

## Remaining risks and measurements needed

- The original external browser timeout for anonymous `/login` remains an observation only. The login GET path contains no synchronous IP-geolocation call after this change, but a production trace is required to attribute any remaining timeout to web-worker queueing, MongoDB, platform routing, or another dependency.
- The application still starts with a MongoDB connectivity check. Measure startup and connection-pool wait time in the Render region before changing worker count or connection settings.
- CPU and memory headroom do not rule out request blocking. With a 512 MB instance, worker-count changes need a memory measurement under real load.
- Legacy compatibility endpoints intentionally remain available. Their clients should be identified from access logs before removal; the current React support UI no longer relies on the full-list route.
- The supplier nested-search compatibility path, broad executive/account views, large JavaScript bundles, and long-running PDF/upload/payment workflows require production traffic and data-volume measurements before further redesign.

## Files added for operations and validation

- `services/performance.py`, `services/public_assets.py`, `services/login_enrichment.py`, `services/request_memo.py`, `services/singleflight.py`, `services/index_management.py`
- `services/support_task_queries.py`, `services/delivery_followups.py`
- `scripts/manage_indexes.py`, `scripts/repair_delivery_followups.py`, `scripts/enrich_login_locations.py`, `scripts/validate_task_pagination.py`, `scripts/run_offline_tests.py`
- `tests/test_performance_improvements.py`, `tests/test_login_performance.py`, `tests/polling.test.cjs`

## Summary for ChatGPT

The agreed performance changes are implemented and locally validated with 351 passing isolated tests. The customer-support page now uses paginated task data and safe polling, the doubled agent URL and role-mismatched sidebar request are fixed, login geolocation is a bounded asynchronous job, delivery repair is no longer a read-side recurring write, and index changes now require a reviewed operational command. No production deployment or database change has been made. The next step is to deploy normally, apply the reviewed index plan once, schedule the two maintenance jobs, and collect per-route Render/MongoDB timings before making worker or broader caching decisions.
