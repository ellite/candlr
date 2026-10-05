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

// Wraps the digits in an age/milestone string (e.g. "Turns 36") in a bold,
// accent-colored span so the number stands out. Purely presentational, so
// it's applied at render time rather than baked into the label functions
// themselves - those stay plain text for callers (like tests) that don't
// want markup. Safe to inject as-is: the input is always our own generated
// "Word N" text, never user-controlled. Browser-only call sites (built as
// innerHTML strings); server-rendered Astro components should use
// splitNumber instead so JSX handles escaping.
export function highlightNumber(text) {
  return text.replace(/\d+/, (n) => `<strong class="font-semibold text-accent dark:text-accent-light">${n}</strong>`);
}

// Same idea as highlightNumber, but splits the string into parts instead of
// building an HTML string - for Astro frontmatter (runs server-side, no DOM,
// so the innerHTML-based escapeHtml in avatar.js isn't available there).
export function splitNumber(text) {
  const match = text.match(/\d+/);
  if (!match) return { before: text, number: "", after: "" };
  return {
    before: text.slice(0, match.index),
    number: match[0],
    after: text.slice(match.index + match[0].length),
  };
}

export function dueLabel(daysUntil) {
  if (daysUntil === 0) return "Today";
  if (daysUntil === 1) return "Tomorrow";
  return `In ${daysUntil} days`;
}

// ── Cadence ──
// An event type repeats "every `interval` `unit`s" (day, week, month, year;
// default every 1 year). Mirrors backend/app/events_logic.py: the date's
// month/day/year is where the cadence starts, and every cadence except
// "every 1 year" and "every 1 month" counts from that start, so it needs a
// known year.
export function isYearly(type) {
  return type.interval === 1 && type.unit === "year";
}

export function needsStartYear(type) {
  return !(type.interval === 1 && (type.unit === "month" || type.unit === "year"));
}

export function cadenceLabel(type) {
  return type.interval === 1 ? `every ${type.unit}` : `every ${type.interval} ${type.unit}s`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

// Feb 29 in a non-leap year is Feb 28, like the backend.
function safeDate(year, month, day) {
  return new Date(year, month - 1, Math.min(day, month === 2 ? daysInMonth(year, 2) : day));
}

function utcDays(date) {
  return Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS);
}

// Whole `unit`s (the type's unit) from an event's start date to a date.
export function elapsedUnits(event, date) {
  const { unit } = event.event_type;
  if (unit === "day" || unit === "week") {
    const days = utcDays(date) - utcDays(safeDate(event.year, event.month, event.day));
    return unit === "week" ? Math.floor(days / 7) : days;
  }
  if (unit === "month") return (date.getFullYear() - event.year) * 12 + date.getMonth() + 1 - event.month;
  return date.getFullYear() - event.year;
}

// "7 months", for a date on a non-yearly cadence; "" before it has started
// or when the start year isn't known.
function countLabel(event, date) {
  if (!event.year_known || !event.year) return "";
  const count = elapsedUnits(event, date);
  return count > 0 ? `${count} ${event.event_type.unit}${count === 1 ? "" : "s"}` : "";
}

// Whether the event lands on this calendar day (month is 1-12). Used to
// place dates on the calendar page for whichever month/year is in view.
export function occursOn(event, year, month, day) {
  const type = event.event_type;
  const known = event.year_known && event.year;
  if (known && needsStartYear(type)) {
    const cell = new Date(year, month - 1, day);
    if (cell < safeDate(event.year, event.month, event.day)) return false;
    if (type.unit === "day" || type.unit === "week") {
      const period = type.interval * (type.unit === "week" ? 7 : 1);
      return (utcDays(cell) - utcDays(safeDate(event.year, event.month, event.day))) % period === 0;
    }
    if (type.unit === "month") {
      const months = (year - event.year) * 12 + month - event.month;
      return months % type.interval === 0 && day === Math.min(event.day, daysInMonth(year, month));
    }
    const years = year - event.year;
    return years % type.interval === 0 && month === event.month && day === (event.month === 2 && event.day === 29 ? daysInMonth(year, 2) : event.day);
  }
  // Every month or every year on the same day, which need no start year.
  // Not shown before the year it started in, when that is known.
  if (type.unit === "month") {
    if (known && (year - event.year) * 12 + month - event.month < 0) return false;
    return day === Math.min(event.day, daysInMonth(year, month));
  }
  if (known && year < event.year) return false;
  return month === event.month && day === event.day;
}

export function ageLabel(event, standalone = false) {
  if (!event.year_known || !event.year) return "";
  const nextOccurrence = new Date();
  nextOccurrence.setDate(nextOccurrence.getDate() + event.days_until);
  if (!isYearly(event.event_type)) {
    const label = countLabel(event, nextOccurrence);
    return label && !standalone ? ` · ${label}` : label;
  }
  // Age turned = the calendar year of the next occurrence, minus the birth year.
  const turns = nextOccurrence.getFullYear() - event.year;
  if (turns < 0) return "";
  return standalone ? `Turns ${turns}` : ` · turns ${turns}`;
}

// Like ageLabel, but for a specific occurrence date (e.g. a day the user
// clicked on in the calendar while browsing a past or future year) rather
// than the next upcoming one. Returns "" for a date before the event
// happened at all (the person/anniversary didn't exist yet). Yearly dates:
// past occurrences read "Turned X", today's and future ones "Turns X".
// Other cadences read "7 months".
export function ageLabelForOccurrence(event, occurrence, today = new Date()) {
  if (!event.year_known || !event.year) return "";
  if (!isYearly(event.event_type)) return countLabel(event, occurrence);
  const occurrenceYear = occurrence.getFullYear();
  if (occurrenceYear < event.year) return "";
  const turns = occurrenceYear - event.year;
  const occurrenceDate = new Date(occurrenceYear, event.month - 1, event.day);
  const todayDate = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return occurrenceDate < todayDate ? `Turned ${turns}` : `Turns ${turns}`;
}
