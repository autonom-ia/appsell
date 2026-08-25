import { describe, expect, it } from "vitest";

import { nextCreateKey, type CreateKey } from "../app/components/lesson-material-panel";

/**
 * A dropped connection is the case idempotency exists for: the server may have
 * accepted the material before the answer was lost, so the retry must replay
 * rather than add a second row.
 *
 * The trap is that the seller can edit the fields between the failure and the
 * retry. Replaying then means the server answers for the first material while
 * the panel reports the second — a divergence nobody sees. These tests hold the
 * line between "same request again" and "a different request".
 */
describe("create key across a failed attempt", () => {
  const payload = JSON.stringify({ kind: "external", title: "Guia A", url: "https://exemplo.com/a" });
  const edited = JSON.stringify({ kind: "external", title: "Guia B", url: "https://exemplo.com/a" });

  function mintCounter() {
    let count = 0;
    return () => `key-${++count}`;
  }

  it("mints a key on the first attempt", () => {
    const key = nextCreateKey(null, payload, mintCounter());

    expect(key).toEqual({ key: "key-1", payload });
  });

  it("replays the same key when the retry sends the same fields", () => {
    const mint = mintCounter();
    const first = nextCreateKey(null, payload, mint);

    const retry = nextCreateKey(first, payload, mint);

    expect(retry.key).toBe(first.key);
    expect(retry).toBe(first);
  });

  it("earns a new key when the seller edits the fields after a failure", () => {
    const mint = mintCounter();
    const first = nextCreateKey(null, payload, mint);

    const afterEdit = nextCreateKey(first, edited, mint);

    expect(afterEdit.key).not.toBe(first.key);
    expect(afterEdit.payload).toBe(edited);
  });

  it("starts fresh once the previous attempt was cleared", () => {
    const mint = mintCounter();
    const first = nextCreateKey(null, payload, mint);

    // A rejected request drops the key, so the same fields are a new operation.
    const afterRejection = nextCreateKey(null, payload, mint);

    expect(afterRejection.key).not.toBe(first.key);
  });

  it("treats a changed course version as a different request", () => {
    const mint = mintCounter();
    const atVersion3 = JSON.stringify({ expected_course_version: "3", title: "Guia A" });
    const atVersion4 = JSON.stringify({ expected_course_version: "4", title: "Guia A" });
    const first: CreateKey = nextCreateKey(null, atVersion3, mint);

    const next = nextCreateKey(first, atVersion4, mint);

    expect(next.key).not.toBe(first.key);
  });
});
