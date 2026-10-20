# ZA Divergent chat rate-limit rule and concurrency tests

Draft a server-enforced posting interval before building the chat interface. This note contains a proposed Firestore rule and executable emulator-test source, not a tested or deployed implementation.

**Status:** Written for review on 20 October 2026. No emulator tests were run, no Firebase project was configured and no repository or live-site code was changed. This is the rate-limit slice, not the complete social technical plan.

## Contract and scope

Recommend **one message per account every 10 seconds** in the single `main` room. Ten seconds is a proposed starting value, not a previously approved requirement. Two browser tabs using the same account share that interval. Different accounts have separate intervals. This does not prevent multi-account abuse or cap total daily reads.

Each send is one atomic batch containing a new message and the account's activity record. Both documents point to the same message ID and use `serverTimestamp()`. The activity record cannot be deleted or reset by a client. A new activity record is allowed only alongside the account's first valid message.

An owner must provision `members/{uid}` with exactly `{active: true}` for invited users. Members cannot change their own access status. Set `active: false` to suspend posting and room reads. Missing membership fails closed. This isolated rule deliberately denies profiles, reports, moderation writes and message deletion; the full ruleset must add those features without weakening the send invariant. Owner provisioning here means a trusted console operation, not a hidden client bypass.

Document shapes:

- `members/{uid}`: `active` boolean, provisioned by the owner.
- `activity/{uid}`: `lastAt` timestamp and `lastMessageId` string, written only with a valid send.
- `rooms/main/messages/{messageId}`: `authorId` string, `body` string of 1 to 500 characters, and `createdAt` timestamp.

Whitespace-only content should additionally be rejected in the interface; this slice validates size, not a complete content policy. Render every message as text, never HTML. Names belong to a separate validated profile design.

## Proposed firestore.rules

The reciprocal checks matter: an activity update must reference a newly created message, and each new message must be the activity record's one selected message. A batch containing two messages cannot make both IDs equal the one activity pointer.

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function member() {
      return request.auth != null
        && get(/databases/$(database)/documents/members/$(request.auth.uid)).data.active == true;
    }
    function messagePath(id) {
      return /databases/$(database)/documents/rooms/main/messages/$(id);
    }
    function activityPath() {
      return /databases/$(database)/documents/activity/$(request.auth.uid);
    }
    function validMessage(m) {
      return m.keys().hasAll(['authorId', 'body', 'createdAt'])
        && m.keys().hasOnly(['authorId', 'body', 'createdAt'])
        && m.authorId == request.auth.uid
        && m.body is string && m.body.size() > 0 && m.body.size() <= 500
        && m.createdAt == request.time;
    }
    function intervalPassed() {
      return !exists(activityPath())
        || request.time >= get(activityPath()).data.lastAt + duration.value(10, 's');
    }
    function validActivity(a) {
      return a.keys().hasAll(['lastAt', 'lastMessageId'])
        && a.keys().hasOnly(['lastAt', 'lastMessageId'])
        && a.lastAt == request.time
        && a.lastMessageId is string
        && a.lastMessageId.size() > 0 && a.lastMessageId.size() <= 128
        && a.lastMessageId.matches('^[A-Za-z0-9_-]+$');
    }
    match /rooms/main/messages/{id} {
      allow get: if member();
      allow list: if member()
        && request.query.limit != null
        && request.query.limit > 0 && request.query.limit <= 50;
      allow create: if member()
        && validMessage(request.resource.data)
        && intervalPassed()
        && validActivity(getAfter(activityPath()).data)
        && getAfter(activityPath()).data.lastMessageId == id;
      allow update, delete: if false;
    }
    match /activity/{uid} {
      allow get: if member() && request.auth.uid == uid;
      allow list: if false;
      allow create, update: if member() && request.auth.uid == uid
        && validActivity(request.resource.data)
        && intervalPassed()
        && !exists(messagePath(request.resource.data.lastMessageId))
        && validMessage(getAfter(messagePath(request.resource.data.lastMessageId)).data);
      allow delete: if false;
    }
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

