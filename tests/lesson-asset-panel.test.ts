import { describe, expect, it } from "vitest";
import { discardPendingAssetUpload, formatBytes, validIntent, type UploadIntentPayload } from "../app/components/lesson-asset-panel";

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
