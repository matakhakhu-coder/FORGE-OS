# ZA Divergent live Firebase setup sprint

Set up a separate live Firebase project, connect ZA Divergent to it, and release profiles and chat only after configuration, security and browser checks pass.

## Purpose and boundaries

This is a handoff for the agent working with you on your computer. It is a plan, not confirmation that setup or deployment has happened. Work through the 12 tasks below in order. Keep the existing `za-divergent-chat-test` project isolated for tests. A separate live project is a recommendation for isolation, not a Firebase requirement.

Live site identified in the preceding work: `https://forge-os-alpha.vercel.app`. Confirm that this is still the intended site before configuring anything. Add any custom domain only if it is actually used.

Do not import the website into Firebase or move its hosting from Vercel. The website connects to Firebase through the Firebase SDK and live project's web app configuration. Authorizing a hostname permits sign-in from that hostname; it does not connect the website to Firestore.

## Starting evidence and unresolved items

The preceding session reported GitHub main at commit `6f7b971`, containing changes to `social_tests/firestore.rules` and `social_tests/v2-security.test.mjs`. The combined local suite passed 30 tests with no failures or skips. The existing message/activity rules were preserved while profile and report rules were added. The computer agent must fetch and inspect the current repository rather than assume that commit is still the latest.

The candidate rules still use `emulator-moderator-only`. This is a fixture, not the real owner's UID, and must never be deployed. Earlier tests do not constitute live-release clearance. The deployed rules, live owner identity, Authentication setup and browser integration still need verification.

Do not assume the social interface already exists. Earlier repository inspection identified a UID sign-in test page, not proof of a complete profiles/chat interface. Configuration replacement alone may not be sufficient; inspect first.

## Phase 1: Establish the live backend

### 1. Inspect the repository and agree the target

- [ ] Fetch current GitHub main and inspect working-tree changes without discarding them.
- [ ] Identify the Vercel project, production hostname, Firebase initialization code, social UI and existing configuration mechanism.
- [ ] Record the intended live Firebase project ID separately from `za-divergent-chat-test` and the emulator project `demo-za-social`.
- [ ] Keep emulator-only configuration and test scripts separate from production settings.

**Completion evidence:** repository revision, correct Vercel project/hostname, configuration entry point, and list of any missing social screens.

### 2. Create the separate live Firebase project

- [ ] Create a clearly named live project in the user's Google account. Let Firebase assign or validate its unique project ID; do not invent an existing ID.
- [ ] Keep the project on the no-cost Spark plan if it supports the chosen implementation. Do not link billing or enable paid services without explicit approval.
- [ ] Do not enable optional services such as Analytics unless needed and approved.
- [ ] Record project name and project ID in the handoff, not passwords or private credentials.

**Completion evidence:** live project exists, correct account owns it, and plan is confirmed.

### 3. Register the web app and capture its configuration

- [ ] Register ZA Divergent as a web app within the live project. Firebase Hosting is not required for Vercel hosting.
- [ ] Copy the exact SDK configuration supplied by Firebase, including its project ID, API key, auth domain and app ID, plus any other fields supplied for that app.
- [ ] Map these fields to the repository's actual configuration system. Do not assume environment-variable names or framework conventions.
- [ ] Never put service-account keys, passwords, refresh tokens or private credentials in browser code, commits or chat. Firebase web configuration is client configuration, not privileged server access.

**Completion evidence:** live web app is registered and configuration mapping is documented without switching the public site yet.

### 4. Create the live Firestore database

- [ ] Create the default Firestore database unless the inspected implementation explicitly requires another database ID. Do not create an extra database inside the test project as a substitute for the separate live project.
- [ ] Confirm the appropriate database edition and regional availability in the console. Johannesburg was used for the test project; confirm the live location before creation because location decisions are difficult to reverse.
- [ ] Start with restrictive rules, not open test-mode access.
- [ ] Keep test records out of the live database. Do not enable paid backups or other billable options without approval.

**Completion evidence:** correct live project, database ID, location and restrictive initial access recorded.

### 5. Configure Authentication and authorized domains

- [ ] Enable Google sign-in in the live project and complete the required support/contact settings.
- [ ] Authorize the hostname `forge-os-alpha.vercel.app` if confirmed as the actual live site. Enter the hostname, not an entire URL with a path.
- [ ] Add a real custom hostname only if the app also uses it. Treat Vercel preview deployments separately; do not assume arbitrary preview hostnames are authorized.
- [ ] Authorize localhost only if needed for local development. Check the SDK's configured `authDomain` and the selected sign-in flow rather than replacing it with the Vercel hostname blindly.
- [ ] Preserve the test project's existing setup. Do not copy its deleted test accounts into live Authentication.

**Completion evidence:** Google provider enabled and exact intended hostnames authorized in the live project.

## Phase 2: Prepare a release candidate

### 6. Verify the real owner and provision access

- [ ] Sign the owner into the live project through the intended authentication flow and obtain their actual live-project Firebase Auth UID.
- [ ] Do not assume a UID from the separate test project identifies the same account in live.
- [ ] Verify that UID against the live project's Authentication user record before assigning moderator authority.
- [ ] Create only the deliberately approved `members/{uid}` documents, with the fields required by the inspected rules, including `active: true` for an invited active member.
- [ ] Use the trusted owner console or an authorized administrative process for membership. Browser clients must not be able to grant membership or moderator authority.
- [ ] Ensure moderator status alone does not accidentally imply permission to post; inspect whether active membership is also needed.

**Completion evidence:** verified live owner UID and intentional initial membership, with no emulator fixtures or automatic public enrollment.

### 7. Prepare the deployable rules and explicit target

