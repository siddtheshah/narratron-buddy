# Security audit

Reviewed October 7, 2026. Scope: unauthorized access to non-public user data and AI service usage without billing a user.

## 1. [P1] Anonymous access to private theater state

Requests to `/api/chat`, `/api/latest`, and `/api/sticky-notes` without `theater_id` skip authorization. `CanvasStateService` then selects a default deployed theater, exposing its private content to unauthenticated callers.

- Code: `api_server/canvas.py:476`, `api_server/canvas.py:489`, `api_server/canvas.py:815`, `components/canvas/canvas_state_service.py:24`.
- Fix: Resolve the theater first, then require authorization for that theater on every read and write, including requests that omit its ID.

## 2. [P1] Anonymous theater listing exposes saved conversations

The unauthenticated `/api/theaters` branch returns full metadata for all theaters. It hides `join_key` but retains private configuration and `canvas_state`, which includes persisted chat messages.

- Code: `api_server/theaters.py:313`, `api_server/theaters.py:338`, `components/canvas/canvas_state_manager.py:83`.
- Fix: Require authentication and return only authorized theaters. Use an explicit response schema that excludes private state from any public listing.

## 3. [P0] Uploaded HTML executes under the app's origin

Resolved: reference uploads now require validated PNG, JPEG, WebP, or GIF content. Existing unsupported or invalid references are served as attachment downloads with `application/octet-stream`, `nosniff`, and a sandbox CSP; valid images use an explicit image content type. Regression checks cover direct, folder, ZIP, disguised HTML, and legacy documents.

Reference uploads accept arbitrary content, including HTML, and the reference route serves HTML inline as `text/html` under the application's origin. An attacker can share a reference URL for their own theater, including its join key, with a logged-in victim. JavaScript in that document can read the victim's authenticated APIs and extract private data; HttpOnly cookies do not prevent these authenticated requests.

- Code: `api_server/theaters.py:223`, `api_server/theaters.py:701`, `api_server/theaters.py:729`, `components/theater_manager.py:422`.
- Fix: Restrict and validate accepted reference formats. Serve untrusted documents as downloads or from a separate origin without application cookies, with appropriate content-type and sandbox protections.

## 4. [P1] Upload paths escape theater directories

ZIP and folder reference paths retain `..` components and are written without checking that the resolved destination remains inside the theater's reference directory. Lore paths and playlist names also lack equivalent containment checks. An authenticated uploader can overwrite other theaters' files or writable application files, enabling further data compromise.

- Code: `components/theater_manager.py:422`, `components/theater_manager.py:437`, `components/theater_manager.py:458`.
- Verification: An isolated ZIP containing `references/../../../outside.png` wrote its contents outside the created theater directory.
- Fix: Reject absolute paths and traversal components, and verify every resolved destination is inside its intended asset directory before creating directories or writing files.

## 5. [P0] Buyers control both credit quantity and payment amount

Resolved: custom credit sales are disabled. Purchase requests accept only fixed server-defined packages and reject custom fields; checkout verification and signed webhooks also reject custom or missing package metadata. Settlement validates paid status, payment mode, currency, actual amount, and credit quantity against the selected package. Direct charges require the expected amount received. Regression tests cover custom purchase rejection, underpayment, inflated metadata, delayed payments, and duplicate settlement.

Custom purchases accept independent, client-supplied `custom_credits` and `custom_usd` values. These become checkout metadata, and settlement trusts that metadata when granting credits. An attacker can request millions of credits for a tiny payment. Positive amounts below half a cent also round to zero in the generated checkout request; whether such a checkout completes depends on Stripe behavior and was not tested live.

- Code: `api_server/payments.py:70`, `api_server/payments.py:146`, `api_server/payments.py:156`, `api_server/payments.py:234`, `api_server/payments.py:276`.
- Verification: A mocked checkout request for 1,000,000 credits and USD 0.001 produced `unit_amount=0` and metadata granting 1,000,000 credits.
- Fix: Calculate credit quantities and prices exclusively on the server. Enforce finite values, valid payment minimums, and integer currency units, and verify the actual settled amount and payment status before granting credits.

## 6. [P1] Live AI supports unbilled text and image input

WebSocket text and image input reaches the live AI without a corresponding usage charge. Live billing tracks incoming audio bytes and specific tool callbacks. In normal mode, conversations that avoid chargeable tools can consume AI services without debiting the user's balance. The unauthenticated agent-start endpoint also summons the model, creating another entry point for unmetered interactions.

- Code: `services/live_stream_service.py:235`, `services/live_stream_service.py:277`, `services/live_agent_manager.py:269`, `services/live_agent_manager.py:1181`, `api_server/app.py:147`.
- Verification: A mocked text WebSocket turn was forwarded without recording audio or story-plan usage. The live-session input and billing implementations were also inspected.
- Fix: Meter every AI interaction, including text, images, and system-initiated turns, and enforce credit availability before accepting paid work. Require appropriate authorization on agent lifecycle endpoints.

## 7. [P1] Concurrent stamp requests consume AI without billing

Stamp generation releases the billing lock after checking the balance and before calling the image provider. Multiple requests can therefore pass the initial check with credits for only one image. After generation, later requests fail the second balance check without charging for the AI work already performed.

- Code: `api_server/theaters.py:89`, `api_server/theaters.py:100`.
- Verification: The existing `test_stamp_jobs_generate_concurrently_and_settle_without_overdraft` test demonstrates two provider calls but only one usage charge when the account has credits for one image.
- Fix: Reserve credits atomically before calling the provider, then settle or refund the reservation according to the generation outcome. Reservations must work across server processes and all competing spending paths.

## Verification and limitations

Eight targeted local checks passed using mocked AI and payment services and isolated upload files. No live AI calls or payments were made. Application source files were unchanged during the review. Deployed infrastructure and live payment-provider behavior were not tested; this audit confirms vulnerable code paths rather than exploitation of a deployed instance.
