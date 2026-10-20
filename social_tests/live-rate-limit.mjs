import {initializeApp} from 'firebase/app';
import {getAuth, signInWithEmailAndPassword} from 'firebase/auth';
import {getFirestore, doc, getDoc, deleteDoc, writeBatch, serverTimestamp,
  collection, query, limit, getDocs} from 'firebase/firestore';

const PROJECT = 'za-divergent-chat-test';
const UID_A = 'yisL7fnkbXT4JkFRG0W4yscbUEG2';
const UID_B = 'wtsUDf6V6XYjRlGxVFcmNEPmGuE2';
const need = n => {
  const v = process.env[n];
  if (!v) throw new Error('Missing environment variable ' + n);
  return v;
};
if (process.env.FIRESTORE_EMULATOR_HOST) throw new Error('Emulator variable is set; this script targets the real project');
const apiKey = need('ZA_API_KEY');
const authDomain = need('ZA_AUTH_DOMAIN');
if (!authDomain.startsWith(PROJECT)) throw new Error('ZA_AUTH_DOMAIN does not match ' + PROJECT);

const prefix = 'live-' + Date.now().toString(36) + '-';
const mp = id => 'rooms/main/messages/' + id;
let failures = 0;
let a1, a2, b;
const check = (ok, label, detail) => {
  if (!ok) failures++;
  console.log((ok ? 'PASS ' : 'FAIL ') + label + (detail ? ' (' + detail + ')' : ''));
};

async function client(name, email, passVar, expectUid) {
  const app = initializeApp({apiKey, authDomain, projectId: PROJECT}, name);
  const cred = await signInWithEmailAndPassword(getAuth(app), email, need(passVar));
  if (cred.user.uid !== expectUid) throw new Error(name + ' signed in with an unexpected UID');
  return {db: getFirestore(app), uid: cred.user.uid, name};
}
function send(c, id) {
  const batch = writeBatch(c.db);
  batch.set(doc(c.db, mp(id)), {authorId: c.uid, body: 'live test', createdAt: serverTimestamp()});
  batch.set(doc(c.db, 'activity/' + c.uid), {lastAt: serverTimestamp(), lastMessageId: id});
  return batch.commit();
}
async function listAll(c) {
  return (await getDocs(query(collection(c.db, 'rooms/main/messages'), limit(50)))).docs;
}
const ours = async c => (await listAll(c)).filter(d => d.id.startsWith(prefix));

async function cleanup() {
  for (const c of [a1, b]) {
    if (!c) continue;
    try {
      for (const d of await ours(c)) {
        if (d.data().authorId === c.uid) await deleteDoc(d.ref);
      }
    } catch (e) {
      console.log('Cleanup error for ' + c.name + ': ' + (e.code || e.message));
    }
  }
  try {
    const left = (await ours(a1)).map(d => d.id);
    console.log(left.length ? 'LEFTOVER test messages to delete in console: ' + left.join(', ') : 'Cleanup: no test messages left');
  } catch (e) {
    console.log('Could not verify cleanup: ' + (e.code || e.message));
  }
  console.log('Now delete these two documents in the console: activity/' + UID_A + ' and activity/' + UID_B);
}

async function main() {
  try {
    a1 = await client('a1', 'za-test-a@example.com', 'ZA_PASS_A', UID_A);
    a2 = await client('a2', 'za-test-a@example.com', 'ZA_PASS_A', UID_A);
    b = await client('b', 'za-test-b@example.com', 'ZA_PASS_B', UID_B);

    if ((await listAll(a1)).length > 0) throw new Error('Messages collection is not empty; clear it in the console first');
    for (const c of [a1, b]) {
      if ((await getDoc(doc(c.db, 'activity/' + c.uid))).exists()) {
        throw new Error('activity/' + c.uid + ' exists; delete it in the console first');
      }
    }

    const ids = Array.from({length: 20}, (_, i) => prefix + 'a' + i);
    const results = await Promise.allSettled(ids.map((id, i) => send(i % 2 ? a2 : a1, id)));
    const committed = results.filter(r => r.status === 'fulfilled').length;
    const codes = results.filter(r => r.status === 'rejected').map(r => r.reason.code);
    check(committed === 1, 'Test 1: exactly one of 20 concurrent sends committed', committed + ' committed');
    check(codes.every(c => c === 'permission-denied'), 'Test 1: every rejection was permission-denied', JSON.stringify([...new Set(codes)]));
    const msgs = await ours(a1);
    check(msgs.length === 1, 'Test 1: one committed message, no orphans', msgs.length + ' found');
    const act = await getDoc(doc(a1.db, 'activity/' + a1.uid));
    check(act.exists() && msgs.length === 1 && act.data().lastMessageId === msgs[0].id, 'Test 1: activity points at the committed message');

    let first = 'committed';
    await send(b, prefix + 'b1').catch(e => { first = e.code; });
    check(first === 'committed', 'Test 2: user B first send committed', first);
    let second = 'committed';
    await send(b, prefix + 'b2').catch(e => { second = e.code; });
    check(second === 'permission-denied', 'Test 2: B immediate repeat rejected', second);
    const bm = (await ours(b)).filter(d => d.data().authorId === b.uid);
    check(bm.length === 1, 'Test 2: B has exactly one committed message', bm.length + ' found');
  } finally {
    await cleanup();
  }
}
main().then(
  () => process.exit(failures ? 1 : 0),
  e => { console.error('ABORTED: ' + (e.code || '') + ' ' + e.message); process.exit(1); }
);