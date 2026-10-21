/* ZA Divergent social: sign-in states and member chat. Live host only. */
import { socialEnabled } from './social-config.js';
import './social.js';
import { renderProfile } from './social-profile.js';
import { reportControl } from './social-report.js';
import { renderModerator } from './social-moderator.js';
import { collection, doc, query, orderBy, limit, getDocs, writeBatch, deleteDoc, serverTimestamp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

if (socialEnabled) {
  var pill = document.getElementById('social-pill');
  var body = document.getElementById('social-body');
  if (pill && body) {
    pill.style.display = '';

    function line(text) {
      var p = document.createElement('p');
      p.style.cssText = 'margin:8px 12px;font-size:0.8rem;';
      p.textContent = text;
      return p;
    }
    function button(label, fn) {
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = label; b.className = 'md-pill';
      b.style.margin = '8px 12px'; b.addEventListener('click', fn);
      return b;
    }

    async function loadMessages(list, status, uid) {
      var db = window.zaSocial.db;
      try {
        var q = query(collection(db, 'rooms', 'main', 'messages'), orderBy('createdAt', 'desc'), limit(50));
        var snap = await getDocs(q);
        list.textContent = '';
        if (snap.empty) { list.appendChild(line('No messages yet.')); return; }
        snap.forEach(function (d) {
          var m = d.data();
          var row = document.createElement('div');
          row.style.cssText = 'margin:6px 12px;padding:6px 8px;border:1px solid var(--border-dim);border-radius:4px;font-size:0.8rem;';
          var who = document.createElement('div');
          who.style.cssText = 'font-size:0.65rem;opacity:.7;';
          var when = m.createdAt && m.createdAt.toDate ? m.createdAt.toDate().toLocaleString() : '';
          who.textContent = (m.authorId === uid ? 'You' : 'Member ' + String(m.authorId).slice(-4)) + ' ' + when;
          var text = document.createElement('div');
          text.textContent = m.body;
          row.appendChild(who); row.appendChild(text);
          if (m.authorId === uid) {
            var del = document.createElement('button');
            del.type = 'button'; del.textContent = 'Delete'; del.style.cssText = 'margin-top:4px;font-size:0.65rem;';
            del.addEventListener('click', async function () {
              try { await deleteDoc(doc(db, 'rooms', 'main', 'messages', d.id)); await loadMessages(list, status, uid); }
              catch (e) { status.textContent = 'Delete failed (' + (e.code || e) + ').'; }
            });
            row.appendChild(del);
          }
          if (m.authorId !== uid) { row.appendChild(reportControl(db, d.id, uid)); }
          list.appendChild(row);
        });
      } catch (e) { status.textContent = 'Could not load messages (' + (e.code || e) + ').'; }
    }

    async function sendMessage(input, list, status, uid) {
      var db = window.zaSocial.db;
      var text = input.value.trim();
      if (!text) return;
      if (text.length > 500) { status.textContent = 'Messages are limited to 500 characters.'; return; }
      status.textContent = 'Sending...';
      try {
        var ref = doc(collection(db, 'rooms', 'main', 'messages'));
        var batch = writeBatch(db);
        batch.set(ref, { authorId: uid, body: text, createdAt: serverTimestamp() });
        batch.set(doc(db, 'activity', uid), { lastAt: serverTimestamp(), lastMessageId: ref.id });
        await batch.commit();
        input.value = ''; status.textContent = '';
        await loadMessages(list, status, uid);
      } catch (e) {
        status.textContent = e.code === 'permission-denied'
          ? 'Not sent. Wait 10 seconds between messages, or your access was refused.'
          : 'Send failed (' + (e.code || e) + ').';
      }
    }

    function renderChat(user) {
      var status = document.createElement('p');
      status.style.cssText = 'margin:8px 12px;font-size:0.75rem;';
      var list = document.createElement('div');
      var input = document.createElement('textarea');
      input.maxLength = 500; input.rows = 2; input.placeholder = 'Write a message';
      input.style.cssText = 'display:block;width:calc(100% - 24px);margin:8px 12px;box-sizing:border-box;font-size:16px;';
      renderProfile(body, user);
      body.appendChild(input);
      body.appendChild(button('Send', function () { sendMessage(input, list, status, user.uid); }));
      body.appendChild(button('Refresh', function () { loadMessages(list, status, user.uid); }));
      body.appendChild(status);
      body.appendChild(list);
      renderModerator(body, user);
      loadMessages(list, status, user.uid);
    }

    function render(state) {
      body.textContent = '';
      var s = state.status;
      if (s === 'signed-out') {
        body.appendChild(line('Social is invite-only. Sign in with Google to continue.'));
        body.appendChild(button('Sign in with Google', function () { window.zaSocial.signIn().catch(function (e) { body.appendChild(line('Sign-in failed: ' + (e.code || e))); }); }));
        return;
      }
      if (s === 'checking') { body.appendChild(line('Checking your access...')); return; }
      if (s === 'uninvited') body.appendChild(line('This account has not been invited yet.'));
      else if (s === 'suspended') body.appendChild(line('This account is suspended.'));
      else if (s === 'error') body.appendChild(line('Could not check access (' + state.error + ').'));
      body.appendChild(button('Sign out', function () { window.zaSocial.signOut(); }));
      if (s === 'member') renderChat(state.user);
    }
    window.zaSocial.onState(render);
  }
}