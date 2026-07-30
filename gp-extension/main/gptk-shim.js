/**
 * Userscript-manager compatibility shim. MUST be injected before the GPTK bundle.
 *
 * GPTK ships as a Tampermonkey userscript and expects two globals a userscript manager would
 * normally provide. Injected as a plain page script neither exists, and the bundle throws on its
 * first reference — which is exactly the failure mode that made the reference implementation's
 * HFP-style "it silently did nothing" bugs hard to spot. Define them up front instead.
 *
 *   unsafeWindow            — in a userscript this is the page's real window; here we ARE the page.
 *   GM_registerMenuCommand  — registers a Tampermonkey menu entry. We drive GPTK programmatically,
 *                             so there is no menu to register into; swallow it.
 */
(() => {
  if (!("unsafeWindow" in window)) {
    window.unsafeWindow = window;
  }
  if (typeof window.GM_registerMenuCommand !== "function") {
    window.GM_registerMenuCommand = () => {};
  }
})();
