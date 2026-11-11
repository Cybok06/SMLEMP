
  # Operations_Management

  This is a code bundle for Operations_Management. The original project is available at https://www.figma.com/design/ya6exopn9ACkIHcGQE5bmh/Operations_Management.

  ## Running the code

  Run `npm i` to install the dependencies.

  Run `npm run dev` to start the development server.

## Customer support targets

Executives and admins can open **Customer Support** in their sidebar to assign each support
officer daily/weekly calls, weekly field trips, and daily/weekly/per-trip customer
interactions. Targets repeat until changed; 0 disables a target. Changes have an
effective date and are retained in the executive change history.

Support officers can view **My Targets**. In **Tasks → Visit to field**, log a
completed visit with its location, start/end times, and notes. Then use
**Customers → Activities → Log Activity**, choose **Interaction**, and select
the visit. The interaction time must be within that officer's visit.
Notes and older activities without a linked field visit do not count as field
interactions. Logged calls count once for their original officer; completing an
unassigned synced call assigns it to the logging officer.

All dates/times use Ghana time (UTC), with Monday–Sunday weeks. Daily and weekly
progress uses the actual call/visit/interaction time. Setting a target partway
through a period applies it to that entire period's totals; earlier reporting
dates keep their prior target settings.

Run `npm run build` before serving the app through Flask. Backend checks:
`python -m pytest tests/test_customer_support_targets.py tests/test_mobile_call_sync.py -q`
(requires pytest and mongomock). Optional browser verification:
`python -m pytest tests/test_customer_support_targets_ui.py -q`
(requires Playwright and an installed Chromium browser; `PLAYWRIGHT_CHANNEL` can
select a browser channel).

## Delivery confirmation and satisfaction

When an item is marked delivered or its payment card is closed, one automatic
Customer Support follow-up task is created per customer card, due the next day.
The delivery transaction includes this task creation. Repeated delivery/closure
events reuse the task and retain its reviews. Older delivered items and closed
cards are reconciled in batches when the follow-up queue or Tasks is opened.

**Deliveries → Satisfaction → Follow up** records whether the customer was
reached, full/partial/no receipt, satisfaction, a 1–5 star rating, outstanding
issues, and notes. Unresolved or unreachable customers require a next follow-up
date. Only full receipt, a satisfied outcome, and no outstanding issue complete
the task. Tasks opens the same review form and prevents manual completion or
deletion of these automatic tasks. Review history includes officer and timestamp;
concurrent edits are rejected rather than overwriting another review.

The confirmation queue includes closed cards without delivery records. The
Satisfaction page reports actual delivery ratings, receipt confirmations, open
follow-ups, and outstanding issues using each card's latest review.

Checks: `python -m pytest tests/test_delivery_followups.py tests/test_packaging_stock.py -q`.
Browser workflow: `python -m pytest tests/test_delivery_followups_ui.py -q` after
building the frontend, with Playwright and a Chromium browser installed.
  


### Repeat-purchase follow-up

Successful delivery reviews require a yes/no answer about another item. A yes answer records the requested product, agent, follow-up date and optional preferences in the same atomic write as the review. Support can track all opportunities in Repeat Purchases and reassign open opportunities. Assigned agents see them in Complaints ? Repeat-purchase opportunities, record follow-up notes and dates, and finish as Purchased (linked to an actual new purchase on that customer account) or Closed (with a reason). Separate versions and audit histories preserve concurrent review and agent updates; finished opportunities cannot be overwritten. Restart Flask after installing this update; the support production bundle is built in dist.


### Customer interaction records

Customer Activity ? Log Activity ? Interaction records the customer and date, channel, branch/salesperson, delivery status, feedback, issues, requested products, action taken and next follow-up details/date. The authenticated staff ID/name/role identifies the performer; this cannot be supplied by the client. Field visit interactions must link to the performer's visit and occur within its times; other channels do not need a visit. The call form captures the same details on its existing call record, including synced-call enrichment, and the customer timeline displays them without creating a duplicate activity or target count. Existing historic records remain readable; missing historic details are not invented.


### Performance tracking

Admin and executive Customer Support target pages and Support My Targets show daily/weekly recorded performance alongside configured targets. Support Dashboard includes personal target bars and the same performance summary. Counts use ERP records attributed to the selected officer: logged non-missed calls (excluding incomplete imports), completed field visits, field interactions, all-channel activity interactions, recorded ticket/delivery issue resolutions, repeat opportunities created, assigned call/task/opportunity follow-ups and distinct linked purchase outcomes. Issues without an attributed resolution event and purchases without an opportunity linkage do not count. Resolution audit events are recorded on new support ticket resolution transitions; historical ownership is not inferred. Opportunities and their purchases are attributed to the support officer who originated them, even when a sales agent follows up. Purchases count on the outcome recording date. Assigned follow-ups count originating call/task/opportunity records created in the reporting window. The dashboard fetches personal progress separately from the shared operational cache.
