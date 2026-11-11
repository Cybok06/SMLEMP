# Printer Analysis

Executive page: `/executive/printer-analysis`.

The dashboard includes only payments with `printer_feature_enabled: true` saved when the payment was recorded. Grant changes do not retroactively add or remove payments. Dates are payment-recorded dates in Africa/Accra. Branch is snapshotted for new eligible payments; older eligible payments use the agent's current branch as a fallback.

Printed means the client reported all receipt bytes sent. It does not verify paper output. A payment counts as printed after its first successful short or long receipt. Receipts sent counts each successful format once; legacy printed records without format data count as one unclassified receipt. The median interval uses the first successful print. A payment without a confirmed print is missed 15 minutes after recording, unless the dispatch is marked uncertain. Payments within 15 minutes are waiting. Late prints remain printed and are counted separately.

Before deploying on a large payments collection, apply the query index in a controlled environment:

```powershell
python -m scripts.manage_indexes --apply docs/printer-analysis-index.json
```

The page defaults to the last 30 Ghana calendar days. The API accepts at most 367 days and returns the latest 100 matching payments in its detail table.
