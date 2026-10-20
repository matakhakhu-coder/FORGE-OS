/* ZA Divergent social: moderator report list. Rules enforce access; this only hides the UI. */
import { collection, doc, getDoc, query, orderBy, limit, getDocs } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

export function renderModerator(container, user) {
  var MOD_UID = 'WwxG72k6PxPRVImyFzXNLkBP2Iz1';
  if (user.uid !== MOD_UID) return;
  var db = window.zaSocial.db;
  var box = document.createElement('div');
  box.style.cssText = 'margin:8px 12px;font-size:0.8rem;border-top:1px solid var(--border-dim);padding-top:8px;';
  var title = document.createElement('div');
  title.textContent = 'Moderator: reports';
  var btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = 'Load reports'; btn.className = 'md-pill';
  btn.style.margin = '6px 0';
  var status = document.createElement('div');
  status.style.cssText = 'font-size:0.75rem;';
  var list = document.createElement('div');
  box.appendChild(title); box.appendChild(btn); box.appendChild(status); box.appendChild(list);
  container.appendChild(box);

  btn.addEventListener('click', async function () {
    status.textContent = 'Loading...'; list.textContent = '';
    try {
      var snap = await getDocs(query(collection(db, 'reports'), orderBy('createdAt', 'desc'), limit(50)));
      if (snap.empty) { status.textContent = 'No reports.'; return; }
      status.textContent = snap.size + ' report(s).';
      for (var i = 0; i < snap.docs.length; i++) {
        var r = snap.docs[i].data();
        var row = document.createElement('div');
        row.style.cssText = 'margin:6px 0;padding:6px 8px;border:1px solid var(--border-dim);border-radius:4px;font-size:0.75rem;';
        var head = document.createElement('div');
        var when = r.createdAt && r.createdAt.toDate ? r.createdAt.toDate().toLocaleString() : '';
        head.textContent = r.reason + ' | reporter ' + String(r.reporterId).slice(-4) + ' | ' + when;
        var msg = document.createElement('div');
        msg.style.marginTop = '4px';
        try {
          var m = await getDoc(doc(db, 'rooms', 'main', 'messages', r.messageId));
          msg.textContent = m.exists() ? 'Message: ' + m.data().body : 'Message no longer exists.';
        } catch (e) { msg.textContent = 'Could not load message (' + (e.code || e) + ').'; }
        row.appendChild(head); row.appendChild(msg);
        list.appendChild(row);
      }
    } catch (e) { status.textContent = 'Could not load reports (' + (e.code || e) + ').'; }
  });
}