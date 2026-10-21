/* ZA Divergent social: own display name (create and rename). */
import { doc, getDoc, setDoc, updateDoc, serverTimestamp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

export async function renderProfile(container, user) {
  var db = window.zaSocial.db;
  var ref = doc(db, 'profiles', user.uid);
  var exists = false;
  var box = document.createElement('div');
  box.style.cssText = 'margin:8px 12px;font-size:0.8rem;';
  var label = document.createElement('div');
  var input = document.createElement('input');
  input.type = 'text'; input.maxLength = 40; input.placeholder = 'Display name';
  input.style.cssText = 'display:block;width:100%;box-sizing:border-box;margin:6px 0;font-size:16px;';
  var save = document.createElement('button');
  save.type = 'button'; save.textContent = 'Save name'; save.className = 'md-pill';
  var status = document.createElement('div');
  status.style.cssText = 'margin-top:6px;font-size:0.75rem;';
  box.appendChild(label); box.appendChild(input); box.appendChild(save); box.appendChild(status);
  container.appendChild(box);

  try {
    var snap = await getDoc(ref);
    if (snap.exists()) { exists = true; input.value = snap.data().displayName; label.textContent = 'Your display name'; }
    else { label.textContent = 'Choose a display name'; }
  } catch (e) { status.textContent = 'Could not load profile (' + (e.code || e) + ').'; }

  save.addEventListener('click', async function () {
    var name = input.value.trim();
    if (!name || name.length > 40) { status.textContent = 'Use 1 to 40 characters.'; return; }
    status.textContent = 'Saving...';
    try {
      if (exists) { await updateDoc(ref, { displayName: name }); }
      else { await setDoc(ref, { displayName: name, createdAt: serverTimestamp() }); exists = true; label.textContent = 'Your display name'; }
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = 'Save failed (' + (e.code || e) + ').'; }
  });
}