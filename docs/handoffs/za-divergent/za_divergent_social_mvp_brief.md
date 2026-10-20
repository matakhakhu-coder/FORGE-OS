# ZA Divergent social MVP product brief v2

Add lightweight profiles and one live community chat to the existing map, without requiring payment or changing FORGE’s publishing authority.

**Status:** Version 2, draft for review, not authorization to build. 20 October 2026.

This revision adds a hard 50-message listener cap, moderator UID allowlisting, a security-rules-first engineering gate and corrected daily Firestore quota recovery. It retains zero financial spend and makes no code, account or deployment changes.

## Purpose and audience

ZA Divergent is becoming a portfolio and proof-of-concept project, not a high-volume social network. The first social release should demonstrate that a visitor can become a recognizable participant, return to a conversation, and discuss the map with other people.

The intended audience is a small group of interested visitors, collaborators and portfolio reviewers. Success means a coherent, safe working demonstration, not thousands of accounts or constant chat activity.

“Zero dependency” means zero financial spend. External services are acceptable, but a payment card, paid subscription or usage-billed upgrade is not part of this proposal.

## Current code baseline

The original brief’s local inspection found a clean working tree in the `forge-s4` checkout, on branch `fix/mobile-sprint1-header-map`, at commit `29e25dd`, “Add native sharing and copy-link action to event drawers.” This records the original brief’s source baseline, not a new v2 inspection of the checkout, GitHub or the deployed site.

The relevant source integration points are:

- `publisher/templates/map.html`: map shell, incident drawer and inline map logic/styles. The map container is at line 1135 and Leaflet initialization at line 1556 in this revision.
- `publisher/templates/base.html`: shared header at line 20 and footer at line 96. Changes here can affect other published pages.
- `publisher/static/css/site.css`: shared site styles.
- `publisher/static/css/map-first.css`: map-first layout overrides, loaded by the map template.
- `publisher/static/js/map-first.js`: current header Tools, search and general-menu interactions. New community controls must cooperate with its close/open and keyboard behavior.
- `tools/publish.py`: renders the map template into `dist/index.html` at line 1254 and copies static assets into the published output.
- `vercel.json`: serves `dist` as static files and redirects older screen URLs into map panels.

The social feature should extend these source templates and assets, then use the existing publishing pipeline. Editing only generated `dist` files would risk losing changes on the next build.

FORGE’s unfinished authentication was described in the conversation but was not audited for this brief. Reusing it is not a prerequisite. No code, accounts or deployments were changed while preparing this brief.

## Proposed first release

### Profiles and sign-in

Provide a small account surface with sign-in, sign-out and an editable display name. A profile should establish identity within the community, not imply professional credentials, official authority or eyewitness status.

Recommend Google sign-in for the first trial to avoid operating password recovery and verification-email delivery. This is a recommendation, not a settled exclusion of other sign-in methods. Email/password can follow if broader access is required and its email flows are configured and tested.

Public profile information should be limited to a display name and join date. Keep authentication email addresses private. Defer uploaded avatars, biographies, follower counts, reputation scores and public profile directories.

### One live community chat

Add one shared text room, provisionally called “War room,” with a visible “Community discussion, not verified reporting” notice. Open it from a compact Community control, without rebuilding the map layout.

Recommended initial behavior:

- Visitors can browse the map without an account. Reading and posting in chat require sign-in for the initial small trial.
- Signed-in members see a bounded recent message history, author names and timestamps, and can send plain-text messages.
- Users can delete their own messages and report a message. A moderator can remove messages and suspend posting access.
- Closing chat unsubscribes its live listener. Reopening retrieves recent history, not the entire archive.
- Use a proposed 500-character message limit. Hard-cap every live message listener and history query with `limit(50)`, ordered by timestamp, and enforce a maximum query limit of 50 in security rules. Do not attach an unbounded listener or silently page through the archive. A bounded result set is not a daily usage cap: ongoing message deliveries to every connected client still consume reads.

Do not add direct messages, attachments, voice/video, typing indicators, reactions, multiple rooms or notifications in the first release. Online presence/counts are a later enhancement: an exact count is not necessary to prove chat works, and idle browser tabs make presence more subtle than counting accounts.

## User journeys

1. **Visitor:** opens ZA Divergent and uses the map immediately, without a login prompt blocking access.
2. **New participant:** opens Community, signs in, chooses a display name and accepts the community rules before posting.
3. **Returning participant:** opens the room, sees recent discussion and sends a message that appears in a second signed-in browser without refreshing.
4. **Moderator:** reviews a report, removes harmful content and disables a repeat offender’s posting access.
5. **Service unavailable:** receives a clear chat-unavailable message while the map remains usable.

## Proposed zero-cost architecture

