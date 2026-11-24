(() => {
    const worker = document.currentScript.dataset.worker;
    const panel = document.getElementById("app-settings");
    const registration = "serviceWorker" in navigator
        ? navigator.serviceWorker.register(worker).then(() => navigator.serviceWorker.ready).catch(() => null)
        : Promise.resolve(null);
    if (!panel) return;

    const install = document.getElementById("install-app");
    const help = document.getElementById("install-help");
    const status = document.getElementById("push-status");
    const toggle = document.getElementById("toggle-push");
    const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    let prompt;
    function showInstalled() {
        help.textContent = "BigPicks is installed on this device.";
        install.classList.add("hidden");
    }
    if (standalone) showInstalled();
    else if (ios) help.textContent = "Tap Share, then Add to Home Screen. Open BigPicks from that icon to enable alerts.";

    window.addEventListener("beforeinstallprompt", event => {
        event.preventDefault();
        prompt = event;
        install.classList.remove("hidden");
        help.textContent = "Open BigPicks from your home screen.";
    });
    install.addEventListener("click", async () => {
        if (!prompt) return;
        const event = prompt;
        prompt = null;
        install.classList.add("hidden");
        await event.prompt();
    });
    window.addEventListener("appinstalled", showInstalled);

    if (!panel.dataset.key || !("PushManager" in window) || !("Notification" in window)) {
        status.textContent = !panel.dataset.key ? "Weekly alerts are currently unavailable." :
            ios && !standalone ? "Weekly alerts are available in the home screen app." :
            "This browser doesn’t support push notifications.";
        return;
    }

    async function update(action, subscription) {
        const response = await fetch(panel.dataset.subscriptionUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": document.querySelector('[name="csrfmiddlewaretoken"]').value
            },
            body: JSON.stringify({ action, ...subscription.toJSON() })
        });
        if (!response.ok || response.redirected) throw new Error("Subscription update failed");
        return (await response.json()).enabled;
    }

    let reg, subscription, enabled = false;
    function render() {
        status.textContent = enabled ? "Alerts are on for this device." : "Alerts are off for this device.";
        toggle.textContent = enabled ? "Turn off alerts" : "Turn on alerts";
        toggle.classList.remove("hidden");
        toggle.disabled = !enabled && Notification.permission === "denied";
        if (toggle.disabled) status.textContent = "Allow notifications for BigPicks in your device or browser settings.";
    }
    registration.then(async result => {
        reg = result;
        if (!reg) throw new Error("Service worker unavailable");
        subscription = await reg.pushManager.getSubscription();
        enabled = subscription ? await update("status", subscription) : false;
        render();
    }).catch(() => { status.textContent = "Couldn’t load alert settings. Reload to try again."; });

    toggle.addEventListener("click", async () => {
        toggle.disabled = true;
        try {
            if (enabled) {
                await update("disable", subscription);
                enabled = false;
                await subscription.unsubscribe();
                subscription = null;
            } else if (await Notification.requestPermission() === "granted") {
                // A browser may still have a subscription from an earlier account or session.
                if (subscription) await subscription.unsubscribe();
                const base64 = panel.dataset.key.replace(/-/g, "+").replace(/_/g, "/");
                const key = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
                subscription = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
                enabled = await update("enable", subscription);
            }
            render();
        } catch {
            render();
            status.textContent = "Couldn’t update alerts. Check your connection and try again.";
        }
    });
})();
