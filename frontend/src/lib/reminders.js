// Reminder-state indicators shared by the Events card grid and the person
// detail sheet, so both draw the same bell.
const BELL =
  "M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.311 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0";
const BELL_OFF =
  "M9.143 17.082a24.248 24.248 0 003.844.148m-3.844-.148a23.856 23.856 0 01-5.455-1.31 8.964 8.964 0 002.3-5.542m3.155 6.852a3 3 0 005.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 003.536-1.003A8.967 8.967 0 0118 9.75V9A6 6 0 006.53 6.53m10.245 10.245L6.53 6.53M3 3l3.53 3.53";

function icon(path, sizeClass) {
  return `<svg xmlns="http://www.w3.org/2000/svg" class="${sizeClass}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="${path}" /></svg>`;
}

export function hasReminder(person) {
  return person.events.some((event) => event.notify);
}

// Corner badge for a card: only drawn when at least one of its dates has
// reminders on, so cards without any stay uncluttered.
export function cardBellHtml(person) {
  if (!hasReminder(person)) return "";
  const on = person.events.filter((event) => event.notify).length;
  const label = person.events.length > 1 ? `Reminders on for ${on} of ${person.events.length} dates` : "Reminders on";
  return `<span class="absolute top-2 right-2 text-zinc-500 dark:text-zinc-400" role="img" aria-label="${label}" title="${label}">${icon(BELL, "w-4 h-4")}</span>`;
}

// Per-date line for the detail sheet: always shown, on or off, so the state
// is readable without opening the edit form.
export function reminderLineHtml(event) {
  return event.notify
    ? `<div class="flex items-center gap-1 text-[13px] font-medium text-zinc-700 dark:text-zinc-200 mt-0.5">${icon(BELL, "w-3.5 h-3.5")}Reminders on</div>`
    : `<div class="flex items-center gap-1 text-[13px] text-zinc-500 dark:text-zinc-400 mt-0.5">${icon(BELL_OFF, "w-3.5 h-3.5")}Reminders off</div>`;
}
