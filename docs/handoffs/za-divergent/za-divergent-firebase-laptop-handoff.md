# ZA Divergent Firebase laptop-agent handoff

Continue the isolated Firebase security and concurrency proof before building profiles and chat into ZA Divergent. Work from the user's GitHub-synced laptop checkout, keep billing disabled, and distinguish local emulator evidence from real-project validation.

Prepared 20 October 2026. This is a continuation prompt and technical handoff, not a claim that Firebase setup or deployment is complete.

## Instructions to the receiving agent

You are taking over development with the owner present on their laptop. Inspect the checkout and console state before changing anything. Explain the immediate next step plainly and help the owner complete it. Do not restart the product-discovery discussion or rebuild the existing map. Do not use background delegation for this workflow: previous handoffs repeatedly failed, and the owner wants direct, incremental execution with observable results.

First establish the current repository, Firebase CLI authorization, isolated project configuration, and owner UID. Then reproduce local rule tests and perform an explicitly approved, tightly bounded test against the isolated real Firebase project. Do not deploy to the public map or a production Firebase project as part of that proof.

## Product goal and constraints

ZA Divergent is a static, South Africa-focused incident map populated through FORGE. Add lightweight user identity and one text community room as a small MVP and portfolio demonstration, not a high-volume social network.

- Zero financial spend is a hard constraint. External services are acceptable; enabling billing, adding a payment card, switching to Blaze, paid extensions, and paid infrastructure are not.
- Keep existing hosting and the FORGE publication pipeline. FORGE retains editorial authority. Chat must never write incidents directly into the map.
- Visitors must continue to use the map anonymously. The initial chat trial is authenticated and membership-gated.
- Use Firebase Authentication and Cloud Firestore. Google sign-in is the current setup path and initial recommendation, not a requirement to add every provider.
- Preserve the completed mobile map-first work, compact Tools/menu controls, incident drawers, native sharing and deep links, and desktop layout.
- Treat community discussion as unverified discussion, never as confirmed reporting.

Initial scope: sign-in/out, minimal display-name profiles, one room, bounded recent history, message sending, own-message deletion, reporting, moderator removal, and posting suspension. The first security proof implements only a subset of that scope.

Exclude direct messages, attachments, uploaded avatars, voice/video, reactions, typing indicators, multiple rooms, notifications, prediction features, and automatic community-to-map publication. Online presence and community incident submissions are future work.

## Repository and integration points

Observed repository remote: `https://github.com/matakhakhu-coder/FORGE-OS`.

The inspected reference checkout was on `fix/mobile-sprint1-header-map` at `29e25dd`, “Add native sharing and copy-link action to event drawers,” with a clean working tree at handoff inspection. This describes the reference checkout, not a fresh audit of the user's laptop or GitHub branch. The user says their local repository has now been pushed to GitHub; inspect the actual laptop state rather than resetting it to this reference.

Start with `git status --short`, `git branch --show-current`, `git remote -v`, and `git log -5 --oneline`. Do not overwrite uncommitted work.

Relevant paths:

- `publisher/templates/map.html`: map shell, drawer, map behavior and integration markup.
- `publisher/templates/base.html`: shared header/footer; edits can affect other pages.
- `publisher/static/css/site.css`: shared styling.
- `publisher/static/css/map-first.css`: map-first layout overrides.
- `publisher/static/js/map-first.js`: Tools/search/menu interactions and keyboard behavior.
- `tools/publish.py`: renders the map into `dist/index.html` and copies assets.
- `vercel.json`: existing static-site serving configuration.

Proposed new assets are `publisher/static/js/community.js` and `publisher/static/css/community.css`. Edit sources, not just generated `dist` files. Social initialization errors must not prevent Leaflet or incident drawers from working.

The reference workspace also contains `za_divergent_social_mvp_brief.md` (v2), `social_rate_limit_rule.md` (older draft), `social_tests/` (newer executable rule/test bundle), and `firebase-uid-test.html` (Google sign-in helper). These were separate workspace files; do not assume they were included in the GitHub push. No matching social/Firebase files were found among tracked paths in the inspected reference checkout. If missing on the laptop, recreate the isolated test harness from the contract below or obtain those files, rather than claiming they transferred.

## Current Firebase setup state

The owner has been working through creation of a separate Spark test project. The existing UID helper is configured for:

- Project ID: `za-divergent-chat-test`.
- Auth domain: `za-divergent-chat-test.firebaseapp.com`.

These values were read from the existing helper, not verified against the live Firebase console in this handoff. Confirm that the signed-in owner owns this exact project, that it is isolated from production, and that billing is disabled. Obtain the web app configuration from its Project settings. Do not infer project ownership, provider activation, database creation, deployment state, region, or successful CLI login from these values.

