/* ZA Divergent live Firebase web config. Client config only, no secrets. */
export const LIVE_HOST = 'forgemap.co.za';

export const firebaseConfig = {
  apiKey: 'AIzaSyDzAxx6vjkt9ZHQyoUjeRgJByXmt3WoK2A',
  authDomain: 'za-divergent-live-dc022.firebaseapp.com',
  projectId: 'za-divergent-live-dc022'
};

/* Social features run only on the real host. Previews and localhost stay off. */
export const socialEnabled = window.location.hostname === LIVE_HOST;