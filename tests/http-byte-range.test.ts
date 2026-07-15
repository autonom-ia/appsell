import { describe, expect, it } from "vitest";

import { parseSingleByteRange } from "../lib/http-byte-range";

describe("single HTTP byte ranges", () => {
  it("accepts bounded, open-ended and suffix ranges", () => {
    expect(parseSingleByteRange("bytes=0-99", 1_000)).toEqual({ start: 0, end: 99 });
    expect(parseSingleByteRange("bytes=900-", 1_000)).toEqual({ start: 900, end: 999 });
    expect(parseSingleByteRange("bytes=-100", 1_000)).toEqual({ start: 900, end: 999 });
    expect(parseSingleByteRange("bytes=0-9999", 1_000)).toEqual({ start: 0, end: 999 });
    expect(parseSingleByteRange("Bytes=0-99", 1_000)).toEqual({ start: 0, end: 99 });
  });

  it("rejects malformed, multiple and unsatisfiable ranges", () => {
    expect(parseSingleByteRange("bytes=1000-", 1_000)).toBe("unsatisfiable");
    expect(parseSingleByteRange("bytes=20-10", 1_000)).toBe("unsatisfiable");
    expect(parseSingleByteRange("bytes=-0", 1_000)).toBe("unsatisfiable");
    expect(parseSingleByteRange("bytes=0-1,4-5", 1_000)).toBe("unsatisfiable");
  });

  it("ignores unknown range units", () => {
    expect(parseSingleByteRange("items=0-1", 1_000)).toBeNull();
    expect(parseSingleByteRange("malformed", 1_000)).toBeNull();
  });

  it("returns null when no Range header is present", () => {
    expect(parseSingleByteRange(null, 1_000)).toBeNull();
  });
});
