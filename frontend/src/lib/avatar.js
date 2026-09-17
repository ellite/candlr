// Shared by Events, the dashboard, and the calendar to render a
// person's photo (or initials fallback) in card/list/grid cells.
export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

export function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0].toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function avatarHtml(person, sizeClass) {
  if (person.image_url) {
    return `<img src="/api${person.image_url}" alt="" class="${sizeClass} rounded-full object-cover" />`;
  }
  return `<div class="${sizeClass} rounded-full bg-zinc-900 dark:bg-white flex items-center justify-center flex-shrink-0">
    <span class="font-medium text-white dark:text-zinc-900">${escapeHtml(initials(person.name))}</span>
  </div>`;
}
