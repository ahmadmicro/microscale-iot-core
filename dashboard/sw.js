/* Minimal service worker: app-shell cache so the dashboard installs and opens offline.
 * Live data is never cached — it comes over MQTT; retained values give last-known on reconnect. */
var CACHE = "iot-core-v2";
var SHELL = ["./index.html", "./app.js", "./style.css", "./manifest.webmanifest", "./icon.svg", "./vendor/mqtt.min.js"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }));
});
self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: "window" }).then(function (list) {
    if (list.length) return list[0].focus();
    return clients.openWindow("./index.html");
  }));
});
self.addEventListener("fetch", function (e) {
  // network-first for the spec (so edits show up), cache-first for the shell
  var url = e.request.url;
  if (url.indexOf("dashboard.json") !== -1) {
    e.respondWith(fetch(e.request).catch(function () { return caches.match(e.request); }));
    return;
  }
  e.respondWith(caches.match(e.request).then(function (r) { return r || fetch(e.request); }));
});
