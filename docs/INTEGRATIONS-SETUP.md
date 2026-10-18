# Prototype integration adapters (Build 5.2.4; SMS/printer/webcam adapters added in 5.2.3)

This guide covers the dual-mode prototype adapters for SMS, Windows printing and a live webcam used as a local camera input. These adapters are meant for synthetic-data demonstrations and local acceptance checks; they do not make the overall VMS production-ready.

For the new Student/Staff/Contractor kiosk face-attendance workflow, read [`KIOSK-FACE-ATTENDANCE.md`](KIOSK-FACE-ATTENDANCE.md) and the formal [`CHANGE-REQUEST-KIOSK-FACE-ATTENDANCE.md`](CHANGE-REQUEST-KIOSK-FACE-ATTENDANCE.md). The attendance feature remains mock-first and has a narrower live gallery boundary than a search across all Genetec Cardholder portraits.

## Safety defaults

- SMS defaults to `STAMFORD_SMS_MODE=mock`. It writes a clearly labelled `Simulated` message to the local outbox and does not send to a handset.
- Live SMS requires provider credentials **and** an exact `STAMFORD_SMS_ALLOWED_RECIPIENTS` allow-list of E.164 numbers. Start with your own test number. Never configure a general visitor list during development.
- Store API credentials in the Windows user/session environment or an approved secrets store. Do not put them in source code, commit them, add them to screenshots, or paste them into the browser.
- The app and Python API remain loopback-only. Do not port-forward, reverse-proxy or expose the prototype to a LAN or public internet.
- Webcam frames used by the Integrations & devices page stay in the browser preview/capture unless a workflow explicitly submits a frame. No validated SRS AI detector is supplied by this adapter.
- The browser uses the installed Windows browser/printer pipeline. `window.print()` opens the Windows print dialog; the application cannot attest that paper physically emerged. Confirm the selected printer/queue and test page output on the Windows host.
- Genetec remains a separate bridge and must be validated against the site's actual Security Center/SDK/licences. Passing local adapter tests is not evidence of live Genetec acceptance.

## SMS mode: local mock (default)

Run `Start-Demo.cmd` and open **Integrations & devices**. The status should show **Local mock**. Send a test message with a valid E.164 test number (for example `+6591234567`, replaced with your own placeholder/test number). The SMS outbox records `Simulated`; no phone is contacted. The invitation workflow also replaces `{PASS_URL}` with an explicit demo-only note when in mock mode.

## Live SMS with Vonage

