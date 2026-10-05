import assert from "node:assert/strict";
import { test } from "node:test";
import { dashboardGroups, milestone } from "../src/lib/dashboard.ts";

const event = (id, days_until, overrides = {}) => ({
  id, days_until, days_since: days_until === 0 ? 0 : 365 - days_until,
  month: 1, day: 1, year: 2000, year_known: true, notes: null,
  event_type: { id: 1, name: "Birthday", is_default: true, interval: 1, unit: "year" }, ...overrides,
});
const person = (id, name, events) => ({ id, name, events, image_url: null });
const ids = (entries) => entries.map(({ event }) => event.id);

test("groups every event independently at the 0, 7 and 30 day boundaries", () => {
  const groups = dashboardGroups([person(1, "Alex", [
    event(31, 31), event(30, 30), event(8, 8), event(7, 7), event(1, 1), event(0, 0),
  ])]);
  assert.deepEqual(ids(groups.today), [0]);
  assert.deepEqual(groups.lastWeek, []);
  assert.deepEqual(ids(groups.thisWeek), [1, 7]);
  assert.deepEqual(ids(groups.later), [8, 30]);
  assert.deepEqual(groups.nextUp, []);
});

test("next up includes everyone on the nearest future date, in name order", () => {
  const groups = dashboardGroups([
    person(1, "Zoe", [event(1, 60)]),
    person(2, "Alex", [event(2, 60), event(3, 61), event(4, 0)]),
  ]);
  assert.deepEqual(ids(groups.nextUp), [2, 1]);
  assert.deepEqual(ids(groups.today), [4]);
});

test("empty accounts produce empty sections", () => {
  assert.deepEqual(dashboardGroups([]), { today: [], lastWeek: [], thisWeek: [], later: [], nextUp: [] });
});

test("last week excludes today and orders the most recent events first", () => {
  const groups = dashboardGroups([
    person(1, "Zoe", [event(1, 359, { days_since: 6 })]),
    person(2, "Alex", [event(2, 358, { days_since: 7 }), event(3, 364, { days_since: 1 })]),
    person(3, "Ben", [event(4, 357, { days_since: 8 }), event(5, 0, { days_since: 0 })]),
  ]);
  assert.deepEqual(ids(groups.lastWeek), [3, 1, 2]);
});

test("milestones account for year rollover and unknown years", () => {
  const today = new Date(2026, 11, 31, 12);
  assert.equal(milestone(event(1, 1), today), "Turns 27");
  assert.equal(milestone(event(1, 0), today), "Turns 26");
  assert.equal(milestone(event(1, 1, { year_known: false }), today), "");
  assert.equal(milestone(event(1, 1, { year: null }), today), "");
  assert.equal(milestone(event(1, 1, { year: 2030 }), today), "");
  assert.equal(milestone(event(1, 1, { year: 2026, event_type: { name: "Anniversary", interval: 1, unit: "year" } }), today), "1 year");
});

test("non-yearly cadences count units since the start, yearly ones keep their age", () => {
  const today = new Date(2026, 9, 8); // Oct 8, 2026
  const typed = (interval, unit, overrides = {}) =>
    event(1, 0, { month: 3, day: 8, year: 2026, event_type: { id: 9, name: "Check-in", is_default: false, interval, unit }, ...overrides });
  assert.equal(milestone(typed(1, "month"), today, 0), "7 months");
  assert.equal(milestone(typed(1, "month", { month: 9 }), today, 0), "1 month");
  assert.equal(milestone(typed(7, "month"), today, 0), "7 months");
  assert.equal(milestone(typed(1, "week", { month: 9, day: 24 }), today, 0), "2 weeks");
  assert.equal(milestone(typed(37, "day", { month: 8, day: 22 }), today, 0), "47 days");
  assert.equal(milestone(typed(5, "year", { year: 2016, month: 10, day: 8 }), today, 0), "10 years");
  assert.equal(milestone(typed(1, "month", { year: null, year_known: false }), today, 0), "");
  assert.equal(milestone(event(1, 0, { year: 1990, month: 10, day: 8 }), today, 0), "Turns 36");
});
