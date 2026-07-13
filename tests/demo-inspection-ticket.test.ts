import { describe, expect, it } from "vitest";

import {
  parseDemoInspectionTicket,
  serializeDemoInspectionTicket
} from "../lib/demo-inspection-ticket";

const id = "61000000-0000-4000-8000-000000000001";
const token = "A".repeat(43);

describe("demo inspection ticket cookie", () => {
  it("round-trips one UUID and one 256-bit base64url token", () => {
    expect(parseDemoInspectionTicket(serializeDemoInspectionTicket({ id, token }))).toEqual({ id, token });
  });

  it.each([
    "",
    `${id}.${"A".repeat(42)}`,
    `${id}.${"A".repeat(129)}`,
    `${id}.${"A".repeat(42)}+`,
    `${id}.${token}.extra`,
    `not-a-uuid.${token}`
  ])("rejects malformed bearer material without partial parsing: %s", (value) => {
    expect(parseDemoInspectionTicket(value)).toBeNull();
  });

  it("refuses to serialize malformed values", () => {
    expect(() => serializeDemoInspectionTicket({ id, token: "short" })).toThrow(
      "invalid_demo_inspection_ticket"
    );
  });
});
