import {h, render} from "../../lib/dom.js";
export async function setupTab(root, ctx) {
  const input = h("input", {type: "password", id: "local-token", autocomplete: "off", required: true,
                            placeholder: "Paste your local pairing token"});
  const status = h("p", {role: "status", "aria-live": "polite"});
  const button = h("button.primary", {type: "submit"}, "Pair and test connection");
  const form = h("form", {onsubmit: async e => {
    e.preventDefault(); button.disabled = true;
    try {
      await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
      await chrome.storage.local.set({"pc-token": input.value.trim()});
      const health = await ctx.api.health();
      input.value = "";
      status.textContent = health.live_trash_enabled ? "Connected. Live trash requires review and confirmation."
        : "Connected. Dry runs only; live trash is disabled in the backend.";
      await ctx.refreshStatus();
    } catch (error) {status.textContent = error.message + " Check the token and allowed extension ID, then retry.";}
    finally {button.disabled = false;}
  }}, h("label", {htmlFor: "local-token"}, "Local pairing token"), input, button, status);
  render(root, h("div.card", h("h2", "Connect to your local catalog"),
    h("p.sub", "Start the backend, add this extension ID to its allowed list, then paste the pairing token. The token stays in this Chrome profile and is never sent to Google."),
    h("p", "Extension ID: ", h("code", chrome.runtime.id)),
    h("p.sub", "Setup commands are in the repository README. After pairing, open Library Sync to scan metadata and analyze thumbnails."), form));
}