- [ ] Reuse the tested message/activity, profile and report logic, but replace the moderator fixture with the verified live UID in a clearly separated release candidate.
- [ ] Review any difference between the current live rules and the candidate. A new project may only have its initial restrictive rules; do not assume it already has V2.
- [ ] Check that the rules preserve atomic message/activity writes, the cooldown, bounded message queries, profile restrictions and moderator-only report reading.
- [ ] Ensure Firebase CLI configuration points to the intended rules file and the live project explicitly. Do not infer the target from whichever project was used last.
- [ ] Keep emulator identity fixtures isolated; do not silently alter tests to make a production UID appear verified.

**Completion evidence:** reviewed rules diff, verified owner mapping, and unambiguous deployment target. No deployment yet.

### 8. Wire the application to the live backend

- [ ] Use the configuration entry point found in task 1 to connect the social application to the live project's SDK configuration.
- [ ] Confirm authentication and Firestore instances belong to the same live Firebase app/project.
- [ ] Ensure production code never connects to the emulator or uses the test project's IDs.
- [ ] If social screens are missing, implement the minimum sign-in, own-profile, bounded chat list/send/delete, reporting and moderator review flows before calling the integration complete.
- [ ] Preserve map functionality and mobile layout. Handle signed-out, uninvited, suspended and permission-denied states clearly.
- [ ] Match the client to the actual rules: atomic message/activity sends, allowed profile fields and supported report reasons. Do not request forbidden collection-wide profile listings.
- [ ] Scope Vercel configuration correctly; preview builds must not accidentally write to live data. Do not publish production changes yet.

**Completion evidence:** reviewed application/configuration diff and a testable, non-public release candidate.

### 9. Rerun local tests and browser checks

- [ ] Install dependencies using the repository lockfile and run both test files, not only the default npm script if it runs the original suite alone.
- [ ] Run the isolated command from `social_tests` when compatible with the inspected setup:

```sh
npx firebase emulators:exec --only firestore --project demo-za-social "node --test --test-concurrency=1 rate-limit.test.mjs v2-security.test.mjs"
```

- [ ] Require all existing 30 tests to pass and add coverage for any changed behavior. Also verify the release candidate's rule logic and moderator mapping without changing a test merely to hide a failure.
- [ ] Check build/lint commands that actually exist, and run `git diff --check`.
- [ ] Check the candidate interface on desktop and mobile, including sign-in state, profile display, chat empty/error states and map interaction. Emulator browser checks do not prove live Google sign-in works.

**Completion evidence:** exact commands, pass/fail counts, exit codes, tested revision and honest list of blocked browser checks.

## Phase 3: Controlled release and verification

### 10. Stop for release approval

Present the exact live project/database, intended rules change, verified moderator identity, application changes and any remaining limitations. Obtain approval before deploying Firebase rules, publishing the UI or making live smoke-test writes. A request to create this sprint file is not deployment authorization.

Save the current rules/configuration or identify a known restrictive rollback before proceeding. Do not promise rollback will remove data already written.

**Completion evidence:** explicit approval for the named release and its small, bounded verification writes.

### 11. Deploy verified rules and perform Live Verification

- [ ] Deploy only the reviewed rules to the explicitly selected live project. Publish the approved client/configuration changes in the agreed order while keeping incomplete social features unavailable to public users.
- [ ] Rebuild/redeploy Vercel as required for changed build-time configuration. Editing environment variables alone may not change the running build.
- [ ] On the real hostname, verify Google sign-in reaches the intended live project, then sign out and verify the signed-out state.
- [ ] **Minimum end-to-end Live Verification:** sign in on the real site with one real, approved active member account and attempt one short synthetic post. Require a successful persisted message and its matching activity record in the intended live database, then reload and confirm the message is still visible. A successful sign-in by itself is not enough. If the post is denied, inspect the exact project, UID, membership and atomic write shape; do not weaken the rules.
- [ ] With approved real accounts, also verify invited member access, own-profile creation/rename, cooldown behavior, own-message deletion, reporting and moderator report access.
- [ ] Confirm an uninvited account cannot enter member-only data and cannot grant itself access. Check suspension and non-moderator report denial only through approved, bounded tests.
- [ ] Use minimal synthetic messages with no personal data. Do not repeat 20-client stress tests on live infrastructure without separate approval.
- [ ] Clean up approved test content through the allowed process. Do not delete activity records to reset the limiter or loosen rules to make cleanup easier.
- [ ] Check database usage and console errors. Treat dependency/rules reads and repeated reads as usage, not as free operations by assumption.

**Completion evidence:** real-host browser results, live target confirmation, content cleanup status and any residual test records. Any blocked test stays marked unverified.

### 12. Close out and hand back

- [ ] Record project ID, database location, web app configuration mapping, authorized hostname(s), owner UID verification status, repository commit and deployment results.
- [ ] Record emulator evidence separately from live browser evidence.
- [ ] Confirm the original test project remains separate and the live client uses only the approved live configuration.
- [ ] State remaining limitations, access model, usage checks and rollback procedure.
- [ ] Never label the system secure against every attack or production-ready solely because 30 emulator tests pass.

**Sprint done means:** the separate live backend exists, the app is connected intentionally, release approvals were obtained, and the bounded live checks passed with evidence. A new project, a database or an authorized hostname alone does not complete the sprint.

## Instructions for the computer agent

Execute one phase at a time and keep a checked progress log. Inspect the actual repository and console before deciding exact settings or commands. Ask the owner only for required choices, sign-in and approval; never request passwords in chat. Stop on an unexpected project ID, test-only moderator fixture, paid-service requirement or failed security check. Do not migrate test data, add unrelated features or deploy merely because this plan mentions deployment.
