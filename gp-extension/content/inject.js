/**
 * MAIN-world injector.
 *
 * Content scripts run in an isolated world that cannot see the page's own globals, so `gptkApi`
 * and the session tokens it scrapes are unreachable from here. The only way in is to inject real
 * <script> tags the page executes itself.
 *
 * Order is load-bearing: the shim must define `unsafeWindow` before the GPTK bundle references it,
 * and `commands.js` must not run until `gptkApi` exists. `<script src>` tags added this way execute
 * in insertion order, but only because none of them are `async` — do not "optimise" that away.
 */
(() => {
  if (window.__pcInjected) return;
  window.__pcInjected = true;

  const FILES = [
    "main/gptk-shim.js",
    "vendor/gptk/google-photos-toolkit.user.js",
    "main/commands.js",
  ];

  function injectSequentially(paths) {
    const [head, ...rest] = paths;
    if (!head) return;
    const el = document.createElement("script");
    el.src = chrome.runtime.getURL(head);
    el.async = false;
    // Chain on load rather than dumping all three in at once: GPTK is ~190 KB and parses for long
    // enough that commands.js can otherwise win the race and find gptkApi undefined.
    el.onload = () => {
      el.remove();
      injectSequentially(rest);
    };
    el.onerror = () => console.error("[photo-curator] failed to inject", head);
    (document.head || document.documentElement).appendChild(el);
  }

  injectSequentially(FILES);
})();
