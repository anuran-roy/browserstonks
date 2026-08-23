// BrowserStonks content script.
// - Collects the current DOM state (forms, fields, labels, selectors) and
//   answers GET_DOM_STATE.
// - On RUN_PROGRAM: loads Pyodide (Python via WebAssembly) once, then runs
//   the CLI-generated program with a `page` helper bound to the live DOM.

(() => {
  if (window.__browserstonksLoaded) return;
  window.__browserstonksLoaded = true;

  const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.2/full/pyodide.js";
  let pyodidePromise = null;

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

  // ------------------------------------------------------------ Pyodide
  function loadPyodideOnce() {
    if (pyodidePromise) return pyodidePromise;
    pyodidePromise = (async () => {
      if (!window.loadPyodide) {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = PYODIDE_URL;
          s.onload = resolve;
          s.onerror = () => reject(new Error("failed to load pyodide.js"));
          document.head.appendChild(s);
        });
      }
      log("booting pyodide (wasm)...");
      return await window.loadPyodide({ indexURL: new URL(PYODIDE_URL, location.href).href.replace(/pyodide\.js$/, "") });
    })();
    return pyodidePromise;
  }

  // `page` helper object handed to the generated program's fill(page, VALUES)
  function makePageHelper() {
    const dispatch = (selector, type) => {
      const el = document.querySelector(selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      el.dispatchEvent(new Event(type, { bubbles: true }));
    };
    return {
      set(selector, value, force) {
        const el = document.querySelector(selector);
        if (!el) throw new Error(`selector not found: ${selector}`);
        // Never overwrite text the user (or a previous run) already entered,
        // unless the program explicitly passes force=true.
        const existing = (el.value ?? el.innerText ?? "").toString().trim();
        if (existing && !force) {
          log(`skip ${selector}: already filled (${existing.slice(0, 40)}…)`);
          return false;
        }
        el.focus?.();
        el.value = value;
        dispatch(selector, "input");
        dispatch(selector, "change");
        return true;
      },
      click(selector) {
        const el = document.querySelector(selector);
        if (!el) throw new Error(`selector not found: ${selector}`);
        el.click();
        return true;
      },
      select(selector, value) {
        const el = document.querySelector(selector);
        if (!el || el.tagName !== "SELECT") throw new Error(`not a select: ${selector}`);
        const opt = Array.from(el.options).find(
          (o) => o.text.trim() === value || o.value === value);
        if (!opt) throw new Error(`option not found: ${value}`);
        el.value = opt.value;
        dispatch(selector, "change");
        return true;
      },
      submit() {
        const btn = document.querySelector(
          '[role="button"][aria-label*="Submit"], button[type="submit"], input[type="submit"]');
        if (btn) btn.click();
        return !!btn;
      },
    };
  }

  async function runProgram(program, values) {
    const py = await loadPyodideOnce();
    // Pass VALUES as a JSON string — simplest, most reliable bridge.
    py.globals.set("VALUES_JSON", JSON.stringify(values || {}));
    py.runPython("import json\nVALUES = json.loads(VALUES_JSON)");
    const page = makePageHelper();
    py.globals.set("page", page);
    try {
      py.runPython(program);
      py.runPython("fill(page, VALUES)");
      log("program finished");
      return { ok: true };
    } catch (e) {
      log("program error:", e.message || String(e));
      return { ok: false, error: String(e.message || e) };
    }
  }

  // ------------------------------------------------------------ messaging
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "GET_DOM_STATE") {
      sendResponse(domState());
      return;
    }
    if (msg.type === "RUN_PROGRAM") {
      runProgram(msg.program, msg.values || {})
        .then(sendResponse)
        .catch((e) => sendResponse({ ok: false, error: String(e) }));
      return true;
    }
    // ack probes (PING_CONTENT) and anything unknown so the caller never
    // sees "receiving end does not exist"
    sendResponse({ ok: true, pong: msg.type === "PING_CONTENT" });
  });

  log("content script ready");
})();