Vonage is the first option to try because its [official developer page](https://www.vonage.com/communications-apis/sms/developer/) advertises free trial credit without a credit card. Trial/demo accounts are restricted: official [support documentation](https://api.support.vonage.com/hc/en-us/articles/212554438-What-are-the-limitations-of-a-trial-account) says SMS may only be sent to the registered number and four verified test numbers and appends `[FREE SMS DEMO, TEST MESSAGE]` to messages. Free trial therefore means **limited evaluation**, not free ongoing production messaging. Review the current terms and destination-country rules before use.

Create/verify a Vonage account and test destination in the Vonage dashboard, then set these variables in the same PowerShell session used to launch the app:

```powershell
$env:STAMFORD_SMS_MODE = 'vonage'
$env:VONAGE_API_KEY = '<key from provider dashboard>'
$env:VONAGE_API_SECRET = '<secret from provider dashboard>'
$env:VONAGE_FROM = 'StamfordVMS' # or the sender approved for the destination country
$env:STAMFORD_SMS_ALLOWED_RECIPIENTS = '+65XXXXXXXX' # exact approved test recipient only
.\Start-Demo.cmd
```

Replace `+65XXXXXXXX` with the exact verified number in E.164 format. These environment settings apply only to the current shell/session unless deliberately configured elsewhere. Do not save real secrets in this repository. Use a provider-approved sender and destination-country-compliant configuration.

## Live SMS with Twilio (alternative)

```powershell
$env:STAMFORD_SMS_MODE = 'twilio'
$env:TWILIO_ACCOUNT_SID = '<Account SID>'
$env:TWILIO_AUTH_TOKEN = '<Auth Token>'
$env:TWILIO_FROM_NUMBER = '+1XXXXXXXXXX' # SMS-enabled provider number
$env:STAMFORD_SMS_ALLOWED_RECIPIENTS = '+65XXXXXXXX'
.\Start-Demo.cmd
```

[Twilio's current trial documentation](https://www.twilio.com/docs/usage/trials) describes 100 trial SMS units and a 30-day trial, but trial limitations include verified recipients, country restrictions and predefined message-content rules. Consequently, a custom VMS invitation body may not be supported by a trial account; verify the provider's current account restrictions before selecting it for this prototype.

## Important: live SMS testing versus sending real visitor invitations

The **test SMS** function can be exercised live once the provider settings and allow-list are configured. Live **invitation SMS** is intentionally blocked unless `STAMFORD_PUBLIC_PASS_URL_TEMPLATE` is configured as a hosted HTTPS pass-page URL containing `{token}`. Example shape only:

```powershell
$env:STAMFORD_PUBLIC_PASS_URL_TEMPLATE = 'https://<approved-host>/<pass-route>/{token}'
```

This setting is a contract to a secure hosted pass page; the adapter validates only the URL-template syntax and does not probe the endpoint or prove it exists. This prototype does **not** create that internet/LAN-reachable page. Do not set it to a made-up host or expose the loopback-only VMS service. Before live invitation links are enabled, the team must implement/deploy an approved pass endpoint, prove token validation/expiry/revocation, configure TLS and access/network controls, and approve the externally transmitted data. The URL should not include visitor names or other personal data.

## Windows printer test

1. On the Windows demo host, open **Integrations & devices**.
2. Select **Open Windows print dialog**.
3. Choose the approved Windows printer or Microsoft Print to PDF and confirm the preview/layout.
4. Submit the job, then verify the queue and actual output on the printer. A successful call to the browser print dialog is not evidence of physical printing.
5. Then test a visitor sticker using the approved template and verify the required fields (visitor name, host, destination, validity, QR reference). Printer-offline/paper-out recovery remains a separate acceptance test.

This is a real OS/browser print path for an attended kiosk. It is not silent printing or a direct server-side spooler integration. If unattended kiosk printing is required, implement and secure an approved local print bridge after the printer model/driver is confirmed.

## Use a webcam as a local camera source

1. Open the app at `http://127.0.0.1:8080` in a supported desktop browser and allow camera permission.
2. Navigate to **Integrations & devices**, select the intended webcam, then start the live feed.
3. Capture a preview frame. The capture is in-memory and intended to prove camera connectivity/framing for prototype demonstrations.
4. Stop the feed when finished. Leaving the page also stops the adapter's active stream.

A webcam is a useful local camera surrogate, but it is not a validated IP-CCTV/RTSP integration. The capture/preview does not implement dwell, restricted-zone, person-down, queue/crowd or camera-tamper detectors, and no AI accuracy claim should be made. Live analytics acceptance requires an approved local detector pipeline, approved camera/feed and scripted testing for false alerts and missed events.

## Genetec live acceptance

Continue to use the existing local bridge configuration and explicit write gates. On the approved Windows host, verify the actual Security Center version/build, modules/licences and SDK entitlement; perform the approved read-only probes; and, using only synthetic non-production records, validate native Visitor create/update, status/lifecycle read-back, access-window/area mapping, cancellation/revocation, retry/reconciliation and negative controller-enforcement cases. Do not interpret the SMS/printer/webcam adapter tests as Genetec evidence.

## Validation commands

From the repository root:

```powershell
python -m unittest discover -s tests -v
python -m py_compile service\integrations.py service\vms_service.py
```

A browser camera test must be performed in the actual Windows desktop browser because media-device permissions are not available to a headless unit test. Live SMS tests should use the provider's test mode/account and explicitly allow-listed recipients only.

## Current acceptance boundary

- SMS mock adapter: locally testable; no handset delivery.
- SMS provider adapter: provider request/acceptance can be tested with verified test credentials; final delivery must be checked with provider delivery receipts/console where available.
- Live invitation SMS with working secure QR link: blocked pending a deployed, approved HTTPS pass page and data-flow approval.
- Windows attended print: browser/native print dialog is implemented; target printer output still requires manual Windows-host verification.
- Webcam: live local camera preview/frame capture is implemented; analytics inference and CCTV stream integration are separate work.
- Genetec: the existing bridge remains separate; live target-host verification is outstanding.
