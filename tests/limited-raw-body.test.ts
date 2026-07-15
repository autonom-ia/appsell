import { describe, expect, it } from "vitest";

import {
  RawBodyTooLargeError,
  RawBodyUnavailableError,
  readLimitedRawBody
} from "../lib/limited-raw-body.server";

describe("limited raw request bodies", () => {
  it("preserves JSON bytes exactly, including whitespace and Unicode", async () => {
    const body = Buffer.from('{\n  "mensagem": "olá", "ordem": [2, 1]\n}\n', "utf8");
    const request = new Request("https://sell.example/api/webhook", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": String(body.length)
      },
      body
    });

    await expect(readLimitedRawBody(request)).resolves.toEqual(body);
  });

  it("accepts 64 KiB and rejects 64 KiB plus one", async () => {
    const exact = Buffer.alloc(64 * 1024, 0x61);
    await expect(readLimitedRawBody(new Request("https://sell.example/api/webhook", {
      method: "POST", body: exact
    }))).resolves.toHaveLength(64 * 1024);

    const excess = Buffer.alloc(64 * 1024 + 1, 0x61);
    await expect(readLimitedRawBody(new Request("https://sell.example/api/webhook", {
      method: "POST", body: excess
    }))).rejects.toBeInstanceOf(RawBodyTooLargeError);
  });

  it("rejects oversized or malformed declared lengths before streaming", async () => {
    for (const contentLength of ["65537", "-1", "+1", "1.5", "not-a-number"]) {
      const request = new Request("https://sell.example/api/webhook", {
        method: "POST",
        headers: { "content-length": contentLength },
        body: "x"
      });
      if (contentLength === "65537") {
        await expect(readLimitedRawBody(request)).rejects.toBeInstanceOf(RawBodyTooLargeError);
      } else {
        await expect(readLimitedRawBody(request)).rejects.toBeInstanceOf(RawBodyUnavailableError);
      }
    }
  });

  it("rejects invalid caller limits", async () => {
    await expect(readLimitedRawBody(new Request("https://sell.example"), 0))
      .rejects.toBeInstanceOf(TypeError);
  });
});
