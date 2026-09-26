// Shows a message in the global toast region (see components/Toast.astro).
// Use instead of alert()/confirm() - browser dialogs block the page and
// can't be styled, so we never use them for feedback.
export function showToast(message, type = "success", duration = 4000) {
  const region = document.getElementById("toast-region");
  if (!region) return;

  const toast = document.createElement("div");
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.className = [
    "pointer-events-auto max-w-sm text-sm text-center px-3.5 py-2.5 rounded-lg shadow-lg transition-all duration-200 opacity-0 -translate-y-1 text-white",
    type === "error" ? "bg-red-600" : "bg-emerald-600",
  ].join(" ");
  toast.textContent = message;
  region.appendChild(toast);

  requestAnimationFrame(() => toast.classList.remove("opacity-0", "-translate-y-1"));

  setTimeout(() => {
    toast.classList.add("opacity-0");
    toast.addEventListener("transitionend", () => toast.remove(), { once: true });
  }, duration);
}
