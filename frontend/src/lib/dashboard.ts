import type { EventOut, Person } from "./events";

export type DashboardEntry = { person: Person; event: EventOut };

export function dashboardGroups(people: Person[]) {
  const entries = people.flatMap((person) => person.events.map((event) => ({ person, event })))
    .sort((a, b) => a.event.days_until - b.event.days_until || a.person.name.localeCompare(b.person.name) || a.event.id - b.event.id);
  const today = entries.filter(({ event }) => event.days_until === 0);
  const thisWeek = entries.filter(({ event }) => event.days_until > 0 && event.days_until <= 7);
  const later = entries.filter(({ event }) => event.days_until > 7 && event.days_until <= 30);
  const next = entries.find(({ event }) => event.days_until > 30);
  // Include every event tied for the next date, not just the first person.
  const nextUp = !thisWeek.length && !later.length && next
    ? entries.filter(({ event }) => event.days_until === next.event.days_until)
    : [];
  return { today, thisWeek, later, nextUp };
}

export function milestone(event: EventOut, today = new Date()) {
  if (!event.year_known || event.year === null) return "";
  const occurrence = new Date(today);
  occurrence.setDate(occurrence.getDate() + event.days_until);
  const years = occurrence.getFullYear() - event.year;
  if (years < 0) return "";
  return event.event_type.name.toLowerCase() === "birthday"
    ? `Turns ${years}`
    : `${years} ${years === 1 ? "year" : "years"}`;
}
