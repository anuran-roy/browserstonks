// BrowserStonks popup: fill button + CLI connection settings.
const $ = (id) => document.getElementById(id);

async function getConfig() {
  const { config } = await chrome.storage.local.get("config");
  return config || { wsUrl: "ws://127.0.0.1:8765" };
}

async function refreshStatus() {
  const status = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
  $("status").textContent = status?.connected
    ? "CLI connected"
    : "CLI not connected (will retry on fill)";
}

document.addEventListener("DOMContentLoaded", async () => {
  const cfg = await getConfig();
  $("wsUrl").value = cfg.wsUrl;

  $("wsUrl").addEventListener("change", async () => {
    await chrome.storage.local.set({
      config: { ...cfg, wsUrl: $("wsUrl").value },
    });
    $("status").textContent = "saved; reconnecting…";
    refreshStatus();
  });

  $("chat").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await chrome.sidePanel.open({ tabId: tab.id });
    window.close();
  });

  $("fill").addEventListener("click", async () => {
    $("fill").disabled = true;
    $("status").textContent = "sending DOM state to CLI…";
    const res = await chrome.runtime.sendMessage({ type: "FILL_PAGE" });
    $("status").textContent = res?.error
      ? "error: " + res.error
      : (res?.note || "done");
    $("fill").disabled = false;
  });

  refreshStatus();
});
