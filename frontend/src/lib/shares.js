// Helpers for shared calendar links (Share modal on /calendar, list on /settings).

// Built from the address the page is served on, so it is whatever URL the
// user (and so whoever they send it to) can actually reach.
export function shareUrl(token) {
  return `${window.location.origin}/shared/${token}`;
}

export async function copyText(text, fallbackInput) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API needs HTTPS or localhost; self-hosted over plain HTTP
    // falls back to the old select-and-copy command.
    if (!fallbackInput) return false;
    fallbackInput.select();
    return document.execCommand("copy");
  }
}

export async function listShares() {
  const res = await fetch("/api/shares");
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail || "Could not load shared calendars");
  return data;
}
