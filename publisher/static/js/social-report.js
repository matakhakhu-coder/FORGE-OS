/* ZA Divergent social: report another member's message. */
import { collection, addDoc, serverTimestamp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

var REASONS = [
  ['doxxing', 'Doxxing'],
  ['threat', 'Threat'],
  ['unsupported-accusation', 'Unsupported accusation'],
  ['harassment', 'Harassment'],
  ['spam', 'Spam'],
  ['other', 'Other']
];

export function reportControl(db, messageId, uid) {
  var wrap = document.createElement('div');
  wrap.style.cssText = 'margin-top:4px;font-size:0.65rem;';
  var select = document.createElement('select');
  select.style.cssText = 'font-size:0.7rem;margin-right:4px;';
  REASONS.forEach(function (r) {
    var o = document.createElement('option');
    o.value = r[0]; o.textContent = r[1];
    select.appendChild(o);
  });
  var btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = 'Report';
  btn.style.cssText = 'font-size:0.65rem;';
  var note = document.createElement('span');
  note.style.marginLeft = '6px';
  btn.addEventListener('click', async function () {
    btn.disabled = true; note.textContent = 'Sending...';
    try {
      await addDoc(collection(db, 'reports'), {
        reporterId: uid,
        messageId: messageId,
        reason: select.value,
        createdAt: serverTimestamp()
      });
      note.textContent = 'Reported.';
    } catch (e) {
      btn.disabled = false;
      note.textContent = 'Report failed (' + (e.code || e) + ').';
    }
  });
  wrap.appendChild(select); wrap.appendChild(btn); wrap.appendChild(note);
  return wrap;
}