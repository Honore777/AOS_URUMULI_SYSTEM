async function registerServiceWorkers() {
  if (!('serviceWorker' in navigator)) return;

  // Base service worker (installability)
  try {
    await navigator.serviceWorker.register('/static/service-worker.js');
  } catch (e) {
    console.warn('service-worker registration failed', e);
  }

  // Firebase messaging SW (push notifications)
  try {
    await navigator.serviceWorker.register('/static/firebase-messaging-sw.js');
  } catch (e) {
    console.warn('firebase-messaging-sw registration failed', e);
  }
}

async function initFirebaseMessaging() {
  // Load Firebase SDK (v9 compat)
  if (!window.firebase) {
    console.warn('Firebase SDK not loaded');
    return;
  }

  // TODO: Replace placeholders with your Firebase web app config
  const firebaseConfig = {
    apiKey: "__FIREBASE_API_KEY__",
    authDomain: "__FIREBASE_AUTH_DOMAIN__",
    projectId: "__FIREBASE_PROJECT_ID__",
    storageBucket: "__FIREBASE_STORAGE_BUCKET__",
    messagingSenderId: "__FIREBASE_SENDER_ID__",
    appId: "__FIREBASE_APP_ID__"
  };

  // Avoid double-init
  try {
    if (firebase.apps && firebase.apps.length === 0) {
      firebase.initializeApp(firebaseConfig);
    }
  } catch (e) {
    // ignore
  }

  let messaging;
  try {
    messaging = firebase.messaging();
  } catch (e) {
    console.warn('Firebase messaging init failed', e);
    return;
  }

  // Ask permission
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') {
    console.warn('Notification permission not granted');
    return;
  }

  // TODO: Replace with your Web Push certificate key (VAPID key) from Firebase console
  const vapidKey = "__FIREBASE_VAPID_KEY__";

  let token;
  try {
    token = await messaging.getToken({ vapidKey });
  } catch (e) {
    console.warn('Failed to get FCM token', e);
    return;
  }

  if (!token) return;

  // Register token with backend
  try {
    const res = await fetch('/api/push/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, user_agent: navigator.userAgent })
    });
    if (!res.ok) {
      console.warn('Token register failed', res.status);
    }
  } catch (e) {
    console.warn('Token register request failed', e);
  }

  // Foreground messages
  try {
    messaging.onMessage((payload) => {
      const title = (payload && payload.notification && payload.notification.title) || 'Urumuli Smart System';
      const body = (payload && payload.notification && payload.notification.body) || 'New notification';
      new Notification(title, { body });
    });
  } catch (e) {
    // ignore
  }
}

(async function () {
  try {
    await registerServiceWorkers();
    await initFirebaseMessaging();
  } catch (e) {
    console.warn('push init failed', e);
  }
})();