Keep the static map on its existing host. Add Firebase Authentication for identity and Cloud Firestore for profiles, recent chat messages, reports and membership controls. The browser communicates with Firebase through its client SDK; this MVP does not need a new always-running server or a migration of FORGE.

Use the Firebase Spark plan without enabling billing. Treat free service as a quota-bound operating envelope, not a promise of unlimited usage or guaranteed uptime. The exact current account eligibility, authentication configuration and quotas must be confirmed in the provider console before implementation. This brief deliberately does not turn earlier conversational quota estimates into contractual guarantees.

Before Sprint 1, verify official Firebase documentation and the owner’s console together, and capture the Spark plan page showing billing is not enabled. The owner’s project settings and eligibility have not been inspected for v2.

[Firestore’s official quotas documentation](https://firebase.google.com/docs/firestore/quotas) states that free quotas reset daily around midnight Pacific time. Exhausting the daily Firestore read/write allowance can interrupt chat until that next reset, not automatically until month-end. Permission, configuration or other service failures can have different recovery conditions. Keep the map working independently throughout.

Avoid SMS authentication, Firebase Cloud Functions, paid extensions and uploaded-file storage. Do not enable a billed plan just to complete setup. If a required feature cannot work without billing, stop and reduce scope or seek approval for a different design.

Suggested new assets are `publisher/static/js/community.js` and `publisher/static/css/community.css`. Keep social failures isolated from map initialization. Integrate the Community trigger into the existing header/menu behavior, and put its panel markup in the map template. Final control placement should preserve the compact mobile header and existing touch targets.

### Minimal data ownership

- **Firebase Authentication:** credentials and provider identity.
- **Public profiles:** user ID, display name and creation timestamp only.
- **Membership controls:** posting status and moderation state, not writable by ordinary users. Keep a separate tightly validated activity record for the user’s last-post timestamp, updated only as part of an authorized atomic message write. Users cannot clear a suspension through an activity update.
- **Messages:** author user ID, body, server timestamp and room identifier. Render names as untrusted text, never as HTML.
- **Reports:** message reference, reporter ID, reason and timestamp, visible to moderators rather than the room.
- **FORGE:** continues to own its editorial records and publication process. Chat has no write path into those records.

The Firebase browser configuration is not an administrator credential. Access control must come from deployed rules, not from trying to hide browser configuration. Never put service-account credentials, private FORGE data or administrator keys into the static build.

## Community reporting and verification: future candidate

Spidey Tracker was discussed as an inspiration for participation, not an approved requirement to build a sightings system. For this real-world incident map, that idea needs a separate scope and safety review. It is excluded from the first profiles-and-chat release.

A possible later workflow is: a member submits a source-backed correction or report, it enters a private review queue, an editor checks it, and only then may it become an editorial update through FORGE. No submission should publish itself as a map pin.

Keep three concepts distinct:

- **Published FORGE record:** sourced editorial content, carrying its actual provenance and uncertainty. Published does not automatically mean independently confirmed.
- **Community note or report:** user-supplied information, clearly labeled unreviewed and visually separate from editorial details.
- **Reviewed outcome:** an editor’s documented decision, such as insufficient evidence, correction accepted or report supported by identified sources.

Votes, repeated claims, popularity and chat agreement do not verify an incident. Avoid “confirmed” badges without explicit evidentiary criteria. Never simulate real community submissions to make this incident map look populated. A portfolio demo may use clearly labeled synthetic test content, separated from real records.

## Safety, privacy and cost controls

Access rules and moderation are release requirements, not later polish.

- Deny access by default. Require authentication for chat; enforce author identity, allowed fields, body length and server-based timestamps. Prevent users from changing roles or other users’ profiles.
- Enforce posting suspensions in database rules. For the initial one-moderator trial, authorize the owner’s exact Firebase Authentication UID through an allowlist in the deployed security rules. Never authorize by display name, editable profile role or a client-side flag. Updating the allowlist requires a controlled rules deployment. No custom-claims script or service-account key is needed for this design.
- Apply a rules-enforced per-user posting interval using a tightly constrained activity record updated atomically with each message. Use batched writes and `get`/`getAfter` checks as appropriate. Before building chat UI, prove with emulator tests that direct SDK writes, concurrent sends, activity-only resets, forged timestamps and attempts to clear suspensions fail. A disabled send button is not a security boundary. Reserve extra implementation and review time for this gate.
- Use Firebase App Check where suitable as an additional abuse barrier, not a substitute for access rules or a guarantee against quota exhaustion.
- Test that users cannot read reports, impersonate authors or make arbitrary database queries. Keep chat history queries bounded and indexed.
- Begin with a small invited trial. Do not open unrestricted public posting until removal, reporting, suspensions and a chat-disable mechanism work.
- Publish plain rules against doxxing, threats, graphic uploads and unsupported accusations against identifiable people. Do not solicit precise live locations of vulnerable people.
- Explain what account/chat data is stored, what is visible, and how deletion requests are handled. Account deletion and removal of chat/profile records are separate operations and need a tested process.
- Recommend a 30-day chat retention policy, with owner-run manual cleanup for this MVP. [Firestore’s quota documentation](https://firebase.google.com/docs/firestore/quotas) lists TTL deletes as requiring billing and outside free usage, so automatic TTL deletion does not fit the Spark-only constraint. Do not enable billing for retention. Specify the cleanup cadence and owner before trial release, and verify actual stored-data deletion; hiding old messages is not retention enforcement.
- Review the usage dashboard during the first multi-client test and throughout the invited trial. Closing listeners and limiting history reduce reads; a small user count alone does not eliminate quota risk. If quota is exhausted, stop social writes, show an unavailable state and keep the map independent. Avoid aggressive automatic retries. Daily quota recovery is expected after the next daily reset, not guaranteed recovery from every backend error.

Confirm that the existing Vercel plan permits the intended use before launch. A personal portfolio demonstration and a commercial studio service may have different hosting eligibility. No hosting-plan change is authorized here.

## Delivery sprints and acceptance gates

### Sprint 1: identity and configuration

Confirm the no-billing Firebase setup, sign-in method, authorized domains, data region and project ownership, retaining the plan-page evidence. Before social UI implementation, draft the schema and security rules and run emulator tests for identity, moderator UID allowlisting, suspensions, the atomic posting interval, bounded message queries and report isolation. Only after this gate passes, add sign-in/out and minimal profiles in a non-public trial.

**Pass:** the rules-first emulator suite passes, including concurrent posting and forged activity updates; two test users have separate profiles; neither can edit the other’s profile or membership controls, grant moderator rights or query messages without an allowed limit; authentication failure does not break the map.

### Sprint 2: one-room chat

Add the Community panel, bounded message history, live updates and accessible loading/error states. Integrate with existing header behavior and mobile layout.

**Pass:** a message appears in a second browser without refresh; every history/live query uses `limit(50)` and oversized or unbounded queries are rejected by rules; invalid writes fail through direct SDK requests; closing the panel stops its listener; early dashboard usage is reviewed; map controls and incident drawers still work.

### Sprint 3: moderation and privacy

Implement reports, message removal, posting suspension, abuse controls, a practical disable procedure and account/data deletion handling. Publish short community and privacy notices.

**Pass:** only allowlisted moderator UIDs can moderate or read reports; ordinary accounts cannot grant themselves authority; suspended members cannot post or reset their status; HTML-like messages display as text; account/data deletion and the owner-run 30-day retention procedure are exercised.

### Sprint 4: portfolio release gate

Run phone, tablet and desktop regression checks through the real publishing pipeline. Test sign-in redirects, session persistence, empty chat, backend outage and quota/permission errors. Prepare a short demo with labeled test accounts.

**Pass:** public map browsing remains anonymous and functional; social features degrade gracefully; no billing is enabled; the owner approves a small trial before public posting.

## Effort and sequencing

Do not treat faster assisted coding or rapid approvals as a guaranteed shorter delivery time. Security-rule validation, provider setup, moderation and regression testing remain substantive work. Estimate effort after the rules-first gate exposes the actual complexity, with extra contingency for atomic rate limiting. No fixed delivery date is promised.

## Decisions before implementation

Approve this product scope first. Then settle the initial sign-in option, the room’s public-facing name, whether the trial is invitation-only, the owner’s moderator UID, and the retention cleanup cadence. The v2 technical recommendation is a moderator UID allowlist, a hard 50-message query cap and manual 30-day retention cleanup without billing. Provider setup will require the owner’s account and any necessary sign-in or approvals.

The next engineering deliverable is a lightweight technical plan covering schema, security rules, UI states and test cases, followed by the rules-first emulator gate before UI work. This brief authorizes neither account provisioning nor code changes.

## References for the implementation gate

V2 checked the official Firestore quota documentation for daily resets and the billing requirement for TTL deletes. Other current prices, authentication allowances, hosting eligibility and the owner’s console configuration remain implementation-gate checks, not freshly validated guarantees:

- [Firebase pricing and Spark plan](https://firebase.google.com/pricing)
- [Firestore quotas and limits](https://firebase.google.com/docs/firestore/quotas)
- [Firebase Authentication for web](https://firebase.google.com/docs/auth/web/start)
- [Firestore security rules](https://firebase.google.com/docs/firestore/security/get-started)
- [Firebase App Check](https://firebase.google.com/docs/app-check)
- [Vercel Hobby plan](https://vercel.com/docs/plans/hobby)
