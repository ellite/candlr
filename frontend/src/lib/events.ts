export type EventType = { id: number; name: string; is_default: boolean };
export type EventOut = {
  id: number;
  event_type: EventType;
  month: number;
  day: number;
  year: number | null;
  year_known: boolean;
  notes: string | null;
  days_until: number;
};
export type Person = { id: number; name: string; image_url: string | null; events: EventOut[] };
