# Firebase test agent handoff

We are verifying Firestore security rules and chat rate limiting for ZA Divergent in an isolated, zero-spend Firebase test project. We are starting at the Firestore database setup check: open Firestore and, if a database needs creating, stop at the location screen before choosing a region.

## Task and boundaries

Help me finish setting up the test database and verify the rules against real Firebase, especially concurrent message sends. This is testing for a profiles-and-chat MVP on ZA Divergent, a map site fed by FORGE. Do not build the social UI, change the live map, enable billing, or broaden the task.

Work directly with me in small steps. I use Windows PowerShell. Give me one bounded console action or command at a time and wait for its result. Do not delegate. Do not restart completed setup or describe an unrun test or deployment as complete.

## Completed setup

- Repository: C:\Users\matam\projects\forge, branch main, remote https://github.com/matakhakhu-coder/FORGE-OS.git.
- Firebase CLI version: 15.34.0. Login completed as matamelaramovha8@gmail.com, confirmed by firebase login:list.
- Test project: za-divergent-chat-test, display name Za-divergent chat test, project number 739080124157. It appears in firebase projects:list.
- The console screenshot was reported to show the Spark plan badge and one registered web app. Treat Spark as the established plan, keep the zero-spend constraint, and do not upgrade to Blaze. The badge itself is not a separate inspection of billing-account linkage.
- Housekeeping is complete: .gitignore excludes node_modules/ and *-debug.log. Commit e0ebe5c was pushed successfully, advancing main from 3a3adb3. Do not repeat the ignore-file edits, commit, push, or CLI login.
- A UID helper is reported to have the web app API key and auth domain filled in. Inspect the actual helper before using it; a verified application-user UID is not established by CLI login.

## Completed local tests

The supplied test summary reports 20 tests, 20 passed, 0 failed, 0 skipped, 8.3 seconds, exit code 0. The pasted terminal log independently shows passing individual cases, including the 20-concurrent-first-send test with exactly one commit and no orphan messages.

The local run used npm ci followed by npm test in C:\Users\matam\projects\forge\social_tests. The test command was firebase emulators:exec --only firestore --project demo-za-social "node --test --test-concurrency=1 rate-limit.test.mjs".

Covered cases:

- First atomic send succeeds and an immediate repeat fails.
- Two-tab first-send and existing-expired-activity races.
- Twenty-send races with no activity and with existing activity, including no orphan messages.
- Separate users and expired intervals.
- Denied-batch rollback, non-members, impersonation, and forged time.
- Deletion rules and bounded queries.

PERMISSION_DENIED messages accompanied deliberate forbidden requests in the passing tests. These results establish local emulator behavior only, not real-project concurrency correctness. Do not rerun the whole suite merely to rediscover this result; rerun when changes require it.

The install completed despite cleanup warnings. npm reported 22 vulnerabilities, including 1 critical, in the test dependency installation; do not assume the dependency tree is safe or run npm audit fix --force blindly. Inspect npm audit separately if needed. The emulator ran with a warning about Java below 21 losing support; a JDK 21 upgrade is a tooling follow-up, not evidence that this run failed.

## Exact starting point

The last requested action is the Firestore console check. Database existence, edition, region, and access mode have not yet been established. Do not assume the location screen has already been reached.

Start here, without repeating login or housekeeping:

1. Open console.firebase.google.com and select za-divergent-chat-test. Expand the collapsed sidebar if necessary, then open Build → Firestore Database.
2. If a database already exists, ask me for its region and edition and inspect its current rules. Do not create another database or assume its access mode.
3. If there is a Create database button, guide me into setup and stop at the location screen. Ask for a screenshot or the offered regions and edition choices. Do not choose a region or finalize creation yet.
4. Check current official Firebase documentation for the selected edition, location availability, and no-cost quota eligibility before recommending a choice. The audience is South African, so evaluate Johannesburg if offered, without assuming it is available or eligible. Treat the location as a permanent choice and obtain my approval before proceeding.
5. When creation is approved, use production mode rather than permissive test mode. Do not deploy custom rules until I have approved the exact deployment.

Resource Location ID being Not specified in the CLI project list does not prove that Firestore is absent or identify its region.

## After the database check

Only after the region and creation decision is settled, check Authentication and enable Google sign-in if needed and approved, then inspect and run the UID helper. Verify the intended moderator's application Auth UID in this test project before inserting it into an allowlist. Do not treat an inherited UID value as verified.

Inspect the actual Firebase configuration, firestore.rules, and social_tests files before preparing deployment or a real-project integration test. Preserve the emulator moderator placeholder if tests depend on it. Use a temporary deployment file/configuration or an explicitly backed-up workflow rather than a blind replacement followed by git restore, which could discard local edits.

Every remote command must explicitly target za-divergent-chat-test. Seeing only one project in a listing is not a wrong-target safeguard. Deploy only approved Firestore rules, never hosting or unrelated services. Do not run firebase init over existing configuration.

For live concurrency verification, use authenticated client SDK requests subject to Security Rules, not Admin SDK writes that bypass them. Ensure the test connects to the real test project, not the emulator. Read the actual cap, cooldown, schema, and atomic-write invariants before specifying expected outcomes. Use bounded requests and isolated test records; inspect both committed messages and rate-limit state. Clean up only records created for the test. No live-project rules deployment or successful live concurrency test is established yet.

## Preserve unrelated files

The last shown working state included modified data/fic_tfs.xml and data/sdn.csv, plus untracked audit-forge.ps1, find-overlap.ps1, forge_mcp/, publish-mechanism.ps1, and tests/test_sprint6_gravity.py. Leave them untouched. Check status before edits and stage only intended paths. Never request passwords, CLI tokens, or service-account private keys in chat.

Begin by guiding me to the Firestore setup check above. Continue from that result, not from the beginning of the project.
