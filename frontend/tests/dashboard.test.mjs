import assert from "node:assert/strict";
import { test } from "node:test";
import { dashboardGroups, milestone } from "../src/lib/dashboard.ts";

const event = (id, days_until, overrides = {}) => ({
  id, days_until, month: 1, day: 1, year: 2000, year_known: true, notes: null,
  event_type: { id: 1, name: "Birthday", is_default: true }, ...overrides,
});
const person = (id, name, events) => ({ id, name, events, image_url: null });
const ids = (entries) => entries.map(({ event }) => event.id);

test("groups every event independently at the 0, 7 and 30 day boundaries", () => {
  const groups = dashboardGroups([person(1, "Alex", [
    event(31, 31), event(30, 30), event(8, 8), event(7, 7), event(1, 1), event(0, 0),
  ])]);
  assert.deepEqual(ids(groups.today), [0]);
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
  assert.deepEqual(dashboardGroups([]), { today: [], thisWeek: [], later: [], nextUp: [] });
});

test("milestones account for year rollover and unknown years", () => {
  const today = new Date(2026, 11, 31, 12);
  assert.equal(milestone(event(1, 1), today), "Turns 27");
  assert.equal(milestone(event(1, 0), today), "Turns 26");
  assert.equal(milestone(event(1, 1, { year_known: false }), today), "");
  assert.equal(milestone(event(1, 1, { year: null }), today), "");
  assert.equal(milestone(event(1, 1, { year: 2030 }), today), "");
  assert.equal(milestone(event(1, 1, { year: 2026, event_type: { name: "Anniversary" } }), today), "1 year");
});
