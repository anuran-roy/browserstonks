// BrowserStonks content script.
// - Collects the current DOM state (forms, fields, labels, selectors) and
//   answers GET_DOM_STATE.
// - On RUN_PROGRAM: loads Pyodide (Python via WebAssembly) once, then runs
//   the CLI-generated program with a `page` helper bound to the live DOM.

(() => {
  if (window.__browserstonksLoaded) return;
  window.__browserstonksLoaded = true;

  function log(...args) {
    console.log("[browserstonks]", ...args);
  }

  // ------------------------------------------------------------ DOM state
  function cssSelector(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const name = el.tagName.toLowerCase();
    if (el.name) return `${name}[name="${el.name}"]`;
    const path = [];
    let node = el;
    while (node && node.nodeType === 1 && path.length < 5) {
      let seg = node.tagName.toLowerCase();
      if (node.id) { path.unshift(`#${CSS.escape(node.id)}`); break; }
      const parent = node.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(
          (c) => c.tagName === node.tagName);
        if (sibs.length > 1) seg += `:nth-of-type(${sibs.indexOf(node) + 1})`;
      }
      path.unshift(seg);
      node = node.parentElement;
    }
    return path.join(" > ");
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function labelFor(input) {
    // <label for=...>, aria-label, placeholder, or closest .question-style text
    if (input.getAttribute("aria-label")) return input.getAttribute("aria-label");
    if (input.labels?.length) return input.labels[0].innerText.trim();
    if (input.name) {
      const lbl = document.querySelector(`label[for="${input.name}"]`);
      if (lbl) return lbl.innerText.trim();
    }
    if (input.placeholder) return input.placeholder;
    const holder = input.closest(
      '[role="listitem"], .question, .field, li, div');
    if (holder) {
      const text = Array.from(holder.querySelectorAll("label, span, div"))
        .map((n) => n.innerText?.trim())
        .filter((t) => t && t.length < 200);
      if (text.length) return text[0];
    }
    return "";
  }

  function snapshotForms() {
    const forms = [];
    // real <form>s
    for (const form of document.querySelectorAll("form")) {
      forms.push(describeFields(
        Array.from(form.querySelectorAll("input, select, textarea")), form));
    }
    // form builders often skip <form> (Google Forms, Typeform): grab visible
    // inputs/roles not already inside a <form>
    const orphans = Array.from(
      document.querySelectorAll('input, select, textarea, [role="radio"], [role="checkbox"]')
    ).filter((el) => visible(el) && !el.closest("form"));
    if (orphans.length) {
      const groups = new Map();
      for (const el of orphans) {
        const holder = el.closest('[role="listitem"], .question, .field') || document.body;
        if (!groups.has(holder)) groups.set(holder, []);
        groups.get(holder).push(el);
      }
      for (const [holder, els] of groups) {
        forms.push(describeFields(els, holder));
      }
    }
    return forms.filter((f) => f.fields.length);
  }

  function describeFields(els, holder) {
    const fields = [];
    for (const el of els) {
      if (!visible(el)) continue;
      const type = el.getAttribute("role")?.includes("radio") ? "radio"
        : el.getAttribute("role")?.includes("checkbox") ? "checkbox"
        : el.tagName === "SELECT" ? "select"
        : el.tagName === "TEXTAREA" ? "textarea"
        : (el.type || "text");
      fields.push({
        id: el.id || el.name || undefined,
        name: el.name || undefined,
        label: labelFor(el),
        type,
        role: el.getAttribute("role") || undefined,
        required: el.required || el.getAttribute("aria-required") === "true",
        // current value, so the CLI can leave pre-filled fields alone
        value: (el.value || el.innerText || "").trim().slice(0, 200) || undefined,
        checked: el.checked || undefined,
        options: el.tagName === "SELECT"
          ? Array.from(el.options).map((o) => o.text)
          : Array.from(el.closest('[role="listitem"], .question, .field')?.querySelectorAll('[role="radio"], [role="checkbox"], li') || [])
              .map((o) => o.getAttribute("aria-label") || o.innerText?.trim())
              .filter(Boolean),
        selector: cssSelector(el),
      });
    }
    return {
      id: holder.id || undefined,
      fields,
    };
  }

  function domState() {
    return {
      url: location.href,
      title: document.title,
      site: detectSite(),
      forms: snapshotForms(),
    };
  }

  function detectSite() {
    const h = location.hostname;
    if (h.endsWith("docs.google.com")) return "google_forms";
    if (h.includes("typeform")) return "typeform";
    return "generic";
  }

  // `page` helper ops — executed in this tab on request of the offscreen
  // document (which runs the Python program via Pyodide).
  function pageOp(op, args) {
    const dispatch = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true }));
    const q = (selector) => {
      const el = document.querySelector(selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      return el;
    };

    if (op === "set") {
      const [selector, value, force] = args;
      const el = q(selector);
      // Never overwrite text the user (or a previous run) already entered,
      // unless the program explicitly passes force=true.
      const existing = (el.value ?? el.innerText ?? "").toString().trim();
      if (existing && !force) {
        log(`skip ${selector}: already filled (${existing.slice(0, 40)}…)`);
        return false;
      }
      el.focus?.();
      el.value = value;
      dispatch(el, "input");
      dispatch(el, "change");
      return true;
    }
    if (op === "click") {
      const [selector] = args;
      q(selector).click();
      return true;
    }
    if (op === "select") {
      const [selector, value] = args;
      const el = q(selector);
      if (el.tagName !== "SELECT") throw new Error(`not a select: ${selector}`);
      const opt = Array.from(el.options).find(
        (o) => o.text.trim() === value || o.value === value);
      if (!opt) throw new Error(`option not found: ${value}`);
      el.value = opt.value;
      dispatch(el, "change");
      return true;
    }
    if (op === "submit") {
      const btn = document.querySelector(
        '[role="button"][aria-label*="Submit"], button[type="submit"], input[type="submit"]');
      if (btn) btn.click();
      return !!btn;
    }
    throw new Error(`unknown page op: ${op}`);
  }

  // ------------------------------------------------------------ messaging
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "GET_DOM_STATE") {
      sendResponse(domState());
      return;
    }
    if (msg.type === "PAGE_CALL") {
      try {
        const result = pageOp(msg.op, msg.args || []);
        log(`page.${msg.op}(${(msg.args || []).slice(0, 2).join(", ")}) ->`, result);
        sendResponse({ result });
      } catch (e) {
        log(`page.${msg.op} FAILED:`, e.message);
        sendResponse({ error: String(e.message || e) });
      }
      return;
    }
    if (msg.type === "STATUS_LOG") {
      log(msg.message);
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "GET_TAB_HTML") {
      // full page HTML for harness read/process actions (truncated server-side)
      try {
        sendResponse({ ok: true, html: document.documentElement.outerHTML });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
      return;
    }
    // ack probes (PING_CONTENT) and anything unknown so the caller never
    // sees "receiving end does not exist"
    sendResponse({ ok: true, pong: msg.type === "PING_CONTENT" });
  });


  // ------------------------------------------------- floating panel button
  // A draggable button hugging the right edge of every page; click opens
  // the BrowserStonks sidepanel. Position persists per-origin.
  function mountPanelButton() {
    if (document.getElementById("bs-panel-btn-host")) return;
    const host = document.createElement("div");
    host.id = "bs-panel-btn-host";
    const shadow = host.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      .btn {
        position: fixed; right: 0; top: 40%;
        width: 38px; height: 38px;
        background: linear-gradient(180deg, #4f46e5, #6366f1);
        color: #fff; border: 0; border-radius: 12px 0 0 12px;
        cursor: grab; user-select: none; z-index: 2147483647;
        box-shadow: -2px 2px 10px rgba(0,0,0,.25);
        display: flex; align-items: center; justify-content: center;
        padding: 5px; box-sizing: border-box;
        transition: filter .15s;
      }
      .btn:hover { filter: brightness(1.15); }
      .btn.dragging { cursor: grabbing; filter: brightness(1.1); }
    `;
    shadow.appendChild(style);

    const btn = document.createElement("button");
    btn.className = "btn";
    btn.title = "Click to open the BrowserStonks chat panel — drag to reposition";
    const logo = document.createElement("img");
    // web_accessible_resource — pages can load it from the extension
    logo.src = chrome.runtime.getURL("public/browserstonks-logo.png");
    logo.alt = "BrowserStonks";
    logo.style.cssText = "width:100%;height:100%;object-fit:contain;pointer-events:none;";
    btn.appendChild(logo);
    shadow.appendChild(btn);

    // restore saved vertical position for this origin
    const key = "bs-btn-top:" + location.origin;
    try {
      const saved = parseFloat(sessionStorage.getItem(key) || localStorage.getItem(key) || "");
      if (!isNaN(saved)) btn.style.top = saved + "px";
    } catch { /* storage may be blocked; default position is fine */ }

    function clamp() {
      const max = window.innerHeight - btn.offsetHeight - 8;
      const cur = parseFloat(btn.style.top) || (window.innerHeight * 0.4);
      btn.style.top = Math.max(8, Math.min(cur, max)) + "px";
    }
    clamp();
    window.addEventListener("resize", clamp);

    // drag along the right edge; a tiny move (< 5px) counts as a click
    let dragging = false, moved = false, startY = 0, startTop = 0;
    btn.addEventListener("pointerdown", (e) => {
      dragging = true; moved = false;
      startY = e.clientY;
      startTop = parseFloat(btn.style.top) || (window.innerHeight * 0.4);
      btn.setPointerCapture(e.pointerId);
      btn.classList.add("dragging");
      e.preventDefault();
    });
    btn.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dy = e.clientY - startY;
      if (Math.abs(dy) > 5) moved = true;
      const max = window.innerHeight - btn.offsetHeight - 8;
      btn.style.top = Math.max(8, Math.min(startTop + dy, max)) + "px";
    });
    btn.addEventListener("pointerup", (e) => {
      if (!dragging) return;
      dragging = false;
      btn.classList.remove("dragging");
      if (moved) {
        // persist position
        try {
          localStorage.setItem(key, btn.style.top);
          sessionStorage.setItem(key, btn.style.top);
        } catch { /* ignore */ }
      } else {
        // click: open the sidepanel via the background worker
        chrome.runtime.sendMessage({ type: "OPEN_CHAT_PANEL" })
          .catch(() => log("panel open failed (extension context?)"));
      }
    });

    document.documentElement.appendChild(host);
    log("panel button mounted (right edge)");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountPanelButton);
  } else {
    mountPanelButton();
  }

  log("content script ready");
})();
