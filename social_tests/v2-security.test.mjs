import {readFileSync} from 'node:fs';
import {before, beforeEach, after, test} from 'node:test';
import assert from 'node:assert/strict';
import {initializeTestEnvironment, assertSucceeds, assertFails} from '@firebase/rules-unit-testing';
import {doc, collection, setDoc, updateDoc, deleteDoc, getDoc, getDocs, query, limit, serverTimestamp, Timestamp} from 'firebase/firestore';
let env, a, b, mod, outsider, anon;
const profile = displayName => ({displayName, createdAt: serverTimestamp()});
const report = (reporterId = 'a', messageId = 'seed') => ({reporterId, messageId, reason: 'spam', createdAt: serverTimestamp()});
const ref = (db, path) => doc(db, path);
before(async () => {
  assert.match(process.env.FIRESTORE_EMULATOR_HOST || '', /^(127\.0\.0\.1|localhost):8080$/, 'Local emulator required');
  env = await initializeTestEnvironment({projectId: 'demo-za-social', firestore: {rules: readFileSync(new URL('./firestore.rules', import.meta.url), 'utf8')}});
});
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async ctx => {
    const db = ctx.firestore();
    for (const uid of ['a', 'b']) await setDoc(ref(db, `members/${uid}`), {active: true});
    await setDoc(ref(db, 'rooms/main/messages/seed'), {authorId: 'a', body: 'Synthetic test message', createdAt: Timestamp.fromMillis(1)});
  });
  a = env.authenticatedContext('a').firestore();
  b = env.authenticatedContext('b').firestore();
  mod = env.authenticatedContext('emulator-moderator-only').firestore();
  outsider = env.authenticatedContext('outsider').firestore();
  anon = env.unauthenticatedContext().firestore();
});
after(async () => {if (env) await env.cleanup();});
async function suspendA() {
  await env.withSecurityRulesDisabled(ctx => updateDoc(ref(ctx.firestore(), 'members/a'), {active: false}));
}
test('two members create separate profiles and read display names', async () => {
  await assertSucceeds(setDoc(ref(a, 'profiles/a'), profile('Alice')));
  await assertSucceeds(setDoc(ref(b, 'profiles/b'), profile('Bob')));
  const p = await assertSucceeds(getDoc(ref(a, 'profiles/b')));
  assert.equal(p.data().displayName, 'Bob');
  assert.deepEqual(Object.keys(p.data()).sort(), ['createdAt', 'displayName']);
});
test('owner can rename without changing join date', async () => {
  await setDoc(ref(a, 'profiles/a'), profile('Alice'));
  const before = (await getDoc(ref(a, 'profiles/a'))).data().createdAt;
  await assertSucceeds(updateDoc(ref(a, 'profiles/a'), {displayName: 'Alicia'}));
  assert.ok(before.isEqual((await getDoc(ref(a, 'profiles/a'))).data().createdAt));
});
test('cross-user create, edit and deletion are denied', async () => {
  await assertFails(setDoc(ref(a, 'profiles/b'), profile('Fake Bob')));
  await setDoc(ref(b, 'profiles/b'), profile('Bob'));
  await assertFails(updateDoc(ref(a, 'profiles/b'), {displayName: 'Fake'}));
  await assertFails(deleteDoc(ref(a, 'profiles/b')));
  await assertFails(deleteDoc(ref(b, 'profiles/b')));
});
test('profile fields, name constraints and forged join date enforced', async () => {
  for (const bad of [profile(''), profile('   '), profile('x'.repeat(41)), {...profile('Alice'), email: 'private@example.invalid'}, {...profile('Alice'), role: 'moderator'}, {...profile('Alice'), displayName: 123}, {...profile('Alice'), createdAt: Timestamp.fromMillis(1)}]) await assertFails(setDoc(ref(a, 'profiles/a'), bad));
  await setDoc(ref(a, 'profiles/a'), profile('Alice'));
  await assertFails(updateDoc(ref(a, 'profiles/a'), {createdAt: Timestamp.fromMillis(2)}));
  await assertFails(updateDoc(ref(a, 'profiles/a'), {role: 'moderator'}));
  await assertFails(getDocs(query(collection(a, 'profiles'), limit(50))));
});
test('anonymous, uninvited and suspended profile access denied', async () => {
  await setDoc(ref(a, 'profiles/a'), profile('Alice'));
  for (const db of [anon, outsider]) {
    await assertFails(getDoc(ref(db, 'profiles/a')));
    await assertFails(setDoc(ref(db, 'profiles/outsider'), profile('No')));
  }
  await suspendA();
  await assertFails(getDoc(ref(a, 'profiles/a')));
  await assertFails(updateDoc(ref(a, 'profiles/a'), {displayName: 'No'}));
  await assertSucceeds(getDoc(ref(mod, 'profiles/a')));
});
test('member submits report; only moderator can read it', async () => {
  await assertSucceeds(setDoc(ref(a, 'reports/r1'), report()));
  for (const db of [a, b, anon, outsider]) {
    await assertFails(getDoc(ref(db, 'reports/r1')));
    await assertFails(getDocs(query(collection(db, 'reports'), limit(50))));
  }
  const result = await assertSucceeds(getDoc(ref(mod, 'reports/r1')));
  assert.equal(result.data().reporterId, 'a');
  await assertSucceeds(getDocs(query(collection(mod, 'reports'), limit(50))));
  await assertFails(getDocs(collection(mod, 'reports')));
  await assertFails(getDocs(query(collection(mod, 'reports'), limit(51))));
});
test('invalid or impersonated reports and overwrites denied', async () => {
  for (const bad of [report('b'), report('a', 'missing'), {...report(), reason: 'invalid-category'}, {...report(), createdAt: Timestamp.fromMillis(1)}, {...report(), role: 'moderator'}, {...report(), messageId: '../seed'}]) await assertFails(setDoc(ref(a, 'reports/r1'), bad));
  await setDoc(ref(a, 'reports/r1'), report());
  await assertFails(setDoc(ref(a, 'reports/r1'), report()));
  await assertFails(updateDoc(ref(mod, 'reports/r1'), {reason: 'other'}));
  await assertFails(deleteDoc(ref(mod, 'reports/r1')));
});
test('anonymous, uninvited and suspended cannot report', async () => {
  await assertFails(setDoc(ref(anon, 'reports/r1'), report()));
  await assertFails(setDoc(ref(outsider, 'reports/r1'), report('outsider')));
  await suspendA();
  await assertFails(setDoc(ref(a, 'reports/r1'), report()));
});
test('room reads fail closed for nonmembers and suspension', async () => {
  for (const db of [anon, outsider]) {
    await assertFails(getDoc(ref(db, 'rooms/main/messages/seed')));
    await assertFails(getDocs(query(collection(db, 'rooms/main/messages'), limit(50))));
  }
  await suspendA();
  await assertFails(getDoc(ref(a, 'rooms/main/messages/seed')));
  await assertFails(getDocs(query(collection(a, 'rooms/main/messages'), limit(50))));
  await assertSucceeds(getDoc(ref(b, 'rooms/main/messages/seed')));
});
test('moderator privilege is UID-based, not claims or editable data', async () => {
  const fake = env.authenticatedContext('fake-mod', {role: 'moderator', admin: true}).firestore();
  await assertFails(getDoc(ref(fake, 'reports/anything')));
  await assertFails(deleteDoc(ref(fake, 'rooms/main/messages/seed')));
  await assertFails(updateDoc(ref(a, 'members/a'), {active: true, role: 'moderator'}));
  await assertFails(setDoc(ref(outsider, 'members/outsider'), {active: true}));
  await assertSucceeds(getDoc(ref(mod, 'rooms/main/messages/seed')));
  await assertSucceeds(deleteDoc(ref(mod, 'rooms/main/messages/seed')));
  await assertFails(setDoc(ref(mod, 'members/a'), {active: true}));
  await assertFails(setDoc(ref(mod, 'profiles/a'), profile('Moderator edit')));
  await assertFails(deleteDoc(ref(mod, 'activity/a')));
});
