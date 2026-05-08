/*
  Firebase Messaging service worker.
  
  NOTE: You must fill in your Firebase web app config in static/js/push.js.
  This SW is required for background push notifications.
*/

// Firebase v9 compat (loaded via importScripts)
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js');

// Placeholders - you will replace these values.
firebase.initializeApp({
  apiKey: "__FIREBASE_API_KEY__",
  authDomain: "__FIREBASE_AUTH_DOMAIN__",
  projectId: "__FIREBASE_PROJECT_ID__",
  storageBucket: "__FIREBASE_STORAGE_BUCKET__",
  messagingSenderId: "__FIREBASE_SENDER_ID__",
  appId: "__FIREBASE_APP_ID__"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  const title = (payload && payload.notification && payload.notification.title) || 'Urumuli Smart System';
  const body = (payload && payload.notification && payload.notification.body) || 'New notification';
  self.registration.showNotification(title, {
    body,
    icon: '/static/logo.png'
  });
});
