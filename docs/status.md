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
- Real SMTP delivery.
- The adblock gate with a real ad blocker (§12.12): unit-tested; in the browser only "no
  blocker, empty fill, world opens" was seen.
- Driving with the on-screen stick (touch); WASD was driven in the browser.
- Entity animation with real part boxes (legs, wheels) in the browser: the rig is unit-tested
  and its sheets were reviewed by eye, but in the browser only drawings typed through the
  manual grid (no parts, so they hop) were seen.
- §12.2/3/4/7 in a browser (behaviour over minutes): unit-tested per ● cell instead.

## Open questions for the human

None at the moment.
