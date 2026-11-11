# Payment receipt printing

Executives manage access at `/executive/printer-access` (Printer Access in the sidebar).
Access defaults to disabled. A branch grant follows the agent's current branch;
an explicit individual grant or restriction takes priority. “Use branch setting”
removes that override. Agents without their own branch use their manager's branch.
Agents should refresh Add Payment after receiving access. API authorization is
checked again before every dispatch, so revocation takes effect immediately.

For enabled agents, confirmed Cash and verified Paystack MoMo payments open the
receipt instead of the existing success modal. The connection button invokes the
browser's printer chooser. A connected MP583 prints automatically after payment.
Short receipts are the default. Both screen previews and full/short ESC/POS layouts use the prototype design unchanged. There is no skip-printing control or endpoint.
The transport, QR/raster generation, baud rate (9600),
chunk size (256 bytes), and chunk delay (12 ms) come from `printer_test-main/index.html`.
Direct connection requires a secure context and a browser exposing Web Serial.

New payment documents contain:

| Field | Meaning |
| --- | --- |
| `printed` | Boolean, initially false |
| `payment_recorded_at` | Server UTC timestamp of payment creation |
| `printed_at` | First successful format confirmation timestamp in server UTC, otherwise null |
| `print_delay_seconds` | Confirmation timestamp minus payment creation timestamp |
| `print_status` | `not_requested`, `pending`, `printing`, `printed`, or `uncertain` |
| `printer_feature_enabled` | Access snapshot when the payment was created |
| `print_started_at` | Server timestamp when the dispatch was claimed |
| `payment_id` | Payment ID printed on the receipt and displayed below Printed at in histories |
| `receipt_prints.short` / `receipt_prints.full` | Independent status, confirmation time, interval, and private claim token for each format |

An atomic MongoDB update claims each format before the first printer byte is sent.
Only the recording agent can claim it. One short and one full receipt may be printed per payment; simultaneous claims for the same format cannot both succeed. The format selector stays enabled after printing so the other format can be selected and printed once.
Successful and uncertain dispatches cannot be claimed again for that format. Claims have no automatic expiry.
Cancelling printer selection leaves the payment saved and pending, with the receipt open for connection and retry. The Unprinted receipts list shows only payments recorded during the current Ghana calendar day (UTC midnight to midnight), with no successful format yet. Older records marked skipped by the previous version remain eligible for printing.

The browser acknowledges completion after every write succeeds. Acknowledgement
requests are idempotent and retried without resending printer bytes. If necessary,
the browser retains a pending acknowledgement locally and retries after a reload;
the modal also provides a retry button. Completion of an existing claim remains
allowed after access revocation so the payment audit can still be finalized.

The printer prototype has no paper/output acknowledgement. `printed: true` means
the complete receipt payload was written successfully, rather than proof of paper
output. A connection error locks that format as uncertain or outstanding. The aggregate `printed` flag remains true if the other format was successfully printed. A later successful
acknowledgement records the server time at which that acknowledgement was received.

Historical payments are not backfilled with invented print timestamps. Payments printed by the earlier release without a stored format remain locked in both formats; the system cannot safely determine which copy has already been printed. History
views distinguish them as older, untracked payments. Existing payment behavior
remains available for agents without printer access.

Validation (in-memory MongoDB; browser uses a simulated serial printer):

```powershell
python -m scripts.run_offline_tests tests/test_payment_printing.py tests/test_payment_printing_browser.py tests/test_payment_receipt.py tests/test_momo_payout_routing.py -q -p no:cacheprovider
```

Tests cover access inheritance and overrides, Cash and MoMo recording, CSRF and
ownership enforcement, concurrent claims, cancellation, removal of skip actions, partial transport failure,
lost acknowledgement responses, receipt data escaping, the complete Add Payment
modal transition, and mobile layout. A physical MP583 print remains a hardware
acceptance check.

Receipt URLs are generated with Flask `url_for`, including the production `/payment` blueprint prefix and any deployment mount. Non-JSON error responses are shown as service/session messages without attempting to parse HTML as JSON.

The printer animation displays its percentage without transport details or byte counts. Product and SUSU profile tabs for agents, managers, and executives, plus the customer-support API and built React app, display receipt status, recorded time, first print time, payment ID, interval, and details for each format.
