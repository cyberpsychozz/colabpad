# Status

What is not done, what is not verified, and open questions. How the system works is in
[`spec.md`](spec.md); keep both current. How to run and test: [`development.md`](development.md).
Test runs: [`reports/`](reports/).

## Not done

- Balance tuning beyond the spec's defaults: nothing was changed.
- Fuller landing copy (`/` exists in RU and EN, but is short).

## Not verified

- A real Robokassa test shop ([`billing.md`](billing.md)): the link, ResultURL and recurring requests are checked against
  Robokassa's docs only (pytest with a mocked transport, and a browser scenario that stops
  before Robokassa's page), so §12.10's payment itself has not been walked. Three points the
  docs leave open and a test payment should settle:
  - the Receipt inside the signature: the docs say both "minified JSON" and "URL-encoded";
    we URL-encode it (their PHP example does). Error 29 on the payment page means this is wrong.
  - the signature of a recurring child charge is not spelled out; we sign it like the link.
  - recurring charges need Robokassa's approval for the shop, and the docs don't say whether
    they work in test mode. `billing.recurring` stays off until both are known.
- Robokassa **live mode** (`ROBOKASSA_MODE=live`, [`billing.md`](billing.md)): pytest checks the
  link, ResultURL and recurring request without `IsTest` and with the production passwords; a
  real payment in live mode, and its «Мой налог» receipt, have not been seen yet.
- Umami revenue (`plus-paid` on `/billing/return`): checked against a stub tracker in the
  browser scenario. Not yet seen in the real Umami Revenue report. A sale is missed when the
  buyer never comes back to the return page (closes Robokassa's tab), and counted twice when
  the same buyer returns in two browsers.
- Real SMTP delivery.
- Real РСЯ banners in the four slots (§12.15): the layout and the render calls are checked in
  the browser against a stub loader; whether block `R-A-20462150-1` fills a 216–300 px wide,
  ~300–400 px tall slot well (and whether РСЯ accepts one block shown four times with
  `pageNumber`) is seen only once `ads.enabled` is on with the real id.
- The adblock gate with a real ad blocker (§12.12): unit-tested; in the browser only "no
  blocker, empty fill, world opens" was seen.
- Driving with the on-screen stick (touch). §12.6 (a remote drives the car, the other player
  is told it is taken) has no browser scenario: it was dropped from `web/e2e/` because it
  failed often (B's click on the remote didn't take control within 10 s). The server side is
  covered by pytest.
- Entity animation with real part boxes (legs, wheels) in the browser: the rig is unit-tested
  and its sheets were reviewed by eye, but in the browser only drawings typed through the
  manual grid (no parts, so they hop) were seen.
- §12.2/3/4/7 in a browser (behaviour over minutes): unit-tested per ● cell instead.

- The legal pages (§9b) are written by an engineer from Robokassa's and Yandex's checklists,
  not by a lawyer. Worth a read before moderation, especially the refund terms (unused whole
  days, within 10 days) in `/offer` §4, which are a business decision.

## Open questions for the human

- **Seller details are blank.** Create `config/legal.yaml` on the server from
  `config/legal.example.yaml` (full name, ИНН, support e-mail, phone; `docs/deploy.md` §9) before sending the site to Robokassa or РСЯ moderation; until then the
  footer shows no seller and the documents say «не указано».
- **Receipts for a self-employed seller.** The seller is self-employed (НПД), so receipts go to
  «Мой налог» through Robokassa's «Робочеки СМЗ», which has to be switched on in the shop.
  Robokassa builds that receipt from the `Receipt` item list, so `receipt.enabled` stays on;
  `sno` must stay empty (the tax systems it lists are for ИП/ООО). Not checked against a real
  shop yet.
- **Roskomnadzor notification.** The server is in Russia, and the privacy policy says so
  (152-FZ art. 18 p. 5). The operator still has to notify Roskomnadzor before processing
  personal data (pd.rkn.gov.ru), unless an exemption applies.
- **Auto-renewal.** The offer promises none. Turning `billing.recurring` on needs a new edition
  of `/offer` (consent to recurring charges, how to stop them) at the same time.
