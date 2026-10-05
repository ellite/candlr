export type EventUnit = "day" | "week" | "month" | "year";
// How often dates of this type come around: every `interval` `unit`s.
export type EventType = { id: number; name: string; is_default: boolean; interval: number; unit: EventUnit };
export type EventOut = {
  id: number;
  event_type: EventType;
  month: number;
  day: number;
  year: number | null;
  year_known: boolean;
  notes: string | null;
  notify: boolean;
  days_until: number;
  days_since: number;
};
export type Person = { id: number; name: string; image_url: string | null; events: EventOut[] };