The latest practical blockers were obtaining a Firebase Authentication UID through sign-in and authorizing the Firebase CLI securely. Earlier background attempts failed; they are not evidence that authorization or setup completed. No verified owner UID is carried in this handoff. No production concurrency result is available here.

The conversation previously reported passing emulator tests. The current handoff inspected the test source and configuration but did not rerun the suite. Reproduce results locally before relying on them. The older rate-limit note says “not run”; it predates the executable bundle and must not be treated as its latest verification report.

## Immediate setup sequence

1. Inspect the laptop checkout and whether the test bundle exists. Check Node, npm, Java, and Firebase CLI versions. The existing bundle declares Node 22 or newer; verify compatibility with the installed emulator tooling.
2. Check Firebase CLI account authorization locally. If needed, run the CLI's interactive login on the owner's laptop and let the owner complete Google's flow directly. Do not ask them to dictate a password, OAuth code, token, or credential JSON into chat. Never commit CLI credential caches.
3. List accessible projects and confirm `za-divergent-chat-test` in the console. Verify Spark/no billing, create or confirm Firestore and its region, register or confirm a web app, and enable Google under Authentication if not already enabled. Ask before an irreversible region choice.
4. Serve a small sign-in helper over localhost or HTTPS, not `file://`. Add its exact hostname to Authentication's authorized domains if needed. Localhost may need explicit authorization. Sign in with Google, then read the authenticated user's UID or confirm it under Authentication → Users.
5. A successful sign-in creates an Auth identity only. It does not create a membership document, grant moderator rights, or deploy rules.
6. With the owner’s approval, provision `members/{uid}` as exactly `{active: true}` using a trusted console operation. Record the actual owner UID for the moderator allowlist. Never authorize moderation by display name or editable profile fields.
7. Run the local emulator gate. Then review and approve the candidate rules and exact target project before any isolated-project deployment. Always use an explicit project flag for remote commands; never rely on an ambiguous default alias.

The browser Firebase configuration is public client configuration, not an administrator secret. Rules provide authorization. Do not include service-account keys, Admin SDK credentials, refresh tokens, or private FORGE data in the web build. A real sign-in test must use client credentials and deployed rules, not an Admin SDK bypass.

## Atomic posting contract

Recommended candidate interval: one message per account every 10 seconds. This interval remains a design recommendation. Two tabs using the same UID share the limiter; different UIDs have independent intervals.

Collections and exact shapes for this proof:

- `members/{uid}`: `{active: boolean}`, owner-provisioned, not client-writable.
- `activity/{uid}`: `{lastAt: timestamp, lastMessageId: string}`.
- `rooms/main/messages/{messageId}`: `{authorId: string, body: string, createdAt: timestamp}`.

A send must be one atomic batch containing the new message and that user's activity record. Both timestamps use `serverTimestamp()`, validated against `request.time`. The activity pointer must identify that exact new message. Never split these writes or use client time for enforcement.

Required invariants:

- Author UID equals the authenticated UID; membership exists and is active.
- Message body is a string of 1–500 characters; exact allowed fields only. Reject whitespace-only text additionally in the UI and render all user text as text, never HTML.
- Activity has exactly its two allowed fields. Its message ID is a nonempty, bounded safe string.
- The interval is checked against the prior activity state, not the new submitted timestamp.
- Message creation requires the after-state activity to select that message ID.
- Activity creation/update requires its referenced message not to exist before the batch and to be a valid message afterward, authored by this UID.
- An activity-only write, message-only write, two-message batch, old-message pointer, forged timestamp, or cross-user update must fail atomically.
- Clients cannot delete activity, reset the limiter, change membership, or edit messages. Deleting a message must not clear activity.
- The newer bundle permits own-message deletion, including by a suspended author, and moderator removal. Moderation is not a send/limiter bypass.
- A user can read their own membership even when suspended, but cannot list memberships or read another member's record.
- Message queries require an explicit positive limit of at most 50. Use `orderBy('createdAt', 'desc')` and `limit(50)` in the UI. Detach listeners on panel close.
- Deny everything else, including profiles and reports until their separate rules are implemented.

The executable reference rules use `emulator-moderator-only` as a moderator fixture. Never deploy that fixture as the real owner identity. The older draft lacks some deletion and membership-read behavior; prefer the newer bundle after review.

## Local emulator gate

The existing bundle uses Firebase client SDK, `@firebase/rules-unit-testing`, and `firebase-tools`. It has a dependency lockfile. Its Firestore emulator binds to `127.0.0.1:8080`, disables emulator UI, and points to `firestore.rules`. Its tests include a host guard to refuse remote test connections.

If the bundle is present:

```bash
cd social_tests
npm ci
npm test
```

Its underlying test command is:

