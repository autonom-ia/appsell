import { afterEach, describe, expect, it, vi } from "vitest";
import { boundedAssetFetch, discardPendingAssetUpload, formatBytes, MAX_VERIFICATION_WAIT_MS, validIntent, verificationPhaseForElapsed, verificationStatusTimeoutForElapsed, type UploadIntentPayload } from "../app/components/lesson-asset-panel";

const valid: UploadIntentPayload = {
  upload_id: "55000000-0000-4000-8000-000000000001",
  asset_id: "55000000-0000-4000-8000-000000000002",
  upload_url: "http://127.0.0.1:8333/appsell-quarantine/object?X-Amz-Signature=signed",
  upload_origin: "http://127.0.0.1:8333",
  expires_at: "2026-07-11T20:05:00.000Z",
  required_headers: {
    "Content-Type": "application/pdf",
    "x-amz-checksum-sha256": "checksum",
    "x-amz-meta-appsell-upload-id": "55000000-0000-4000-8000-000000000001"
  }
};
const NOW = Date.parse("2026-07-11T20:00:00Z");
const APP_ORIGIN = "http://localhost:3000";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("direct upload intent boundary", () => {
  it("accepts only the complete signed-header allowlist", () => {
    expect(validIntent(valid, APP_ORIGIN, NOW)).toBe(true);
  });

  it.each([
    { ...valid, upload_url: "http://user:secret@127.0.0.1:8333/object?X-Amz-Signature=signed" },
    { ...valid, upload_origin: APP_ORIGIN, upload_url: `${APP_ORIGIN}/collect?X-Amz-Signature=signed` },
    { ...valid, expires_at: "2026-07-11T19:59:59.000Z" },
    { ...valid, upload_url: "http://127.0.0.1:8333/object" },
    { ...valid, upload_id: "not-a-uuid" },
    { ...valid, required_headers: { ...valid.required_headers, Authorization: "secret" } },
    { ...valid, required_headers: { "Content-Type": "application/pdf" } },
    {
      ...valid,
      required_headers: {
        ...valid.required_headers,
        "content-type": "application/pdf"
      }
    }
  ])("rejects malformed or overprivileged intent %#", (intent) => {
    expect(validIntent(intent, APP_ORIGIN, NOW)).toBe(false);
  });
});

describe("lesson asset size labels", () => {
  it("does not round a small PDF down to zero MiB", () => {
    expect(formatBytes(3181)).toBe("3,1 KiB");
    expect(formatBytes(2 * 1024 * 1024)).toBe("2 MiB");
  });
});

describe("lesson asset verification UX", () => {
  it("leaves the service processing but exits the polling state after 15 seconds", () => {
    expect(MAX_VERIFICATION_WAIT_MS).toBe(15_000);
    expect(verificationPhaseForElapsed(14_999)).toBe("verifying");
    expect(verificationPhaseForElapsed(15_000)).toBe("delayed");
    expect(verificationStatusTimeoutForElapsed(14_900)).toBe(100);
    expect(verificationStatusTimeoutForElapsed(15_000)).toBe(1);
  });
});

describe("bounded asset control requests", () => {
  it("reports a timeout as a failure instead of a voluntary cancellation", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    const request = boundedAssetFetch("/upload-intents", { method: "POST" }, 100, new Set());
    const rejection = expect(request).rejects.toThrow("asset_request_timeout");
    await vi.advanceTimersByTimeAsync(100);
    await rejection;
  });

  it("preserves AbortError when transient work is explicitly stopped", async () => {
    const controllers = new Set<AbortController>();
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    const request = boundedAssetFetch("/uploads/status", {}, 10_000, controllers);
    const rejection = expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(controllers).toHaveLength(1);
    for (const controller of controllers) controller.abort();
    await rejection;
    expect(controllers).toHaveLength(0);
  });
});

describe("lesson asset logout discard", () => {
  it("waits for an intent that is still being created and cancels its upload id", async () => {
    let resolveIntent!: (intent: UploadIntentPayload) => void;
    const pendingIntent = new Promise<UploadIntentPayload>((resolve) => { resolveIntent = resolve; });
    const cancelled: string[] = [];
    const discard = discardPendingAssetUpload(null, pendingIntent, async (uploadId) => { cancelled.push(uploadId); });
    expect(cancelled).toEqual([]);
    resolveIntent(valid);
    await discard;
    expect(cancelled).toEqual([valid.upload_id]);
  });

  it("cancels a known uploading or verifying id without waiting for another intent", async () => {
    const cancelled: string[] = [];
    await discardPendingAssetUpload(valid.upload_id, null, async (uploadId) => { cancelled.push(uploadId); });
    expect(cancelled).toEqual([valid.upload_id]);
  });

  it("completes when there is no active upload and propagates cancellation failure", async () => {
    const cancel = async () => { throw new Error("cancel unavailable"); };
    await expect(discardPendingAssetUpload(null, null, cancel)).resolves.toBeUndefined();
    await expect(discardPendingAssetUpload(valid.upload_id, null, cancel)).rejects.toThrow("cancel unavailable");
  });
});
