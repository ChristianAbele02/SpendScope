/* ============================================================
   SpendScope – popups (toasts) and fetch error handling
   ============================================================ */

// Bootstrap colour per message category; errors and warnings stay until closed.
const TOAST_STYLES = {
  success: { bg: "text-bg-success", icon: "fa-circle-check", autohide: true },
  info:    { bg: "text-bg-info",    icon: "fa-circle-info",  autohide: true },
  warning: { bg: "text-bg-warning", icon: "fa-triangle-exclamation", autohide: false },
  danger:  { bg: "text-bg-danger",  icon: "fa-circle-xmark", autohide: false },
};
const TOAST_DELAY_MS = 6000;

/** Show a popup message in the top-right corner. */
function showToast(message, category = "info") {
  const style = TOAST_STYLES[category] || TOAST_STYLES.info;
  const el = document.createElement("div");
  el.className = `toast align-items-center border-0 ${style.bg}`;
  el.setAttribute("role", category === "danger" ? "alert" : "status");
  el.setAttribute("aria-live", category === "danger" ? "assertive" : "polite");
  el.setAttribute("aria-atomic", "true");

  const row = document.createElement("div");
  row.className = "d-flex";
  const body = document.createElement("div");
  body.className = "toast-body";
  const icon = document.createElement("i");
  icon.className = `fa-solid ${style.icon} me-2`;
  icon.setAttribute("aria-hidden", "true");
  body.append(icon, document.createTextNode(message));  // text only, never HTML
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close btn-close-white me-2 m-auto";
  close.setAttribute("data-bs-dismiss", "toast");
  close.setAttribute("aria-label", window.APP_I18N.close);
  row.append(body, close);
  el.append(row);

  document.getElementById("toastContainer").append(el);
  el.addEventListener("hidden.bs.toast", () => el.remove());
  bootstrap.Toast.getOrCreateInstance(el, {
    autohide: style.autohide,
    delay: TOAST_DELAY_MS,
  }).show();
}

/**
 * fetch() that returns parsed JSON and turns every failure into a popup.
 * Server errors arrive as {"error": message} (app/errors.py); network
 * failures get a generic "cannot reach the server" message. Rejects with the
 * message so callers can reset their UI.
 */
async function fetchJSON(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (err) {
    showToast(window.APP_I18N.networkError, "danger");
    throw new Error(window.APP_I18N.networkError);
  }
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  if (!response.ok) {
    const message = (data && data.error) || `${window.APP_I18N.error} ${response.status}`;
    showToast(message, "danger");
    throw new Error(message);
  }
  return data;
}

// Messages flashed by the server are rendered as hidden toasts; show them now.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("#toastContainer .toast").forEach((el) => {
    const category = el.dataset.category;
    const style = TOAST_STYLES[category] || TOAST_STYLES.info;
    el.addEventListener("hidden.bs.toast", () => el.remove());
    bootstrap.Toast.getOrCreateInstance(el, {
      autohide: style.autohide,
      delay: TOAST_DELAY_MS,
    }).show();
  });
});
