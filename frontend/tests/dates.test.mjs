import assert from "node:assert/strict";
import { test } from "node:test";
import { cadenceLabel, needsStartYear, occursOn } from "../src/lib/dates.js";

const type = (interval, unit) => ({ id: 1, name: "T", is_default: false, interval, unit });
const event = (interval, unit, month, day, year = 2026) => ({
  month, day, year, year_known: year !== null, event_type: type(interval, unit),
});
// Every (month, day) of a year on which the event occurs, as "M-D" strings.
const days = (ev, year) => {
  const found = [];
  for (let m = 1; m <= 12; m++) {
    for (let d = 1; d <= new Date(year, m, 0).getDate(); d++) if (occursOn(ev, year, m, d)) found.push(`${m}-${d}`);
  }
  return found;
};

test("labels and start year rules", () => {
  assert.equal(cadenceLabel(type(1, "week")), "every week");
  assert.equal(cadenceLabel(type(37, "day")), "every 37 days");
  assert.equal(needsStartYear(type(1, "year")), false);
  assert.equal(needsStartYear(type(1, "month")), false);
  assert.equal(needsStartYear(type(2, "month")), true);
  assert.equal(needsStartYear(type(1, "day")), true);
});

test("every year lands once a year, not before the start year", () => {
  assert.deepEqual(days(event(1, "year", 3, 8, 1990), 2026), ["3-8"]);
  assert.deepEqual(days(event(1, "year", 3, 8, 2030), 2026), []);
  assert.deepEqual(days(event(1, "year", 3, 8, null), 2026), ["3-8"]);
});

test("every month lands on its day, clamped to short months", () => {
  assert.equal(days(event(1, "month", 3, 8, null), 2026).length, 12);
  const thirtyFirst = days(event(1, "month", 1, 31, 2026), 2026);
  assert.ok(thirtyFirst.includes("2-28") && thirtyFirst.includes("4-30") && thirtyFirst.includes("1-31"));
  assert.equal(days(event(1, "month", 7, 8, 2026), 2026).length, 6); // Jul-Dec, not before it started
});

test("every 37 days, 4 weeks, 7 months and 5 years count from the start date", () => {
  assert.deepEqual(days(event(37, "day", 1, 1), 2026).slice(0, 3), ["1-1", "2-7", "3-16"]);
  assert.deepEqual(days(event(4, "week", 9, 1), 2026), ["9-1", "9-29", "10-27", "11-24", "12-22"]);
  assert.deepEqual(days(event(7, "month", 1, 15), 2026), ["1-15", "8-15"]);
  assert.deepEqual(days(event(7, "month", 1, 15), 2027), ["3-15", "10-15"]);
  assert.deepEqual(days(event(5, "year", 3, 8, 2020), 2025), ["3-8"]);
  assert.deepEqual(days(event(5, "year", 3, 8, 2020), 2026), []);
  assert.deepEqual(days(event(4, "year", 2, 29, 2024), 2028), ["2-29"]);
});