```bash
firebase emulators:exec --only firestore --project demo-za-social "node --test --test-concurrency=1 rate-limit.test.mjs"
```

Keep the `demo-` project ID and localhost guard. Do not turn emulator tests into remote tests by merely changing the project ID. Emulator-only fixtures and bypass helpers do not transfer to a real database.

Existing test coverage includes first sends, immediate repeats, two-tab races, 20 concurrent first sends, 20 concurrent sends with existing expired activity, separate users, expired intervals, denied-batch rollback, missing/suspended/anonymous users, impersonation, forged time, extra fields, invalid bodies, membership visibility, updates, deletion permissions, activity deletion bypasses, and unbounded/oversized queries.

Record the actual pass/fail count and errors from this run. Do not represent inspection of source or the existence of a debug log as passing execution.

## Isolated real-project proof

After local tests pass and the owner approves test deployment, validate the same critical invariants against the real isolated Spark project using signed-in browser/client SDK sessions and actual deployed rules. The point is to check real server timestamp transforms, production rule evaluation, concurrent requests, and rollback, not just UI behavior.

- Start with one active test user. Confirm one atomic send succeeds and an immediate second send fails.
- Use two tabs with the same UID for simultaneous sends. Check persisted messages and activity, not just promise results.
- Run bounded 20-send races for first activity and existing expired activity. Coordinate sends within one limiter window; if requests span 10 seconds, multiple successes may be legitimate. Capture timing and interpret results correctly.
- Expect exactly one commit for a valid same-window race and no orphan messages or partial activity. Wait beyond the interval and prove sending recovers.
- Prove different UIDs can send independently, using a second account only when available and approved. Report a coverage gap rather than inventing one.
- Recheck forged timestamps, activity-only resets, message-only sends, two-message batches, and missing/suspended membership.
- Test own-message and moderator deletion without resetting the limiter, plus unauthorized deletion and activity deletion denial.
- Check bounded query acceptance and unbounded/over-50 rejection.
- Keep test content synthetic and clearly labeled. Use unique IDs, bounded requests, and no aggressive retries. Clean up test messages through an approved process without deleting activity to evade the limiter.
- Record the project ID, rules revision, run time, cases, persisted outcomes, quota observations, and any skipped checks. Do not log authentication tokens.

Do not deploy the full social feature on the strength of this slice. Profiles, report isolation, moderation provisioning, account deletion, privacy notices, and UI integration remain separate gates.

## Cost and safety limits

A 50-document listener cap limits each query result, not total daily reads. Repeated queries, multiple connected clients, message deliveries, and rules-dependent reads still consume usage. Rate limiting does not prevent multi-account abuse or quota exhaustion. Watch the project's usage dashboard during the proof.

Check current official terms and the owner's actual console before relying on numerical allowances. Firestore daily quotas reset around midnight Pacific time, not at month-end; this is not a guarantee that every backend error resolves at reset. If chat fails, keep the map usable and show a clear unavailable state.

Do not add SMS auth, Cloud Functions, paid extensions, uploaded-file storage, or billed TTL retention. The product brief recommends a 30-day retention policy with owner-run cleanup; cadence and implementation still need settling. Hiding old records does not delete them. Account deletion and stored-data deletion require a separate tested process. App Check may be an additional barrier, never a substitute for rules.

## After the proof

1. Produce a short result report and update the technical plan with observed facts and remaining gaps.
2. Complete profile, reporting, moderator, suspension, and data-deletion rules/tests before public posting.
3. Add minimal sign-in and profiles, then the isolated Community panel and bounded chat listener through the existing publishing pipeline.
4. Verify text rendering, inaccessible reports, moderator UID authorization, listener cleanup, error states, and anonymous map use.
5. Run phone/tablet/desktop regressions, including existing drawer sharing and deep links. Confirm hosting-plan eligibility and no billing before an owner-approved invited trial.

Open decisions include the public room name (“War room” was provisional), final provider scope, moderator UID, retention-cleanup cadence, and any change from a small invited trial. Do not turn provisional recommendations into settled requirements.

## Official references to check during setup

These are implementation references retained from the existing planning files, not a fresh pricing audit for this handoff.

- [Firebase pricing](https://firebase.google.com/pricing)
- [Firestore quotas and limits](https://firebase.google.com/docs/firestore/quotas)
- [Google sign-in for web](https://firebase.google.com/docs/auth/web/google-signin)
- [Firestore rules conditions and atomic validation](https://firebase.google.com/docs/firestore/security/rules-conditions)
- [Firestore rules getting started](https://firebase.google.com/docs/firestore/security/get-started)
- [Firebase App Check](https://firebase.google.com/docs/app-check)
- [Vercel plan eligibility](https://vercel.com/docs/plans/hobby)
