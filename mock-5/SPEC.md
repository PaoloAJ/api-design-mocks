# Design spec — Payments API

The rules this service is built to. Any change to the codebase is measured
against them.

## Shape

- Resources are plural nouns (`/v1/payments`). A state change with side
  effects gets its own action sub-resource (`POST /v1/payments/{id}/status`,
  `POST /v1/payments/{id}/refund`), never a field write on `PATCH`.
- Every response, including failures, uses the shared error envelope.
- Storage shape and wire shape are separate; handlers return `serialize(...)`,
  never a raw record.
- There is no `DELETE /v1/payments/{id}`. A payment is a financial record;
  `void` and `refund` are the only ways to undo one, and both leave a record
  behind instead of erasing history.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`; `store.list()` is for
   collection endpoints only.

2. Filtering **MUST** happen before pagination. Never filter a page after it
   has been sliced — it produces short pages and a wrong `has_more`.

3. Tenant scoping **MUST** derive from the authenticated identity in
   `g.org_id` and nothing else. Never from a request header, query parameter,
   or body field.

4. A resource owned by another tenant **MUST** return `404`, never `403`.
   `403` confirms the ID exists.

5. Cursors are opaque to clients and **MUST NOT** be parsed by them. A cursor
   **MUST** be rejected with `400` if it is replayed against a different
   ordering than the one that issued it.

6. Sort keys **MUST** be immutable for the life of a paging walk. Paging by a
   field that changes under the client can skip or repeat rows.

7. `settled` and `failed` are terminal outcomes written **only** by
   `POST /v1/settlements`. The manual status action (`POST
   /v1/payments/{id}/status`) **MUST** reject any client attempt to set them
   directly — a merchant does not get to declare its own settlement.

8. A refund amount **MUST** be validated against the payment's remaining
   refundable balance (`amount - amount_refunded`), never against the
   original `amount`. Checking against the original amount lets repeated
   partial refunds together exceed what was ever captured.

9. Changes to the cursor payload format **MUST** stay backward compatible for
   one release, or ship behind a version marker inside the cursor.

## Non-goals

- Persistence. Storage is in-memory by design; the subject is the HTTP
  contract.
- Authentication beyond static demo keys.
- Fraud and risk scoring at authorization time. Every `POST /v1/payments`
  succeeds; there is no card network, no decline, no 3-D Secure step.
- Dispute and chargeback resolution. `disputed` exists as a status value so
  support staff can flag a payment during a phone call, but there is no
  dispute record, no evidence flow, and no automated chargeback ingest.
- Customer-facing notifications. No receipt, capture confirmation, or refund
  email is ever sent.
