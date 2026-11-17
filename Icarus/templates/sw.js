/*
 * This site has no service worker.
 *
 * Some browsers still have one saved for this address, left by whatever was
 * served here before, and keep asking for /sw.js to update it (those
 * requests showed up as 404s in the server log).  Answering with this
 * script replaces the old worker with one that deletes anything the old one
 * had saved and then removes itself, so those browsers load the site
 * normally and stop asking.
 */
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.map(function (key) { return caches.delete(key); }));
      })
      .then(function () {
        return self.registration.unregister();
      })
  );
});
