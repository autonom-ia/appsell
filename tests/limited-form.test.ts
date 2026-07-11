import { describe, expect, it } from "vitest";

import { PayloadTooLargeError, readLimitedForm } from "../lib/limited-form.server";

describe("limited form parser", () => {
  it("accepts real urlencoded content types with charset", async () => {
    const request = new Request("http://localhost/form", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams({ title: "Produto real" })
    });
    expect((await readLimitedForm(request))?.get("title")).toBe("Produto real");
  });

  it("preserves multipart boundaries", async () => {
    const input = new FormData();
    input.set("course_title", "Curso inicial");
    const request = new Request("http://localhost/form", { method: "POST", body: input });
    expect((await readLimitedForm(request))?.get("course_title")).toBe("Curso inicial");
  });

  it("rejects oversized declared and streamed bodies", async () => {
    const declared = new Request("http://localhost/form", {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "content-length": "20"
      },
      body: "field=value"
    });
    await expect(readLimitedForm(declared, 10)).rejects.toBeInstanceOf(PayloadTooLargeError);

    const streamed = new Request("http://localhost/form", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("field="));
          controller.enqueue(new TextEncoder().encode("x".repeat(20)));
          controller.close();
        }
      }),
      duplex: "half"
    } as RequestInit & { duplex: "half" });
    await expect(readLimitedForm(streamed, 10)).rejects.toBeInstanceOf(PayloadTooLargeError);
  });

  it("rejects malformed lengths and unsupported media types without parsing", async () => {
    const malformed = new Request("http://localhost/form", {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "content-length": "not-a-number"
      },
      body: "field=value"
    });
    expect(await readLimitedForm(malformed)).toBeNull();

    const json = new Request("http://localhost/form", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}"
    });
    expect(await readLimitedForm(json)).toBeNull();
  });
});