A client send generates one message document reference, then batches `set(message, {authorId, body, createdAt: serverTimestamp()})` with `set(activity, {lastAt: serverTimestamp(), lastMessageId: message.id})`. Do not send these as separate writes. Do not retry denied sends automatically. Do not weaken rules to make an offline queue flush successfully.

Use `orderBy('createdAt', 'desc'), limit(50)` for the room listener and detach it when the panel closes. The rule limits query size, not access to only the newest 50 records: a malicious admitted client can issue repeated bounded queries. This is not a scraping or quota guarantee.

## Executable concurrency-test source

Save the rules block as `firestore.rules` and the following block as `rate-limit.test.mjs` in an isolated local test directory. Install `firebase`, `@firebase/rules-unit-testing` and `firebase-tools` there. Use a compatible Node runtime and the Java runtime required by the current Firestore emulator. No production credentials or paid project are required for these demo-project tests.

Use `firebase.json` with `{"firestore":{"rules":"firestore.rules"},"emulators":{"firestore":{"port":8080}}}`. Run `npx firebase emulators:exec --only firestore --project demo-za-social "node --test --test-concurrency=1 rate-limit.test.mjs"`. Keep the `demo-` project ID and emulator host guard. Resolve any compilation or assertion failures before calling the rule verified.

```javascript
import {readFileSync} from 'node:fs';
import {test, before, beforeEach, after} from 'node:test';
import assert from 'node:assert/strict';
import {initializeTestEnvironment, assertFails, assertSucceeds}
  from '@firebase/rules-unit-testing';
import {doc, setDoc, deleteDoc, writeBatch, serverTimestamp, Timestamp,
  collection, query, limit, getDocs} from 'firebase/firestore';

let env;
let a, a2, b;
const mp = id => `rooms/main/messages/${id}`;
const data = uid => ({authorId: uid, body: 'Test message', createdAt: serverTimestamp()});

before(async () => {
  assert.ok(process.env.FIRESTORE_EMULATOR_HOST, 'Emulator host is required');
  env = await initializeTestEnvironment({
    projectId: 'demo-za-social',
    firestore: {rules: readFileSync('firestore.rules', 'utf8')}
  });
});
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async ctx => {
    for (const uid of ['a', 'b'])
      await setDoc(doc(ctx.firestore(), `members/${uid}`), {active: true});
  });
  a = env.authenticatedContext('a').firestore();
  a2 = env.authenticatedContext('a').firestore();
  b = env.authenticatedContext('b').firestore();
});
after(async () => { if (env) await env.cleanup(); });

function send(db, uid, id) {
  const batch = writeBatch(db);
  batch.set(doc(db, mp(id)), data(uid));
  batch.set(doc(db, `activity/${uid}`), {
    lastAt: serverTimestamp(), lastMessageId: id
  });
  return batch.commit();
}
async function oldActivity(uid, ageSeconds) {
  await env.withSecurityRulesDisabled(ctx => setDoc(
    doc(ctx.firestore(), `activity/${uid}`), {
      lastAt: Timestamp.fromMillis(Date.now() - ageSeconds * 1000),
      lastMessageId: 'old-message'
    }));
}

test('first atomic send succeeds; immediate repeat fails', async () => {
  await assertSucceeds(send(a, 'a', 'first'));
  await assertFails(send(a, 'a', 'second'));
});
test('two tabs: exactly one concurrent first send succeeds', async () => {
  const results = await Promise.allSettled([
    send(a, 'a', 'tab-one'), send(a2, 'a', 'tab-two')
  ]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  const denied = results.find(r => r.status === 'rejected');
  assert.equal(denied.reason.code, 'permission-denied');
});
test('existing expired activity: exactly one concurrent send succeeds', async () => {
  await oldActivity('a', 60);
  const results = await Promise.allSettled([
    send(a, 'a', 'next-one'), send(a2, 'a', 'next-two')
  ]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal(results.find(r => r.status === 'rejected').reason.code, 'permission-denied');
});
test('different users can send concurrently', async () => {
  await Promise.all([
    assertSucceeds(send(a, 'a', 'alice')),
    assertSucceeds(send(b, 'b', 'bob'))
  ]);
});
test('expired interval allows a send', async () => {
  await oldActivity('a', 60);
  await assertSucceeds(send(a, 'a', 'after-wait'));
});
test('one activity pointer cannot authorize two messages in one batch', async () => {
  const batch = writeBatch(a);
  batch.set(doc(a, mp('one')), data('a'));
  batch.set(doc(a, mp('two')), data('a'));
  batch.set(doc(a, 'activity/a'), {
    lastAt: serverTimestamp(), lastMessageId: 'one'
  });
  await assertFails(batch.commit());
});
test('neither half can be written alone; activity cannot be deleted', async () => {
  await assertFails(setDoc(doc(a, mp('alone')), data('a')));
  await assertFails(setDoc(doc(a, 'activity/a'), {
    lastAt: serverTimestamp(), lastMessageId: 'missing'
  }));
  await assertSucceeds(send(a, 'a', 'valid'));
  await assertFails(deleteDoc(doc(a, 'activity/a')));
});
test('suspension and self-reactivation attempts fail', async () => {
  await env.withSecurityRulesDisabled(ctx =>
    setDoc(doc(ctx.firestore(), 'members/a'), {active: false}));
  await assertFails(send(a, 'a', 'suspended'));
  await assertFails(setDoc(doc(a, 'members/a'), {active: true}));
});
test('impersonation, anonymous access and uninvited access fail', async () => {
  await assertFails(send(a, 'b', 'impersonation'));
  await assertFails(send(env.unauthenticatedContext().firestore(), 'a', 'anonymous'));
  await assertFails(send(env.authenticatedContext('outsider').firestore(), 'outsider', 'outsider'));
});
test('forged timestamps fail even in a reciprocal batch', async () => {
  const batch = writeBatch(a);
  const forged = Timestamp.fromMillis(1);
  batch.set(doc(a, mp('forged')), {...data('a'), createdAt: forged});
  batch.set(doc(a, 'activity/a'), {lastAt: forged, lastMessageId: 'forged'});
  await assertFails(batch.commit());
});
test('bounded query allowed; oversized and unbounded queries denied', async () => {
  const room = collection(a, 'rooms/main/messages');
  await assertSucceeds(getDocs(query(room, limit(50))));
  await assertFails(getDocs(query(room, limit(51))));
  await assertFails(getDocs(room));
});
```

