/**
 * Minimal DOM helpers.
 *
 * The extension has no build step on purpose, so there is no JSX and no framework. `h()` covers
 * everything the Sync and Duplicates tabs need. It also means nothing is ever built by string
 * concatenation into innerHTML — filenames come from the user's own library and would otherwise be
 * an injection route into our own page.
 */

/**
 * Is this second argument a props bag, or just the first child?
 *
 * Nodes, strings, numbers and arrays are all children. Only a plain object is props. Without this
 * check `h("h2", "Title")` would iterate the string's character indices as attributes, and
 * `h("div", childEl)` would silently drop the child — both fail by rendering an empty element
 * rather than throwing, which makes them miserable to track down.
 */
const isProps = (v) =>
  v != null && typeof v === "object" && !Array.isArray(v) && !(v instanceof Node);

/**
 * h("div.card", {onclick}, child, [children])
 * h("h2", "Title")                  — props may be omitted entirely
 * Tag may carry .classes and #id, e.g. "button.btn.primary#go".
 */
export function h(spec, props, ...children) {
  if (!isProps(props)) {
    if (props !== undefined) children.unshift(props);
    props = null;
  }

  const [tagAndId, ...classes] = String(spec).split(".");
  const [tag, id] = tagAndId.split("#");
  const el = document.createElement(tag || "div");
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(" ");

  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "class") {
      el.className = [el.className, value].filter(Boolean).join(" ");
    } else if (key === "style" && typeof value === "object") {
      Object.assign(el.style, value);
    } else if (key === "dataset") {
      Object.assign(el.dataset, value);
    } else if (key in el && key !== "list") {
      el[key] = value;
    } else {
      el.setAttribute(key, value);
    }
  }

  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Replace an element's contents. */
export function render(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export const fmtInt = (n) => (n ?? 0).toLocaleString();

export const fmtPct = (n) => `${((n ?? 0) * 100).toFixed(1)}%`;

export function fmtDate(iso) {
  if (!iso) return "no date";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "no date";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function fmtBytes(n) {
  if (!n) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Send a message to the service worker, normalising a dead worker into a readable error. */
export function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response ?? { ok: false, error: "No response from the extension worker." });
    });
  });
}
