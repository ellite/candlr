// Shared date/age formatting for events, used by Events and the
// calendar. Both read event.days_until, which the API always computes
// server-side (next occurrence from today), so age math never needs to know
// what "today" is on its own.
export const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function formatDate(event) {
  const m = MONTH_NAMES[event.month - 1];
  return event.year_known && event.year ? `${m} ${event.day}, ${event.year}` : `${m} ${event.day}`;
}

export function dueLabel(daysUntil) {
  if (daysUntil === 0) return "Today";
  if (daysUntil === 1) return "Tomorrow";
  return `In ${daysUntil} days`;
}

export function ageLabel(event, standalone = false) {
  if (!event.year_known || !event.year) return "";
  // Age turned = the calendar year of the next occurrence, minus the birth year.
  const nextOccurrence = new Date();
  nextOccurrence.setDate(nextOccurrence.getDate() + event.days_until);
  const turns = nextOccurrence.getFullYear() - event.year;
  if (turns < 0) return "";
  return standalone ? `Turns ${turns}` : ` · turns ${turns}`;
}