## Remaining validation before implementation

The source above is a starting suite, not a claim of coverage or success. Add tests for: activity update referencing an already existing message; mismatched message ID; activity reset with an older timestamp; empty and 501-character bodies; extra fields; message overwrite; cross-user activity mutation; deletion and recreation attempts; 20 concurrent sends from one account; and full-batch rollback with no orphan records after denial. Repeat concurrency trials to expose intermittent behavior.

Test just below and just above the 10-second boundary using controlled emulator fixtures, allowing for elapsed wall-clock time. The 60-second fixture in the supplied tests deliberately avoids a flaky boundary assertion. Test offline reconnect and queued writes separately; a rejected queue must not generate a retry storm. Test suspension while a panel is open and while a send is pending. Existing cached content cannot be remotely erased by revoking database read access.

Before merging with a broader ruleset, rerun every negative case. Firestore allows an operation if any matching allow condition grants it; a broad moderator or collection rule can accidentally bypass this limiter. Decide message deletion, account-data cleanup and moderator permissions in that broader design, not by permitting deletion of activity records.

## Read costs and limits

Every accepted send writes two documents. Rule evaluation also accesses membership, activity and the referenced message. Dependent document reads can consume quota even on rejected requests; caching can reduce repeated accesses, so do not assume one charge per function call or quote a fixed cost without measuring. Live delivery adds reads across all listening clients, and reconnects/history loads add more.

The draft is designed to remain within the documented 10 document-access calls per operation and 20 per atomic request. Verify the actual compiled rules and emulator evaluation before relying on that budget. Review real usage in the invited trial; the emulator is not a production billing meter. No paid service, billing enablement or service-account key is part of this proposal.

## Official reference

[Firestore security-rule conditions and atomic-operation validation](https://firebase.google.com/docs/firestore/security/rules-conditions) explains `getAfter()`, dependent-read charges, caching and access-call budgets. The focused documentation lookup for this note confirmed the 10-per-operation and 20-per-batch limits. The supplied rule and test code have not been compiled or executed.
