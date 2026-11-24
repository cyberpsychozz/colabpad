# Billing: Robokassa

Payments moved from YooKassa to **Robokassa** in November 2026. This page explains how billing
works now and why. The exact contracts are in [`spec.md`](spec.md) (§5 schema, §8 REST, §9 billing),
the cabinet setup is in [`deploy.md`](deploy.md) (step 9), and the open points are in
[`status.md`](status.md). Code: `app/billing/` (`gateway.py` speaks Robokassa, `service.py` holds
the subscription logic), `app/repo/billing.py`, `config/billing.yaml`.

**Test or live mode, set on the server.** `ROBOKASSA_MODE=test` (the default) sends `IsTest=1`
with every request and signs with the shop's *test* passwords (`ROBOKASSA_TEST_PASSWORD1/2`).
`ROBOKASSA_MODE=live` sends no `IsTest`, signs with the *production* passwords
(`ROBOKASSA_PASSWORD1/2`) and takes real money. Each mode reads only its own pair: live mode
never falls back to the test passwords (without its own, checkout answers `PAYMENT_FAILED`), and
a ResultURL notice signed with the other mode's password #2 is refused. Robokassa itself rejects
production passwords in test mode and test passwords in live mode. Only the human switches to
live, in the server's `.env`; tests and e2e always run in test mode. The app logs the mode at
startup (`Robokassa: live mode`).

## How payment works

- **Paying.** `POST /api/billing/checkout` records a `pending` invoice and returns `paymentUrl`,
  a signed link to Robokassa's payment page (`auth.robokassa.ru/Merchant/Index.aspx`), in the
  user's language. After paying, Robokassa sends the user back to `/billing/return`
  (SuccessURL), which only shows the state. A failed payment goes to `/plans` (FailURL).
- **Confirmation.** Robokassa calls `GET|POST /api/billing/result` (ResultURL; it replaced
  `/api/billing/webhook`). A call counts only if all of these hold:
  - it is signed with password #2;
  - it comes from one of Robokassa's two addresses (`result_ips`);
  - its amount equals the invoice's.

  It answers `OK<InvId>`. Repeated calls change nothing, because only a `pending` payment can
  settle. A wrong amount cancels the invoice.
- **Why the signature is the proof.** With YooKassa we looked each payment up again before
  trusting it. Robokassa's status lookup (OpStateExt) doesn't answer for test payments, so the
  password #2 signature is the proof.
- **Invoice numbers.** Robokassa needs whole-number invoice ids (`InvId`) that never repeat for
  the shop. We choose them: the larger of the last id + 1 and the current Unix time in seconds,
  so they stay unique even after a fresh database.
- **Auto-renewal.** When a period ends, the subscription goes `past_due` and we charge another
  month by repeating the first invoice paid with `Recurring=true`
  (`POST /Merchant/Recurring`). Robokassa's confirmation at ResultURL makes Plus active again. A
  period is charged at most once. The flag `billing.recurring` (`config/flags.yaml`) is **off**:
  Robokassa has to approve recurring payments for the shop first.
- **Receipts.** A fiscal receipt (54-FZ) goes with every payment as the `Receipt` parameter.
  Its settings are `receipt` in `config/billing.yaml`: no VAT by default, and the tax system
  comes from the Robokassa cabinet unless `sno` is set.
- **Settings.** `ROBOKASSA_MODE` (`test` or `live`), `ROBOKASSA_MERCHANT_LOGIN`, the pair of
  passwords for the mode (`ROBOKASSA_TEST_PASSWORD1/2` or `ROBOKASSA_PASSWORD1/2`) and
  `ROBOKASSA_HASH` (the shop's signature hash, MD5 by default) replaced `YOOKASSA_*`. Without
  them, checkout answers `PAYMENT_FAILED`.

## Database migration (schema 2 → 3)

The payment tables had to change. Raising the schema version without a migration would have
made the production database refuse to start, and wiping it would lose every stroke. So
`app/db.py` has a single migration from version 2 to 3, which runs on the first start:

- the old YooKassa `payment` table is kept, renamed `payment_yookassa` (payment rows are never
  deleted);
- the saved YooKassa card id (`subscription.yk_payment_method_id`) is dropped, because Robokassa
  can't charge it. Its place is the empty `parent_inv_id`;
- everything else is untouched. Active Plus subscriptions stay active until their period ends.

Any other old version is still refused at startup.

## Still to do (for the human)

- In the Robokassa cabinet, set the URLs and put the **test** passwords in `.env`
  ([`deploy.md`](deploy.md), step 9).
- Make one real test payment. Robokassa's docs leave three points open
  ([`status.md`](status.md)):
  - whether `Receipt` goes into the signature URL-encoded (we follow their PHP example and do);
  - how a recurring charge is signed (we sign it like the payment link);
  - whether recurring charges work in test mode.
- If Robokassa's page shows **error 29**, a signature is wrong: check the passwords and the hash
  first, then the receipt.
- To go live: the shop must be activated for real payments in the cabinet, and «Робочеки СМЗ»
  switched on. Put the production passwords #1 and #2 (Technical settings, not the test ones)
  into `ROBOKASSA_PASSWORD1/2`, set `ROBOKASSA_MODE=live` and restart
  ([`deploy.md`](deploy.md), step 9). Then make one small real payment and check that the
  receipt reaches «Мой налог».
