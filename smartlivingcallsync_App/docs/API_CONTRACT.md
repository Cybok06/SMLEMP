# Production API contract

- Base URL: `https://smartliving-u2rf.onrender.com/`
- Endpoint: `POST /api/customer-support/mobile/calls/sync`
- Headers: `Content-Type: application/json`, `Accept: application/json`
- Authentication: none; clients must not send an `Authorization` header, cookie, or CSRF credential
- Batch size in this app: 100; enforced range: 1–500

Request DTO property names are fixed by `ApiModels.kt`:

```json
{
  "device_id": "support-phone-01",
  "calls": [{
    "external_call_id": "7821",
    "phone_number": "0530393625",
    "from_number": "",
    "call_type": "outbound",
    "started_at": "2026-08-12T18:49:00Z",
    "duration_seconds": 332,
    "sim_account": "SIM1"
  }]
}
```

Only `inbound`, `outbound`, and `missed` are sent. Android call-log `_ID` becomes a string. Dates are UTC ISO-8601 instants. Durations stay in seconds and are non-negative. `PHONE_ACCOUNT_ID` is preserved (up to 160 characters); `from_number` is blank when unavailable.

Responses are processed by `results[].index`, not list order assumptions or top-level counts. `created` and `duplicate` are successful locally. `invalid` and `rejected` retain their safe item error. Customer identity and enrichment fields are never sent; the backend owns number normalization, customer matching, source, and enrichment state.
