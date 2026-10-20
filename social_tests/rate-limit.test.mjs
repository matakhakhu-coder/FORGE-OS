import {readFileSync} from 'node:fs';
import {test, before, beforeEach, after} from 'node:test';
import assert from 'node:assert/strict';
import {initializeTestEnvironment, assertFails, assertSucceeds} from '@firebase/rules-unit-testing';
import {doc, setDoc, getDoc, deleteDoc, writeBatch, serverTimestamp, Timestamp,
  collection, query, limit, getDocs} from 'firebase/firestore';

let env, a, a2, b, mod;
const mp = id => `rooms/main/messages/${id}`;
const message = uid => ({authorId: uid, body: 'Test message', createdAt: serverTimestamp()});
const activity = id => ({lastAt: serverTimestamp(), lastMessageId: id});
before(async () => {
  assert.match(process.env.FIRESTORE_EMULATOR_HOST || '', /^(127\.0\.0\.1|localhost):8080$/,
    'Local emulator required; never run against production');
  env = await initializeTestEnvironment({projectId: 'demo-za-social',
    firestore: {rules: readFileSync(new URL('./firestore.rules', import.meta.url), 'utf8')}});
});
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async ctx => {
    for (const uid of ['a', 'b']) await setDoc(doc(ctx.firestore(), `members/${uid}`), {active: true});
  });
  a = env.authenticatedContext('a').firestore();
  a2 = env.authenticatedContext('a').firestore();
  b = env.authenticatedContext('b').firestore();
  mod = env.authenticatedContext('emulator-moderator-only').firestore();
});
after(async () => {if (env) await env.cleanup();});
function send(db, uid, id, overrides = {}) {
  const batch = writeBatch(db);
  batch.set(doc(db, mp(id)), {...message(uid), ...overrides});
  batch.set(doc(db, `activity/${uid}`), activity(id));
  return batch.commit();
}
async function seedActivity() {
  await env.withSecurityRulesDisabled(ctx => setDoc(doc(ctx.firestore(), 'activity/a'),
    {lastAt: Timestamp.fromMillis(Date.now() - 60000), lastMessageId: 'old'}));
}
async function assertNoMessages(ids, expectActivity = false) {
  await env.withSecurityRulesDisabled(async ctx => {
    const db = ctx.firestore();
    for (const id of ids) assert.equal((await getDoc(doc(db, mp(id)))).exists(), false);
    assert.equal((await getDoc(doc(db, 'activity/a'))).exists(), expectActivity);
  });
}
async function race(count, existing = false) {
  if (existing) await seedActivity();
  const ids = Array.from({length: count}, (_, i) => `race-${i}`);
  const results = await Promise.allSettled(ids.map((id, i) => send(i % 2 ? a2 : a, 'a', id)));
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1, 'Exactly one send must commit');
  for (const r of results) if (r.status === 'rejected') assert.equal(r.reason.code, 'permission-denied');
  await env.withSecurityRulesDisabled(async ctx => {
    const db = ctx.firestore();
    const snapshot = await getDocs(collection(db, 'rooms/main/messages'));
    assert.equal(snapshot.size, 1, 'No denied send leaves an orphan message');
    const act = (await getDoc(doc(db, 'activity/a'))).data();
    assert.equal(act.lastMessageId, snapshot.docs[0].id);
  });
}
test('first atomic send succeeds; immediate repeat fails', async () => {
  await assertSucceeds(send(a, 'a', 'first'));
  await assertFails(send(a2, 'a', 'second'));
});
test('two tabs, first send race', () => race(2));
test('two tabs, existing expired activity race', () => race(2, true));
test('20 concurrent first sends: exactly one commit, no orphan messages', () => race(20));
test('20 concurrent sends with existing activity: exactly one commit', () => race(20, true));
test('different users may send concurrently', async () => {
  await Promise.all([assertSucceeds(send(a, 'a', 'alice')), assertSucceeds(send(b, 'b', 'bob'))]);
});
test('expired 60-second fixture allows send', async () => {
  await seedActivity(); await assertSucceeds(send(a, 'a', 'expired'));
});
test('denied two-message batch leaves no message or activity records', async () => {
  const batch = writeBatch(a);
  batch.set(doc(a, mp('one')), message('a'));
  batch.set(doc(a, mp('two')), message('a'));
  batch.set(doc(a, 'activity/a'), activity('one'));
  await assertFails(batch.commit());
  await assertNoMessages(['one', 'two']);
});
test('denied batch leaves preexisting activity unchanged', async () => {
  await assertSucceeds(send(a, 'a', 'first'));
  await assertFails(send(a, 'a', 'denied'));
  await assertNoMessages(['denied'], true);
  const act = (await getDoc(doc(a, 'activity/a'))).data();
  assert.equal(act.lastMessageId, 'first');
});
test('message alone, activity alone, reset and deletion denied', async () => {
  await assertFails(setDoc(doc(a, mp('alone')), message('a')));
  await assertFails(setDoc(doc(a, 'activity/a'), activity('missing')));
  await assertSucceeds(send(a, 'a', 'valid'));
  await assertFails(deleteDoc(doc(a, 'activity/a')));
  await assertFails(setDoc(doc(a, 'activity/a'), {lastAt: Timestamp.fromMillis(1), lastMessageId: 'valid'}));
});
test('activity pointing at existing message cannot reset interval', async () => {
  await assertSucceeds(send(a, 'a', 'valid'));
  await seedActivity();
  await assertFails(setDoc(doc(a, 'activity/a'), activity('valid')));
});
test('missing, anonymous and suspended members cannot send', async () => {
  await assertFails(send(env.unauthenticatedContext().firestore(), 'a', 'anonymous'));
  await assertFails(send(env.authenticatedContext('outsider').firestore(), 'outsider', 'outsider'));
  await env.withSecurityRulesDisabled(ctx => setDoc(doc(ctx.firestore(), 'members/a'), {active: false}));
  await assertFails(send(a, 'a', 'suspended'));
  await assertFails(setDoc(doc(a, 'members/a'), {active: true}));
});
test('own membership readable even when suspended; others and listing denied', async () => {
  await assertSucceeds(getDoc(doc(a, 'members/a')));
  await env.withSecurityRulesDisabled(ctx => setDoc(doc(ctx.firestore(), 'members/a'), {active: false}));
  assert.equal((await getDoc(doc(a, 'members/a'))).data().active, false);
  await assertFails(getDoc(doc(a, 'members/b')));
  await assertFails(getDocs(collection(a, 'members')));
  const outsider = env.authenticatedContext('outsider').firestore();
  assert.equal((await getDoc(doc(outsider, 'members/outsider'))).exists(), false);
});
test('impersonation and cross-user activity writes denied', async () => {
  await assertFails(send(a, 'b', 'impersonation'));
  await assertFails(setDoc(doc(a, 'activity/b'), activity('missing')));
});
test('forged time, extra fields and invalid body denied', async () => {
  for (const [id, fields] of Object.entries({
    empty: {body: ''}, large: {body: 'x'.repeat(501)},
    forged: {createdAt: Timestamp.fromMillis(1)}, extra: {role: 'admin'}
  })) await assertFails(send(a, 'a', id, fields));
  await assertNoMessages(['empty', 'large', 'forged', 'extra']);
});
test('message updates denied', async () => {
  await assertSucceeds(send(a, 'a', 'valid'));
  await assertFails(setDoc(doc(a, mp('valid')), {body: 'edit'}, {merge: true}));
});
test('own deletion succeeds without resetting activity; cannot immediately repost', async () => {
  await assertSucceeds(send(a, 'a', 'valid'));
  await assertSucceeds(deleteDoc(doc(a, mp('valid'))));
  await assertFails(send(a, 'a', 'too-soon'));
  assert.equal((await getDoc(doc(a, 'activity/a'))).data().lastMessageId, 'valid');
});
test('other user cannot delete; moderator can remove but cannot reset activity', async () => {
  await assertSucceeds(send(a, 'a', 'valid'));
  await assertFails(deleteDoc(doc(b, mp('valid'))));
  await assertSucceeds(deleteDoc(doc(mod, mp('valid'))));
  await assertFails(deleteDoc(doc(mod, 'activity/a')));
  await assertFails(send(a, 'a', 'too-soon'));
});
test('deleting activity plus sending cannot bypass limiter', async () => {
  await assertSucceeds(send(a, 'a', 'valid'));
  const batch = writeBatch(a);
  batch.delete(doc(a, 'activity/a'));
  batch.set(doc(a, mp('bypass')), message('a'));
  await assertFails(batch.commit());
  await assertNoMessages(['bypass'], true);
});
test('bounded queries allowed, unbounded and oversized denied', async () => {
  const room = collection(a, 'rooms/main/messages');
  await assertSucceeds(getDocs(query(room, limit(50))));
  await assertFails(getDocs(query(room, limit(51))));
  await assertFails(getDocs(room));
});
