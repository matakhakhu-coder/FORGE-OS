/* ZA Divergent social: sign-in and membership state. Live host only. */
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';
import { getFirestore, doc, getDoc } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';
import { firebaseConfig, socialEnabled } from './social-config.js';

if (socialEnabled) {
  const app = initializeApp(firebaseConfig);
  const auth = getAuth(app);
  const db = getFirestore(app);
  const listeners = [];
  let state = { status: 'signed-out', user: null };

  function setState(next) {
    state = next;
    listeners.forEach(function (fn) { fn(state); });
  }

  async function resolveUser(user) {
    if (!user) { setState({ status: 'signed-out', user: null }); return; }
    setState({ status: 'checking', user: user });
    try {
      const snap = await getDoc(doc(db, 'members', user.uid));
      if (!snap.exists()) setState({ status: 'uninvited', user: user });
      else if (snap.data().active === true) setState({ status: 'member', user: user });
      else setState({ status: 'suspended', user: user });
    } catch (e) {
      setState({ status: 'error', user: user, error: e.code || String(e) });
    }
  }

  onAuthStateChanged(auth, resolveUser);

  window.zaSocial = {
    db: db,
    auth: auth,
    getState: function () { return state; },
    onState: function (fn) { listeners.push(fn); fn(state); },
    signIn: function () { return signInWithPopup(auth, new GoogleAuthProvider()); },
    signOut: function () { return signOut(auth); }
  };
}