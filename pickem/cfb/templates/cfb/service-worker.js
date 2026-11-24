{% load static %}
const offlinePage = `<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offline | BigPicks</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #191e24; color: #f4f5f8; font: 1rem/1.5 system-ui, sans-serif; }
  main { max-width: 25rem; padding: 2rem; } h1 { line-height: 1.1; } a { color: #aabaff; }
</style>
<main><h1>You’re offline</h1><p>Reconnect to load your league and submit picks.</p><a href="">Try again</a></main></html>`;

self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", event => {
    const { request } = event;
    if (request.method === "GET" && request.mode === "navigate" && new URL(request.url).origin === self.location.origin) {
        event.respondWith(fetch(request).catch(() => new Response(offlinePage, {
            status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }
        })));
    }
});

self.addEventListener("push", event => {
    const data = event.data ? event.data.json() : {};
    event.waitUntil(self.registration.showNotification(data.title || "BigPicks", {
        body: data.body || "Your league’s picks are ready.",
        icon: "{% static 'pwa/icon-192.png' %}",
        tag: data.tag,
        data: { url: data.url || "/picks/" }
    }));
});

self.addEventListener("notificationclick", event => {
    event.notification.close();
    const target = new URL(event.notification.data?.url || "/picks/", self.location.origin);
    if (target.origin !== self.location.origin || target.pathname !== "/picks/") return;
    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
        const open = windows.find(client => client.url === target.href);
        if (open) return open.focus();
        return self.clients.openWindow(target.href);
    })());
});
