import assert from "node:assert/strict";
import test from "node:test";
import { feedingEvents, petsFor } from "../dashboard/app.mjs";

const payload = {
  events: [
    { occurredAt: "2026-07-01T07:00:00Z", category: "hydration", action: "fountain_change", subject: { id: "pascal", name: "Pascal" }, measurement: { value: 7.5, unit: "g" } },
    { occurredAt: "2026-07-01T08:00:00Z", category: "feeding", action: "food_change", subject: { id: "joule", name: "Joule" }, measurement: { value: -4, unit: "g" } },
    { occurredAt: "2026-07-01T06:00:00Z", category: "feeding", action: "food_change", subject: { id: "pascal", name: "Pascal" }, measurement: { value: 5, unit: "g" } },
    { occurredAt: "2026-07-01T09:00:00Z", category: "feeding", action: "other", subject: { id: "pascal", name: "Pascal" }, measurement: { value: 2, unit: "g" } },
    { occurredAt: "2026-07-01T10:00:00Z", category: "feeding", action: "food_change", subject: { id: "pascal", name: "Pascal" }, context: "0", measurement: { value: 12, unit: "g" } },
  ],
};
const contextualPayload = {
  events: payload.events.map((event) => ({
    ...event,
    context: event.context ?? "1",
    attribution: event.context === "0" ? "owner" : "pet",
  })),
};

test("keeps only food-change events and orders them chronologically", () => {
  const events = feedingEvents(contextualPayload);
  assert.deepEqual(events.map((event) => event.subject.name), ["Pascal", "Joule"]);
  assert.deepEqual(events.map((event) => event.amountGrams), [5, 4]);
});

test("uses the pet attribution rather than the numeric context directly", () => {
  const events = feedingEvents({
    events: [
      { occurredAt: "2026-07-01T07:00:00Z", category: "feeding", action: "food_change", subject: { id: "pascal", name: "Pascal" }, context: "1", attribution: "pet", measurement: { value: 5, unit: "g" } },
      { occurredAt: "2026-07-01T07:15:00Z", category: "feeding", action: "food_change", subject: { id: "pascal", name: "Pascal" }, context: "5", attribution: "food_addition", measurement: { value: 25, unit: "g" } },
      { occurredAt: "2026-07-01T07:30:00Z", category: "feeding", action: "food_change", subject: { id: "pascal", name: "Pascal" }, context: "6", attribution: "system_event", measurement: { value: 3, unit: "g" } },
    ],
  });
  assert.equal(events.length, 1);
  assert.equal(events[0].context, "1");
});

test("derives display pets from feeding data", () => {
  const pets = petsFor(feedingEvents(contextualPayload));
  assert.deepEqual(pets.map((pet) => pet.name), ["Pascal", "Joule"]);
  assert.deepEqual(pets.map((pet) => pet.color), ["#5f9e8b", "#8d79a7"]);
});
