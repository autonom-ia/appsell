import { describe, expect, it } from "vitest";
import {
  browserUploadIntent,
  configuredStorageUploadOrigin,
  trustedStorageDownloadUrl
} from "../lib/storage-upload-policy.server";

const NOW = Date.parse("2026-07-11T20:00:00Z");
const APP_ORIGIN = "https://sell.autonomia.site";
const STORAGE_ORIGIN = "https://assets.autonomia.site";
const VALID = {
  upload_id: "55000000-0000-4000-8000-000000000001",
  asset_id: "55000000-0000-4000-8000-000000000002",
  upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/object?X-Amz-Signature=signed`,
  expires_at: "2026-07-11T20:05:00.000Z",
  required_headers: {
    "Content-Type": "application/pdf",
    "x-amz-checksum-sha256": "checksum",
    "x-amz-meta-appsell-upload-id": "55000000-0000-4000-8000-000000000001"
  }
};

describe("storage upload boundary", () => {
  it("accepts only the configured cross-origin signed destination", () => {
    expect(browserUploadIntent(VALID, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toMatchObject({
      upload_origin: STORAGE_ORIGIN,
      upload_url: VALID.upload_url
    });
  });

  it.each([
    { ...VALID, upload_url: `${APP_ORIGIN}/api/collect?X-Amz-Signature=signed` },
    { ...VALID, upload_url: "https://evil.example/object?X-Amz-Signature=signed" },
    { ...VALID, expires_at: "2026-07-11T19:59:59.000Z" },
    { ...VALID, expires_at: "2026-07-11T20:10:00.000Z" },
    { ...VALID, required_headers: { ...VALID.required_headers, Authorization: "secret" } }
  ])("rejects an unsafe or stale intent %#", (intent) => {
    expect(browserUploadIntent(intent, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toBeNull();
  });

  it("requires HTTPS for the configured production storage origin", () => {
    expect(() => configuredStorageUploadOrigin({
      NODE_ENV: "production",
      APPSELL_STORAGE_PUBLIC_ORIGIN: "http://assets.autonomia.site"
    })).toThrow(/HTTPS/);
  });

  it("accepts downloads only from the exact configured storage origin", () => {
    const signed = `${STORAGE_ORIGIN}/ready/material.pdf?X-Amz-Signature=signed`;
    expect(trustedStorageDownloadUrl(signed, 60, STORAGE_ORIGIN, {
      NODE_ENV: "production"
    })).toBe(true);
    expect(trustedStorageDownloadUrl(
      "https://evil.example/ready/material.pdf?X-Amz-Signature=signed",
      60,
      STORAGE_ORIGIN,
      { NODE_ENV: "production" }
    )).toBe(false);
    expect(trustedStorageDownloadUrl(
      "https://assets.autonomia.site.evil.example/material.pdf?X-Amz-Signature=signed",
      60,
      STORAGE_ORIGIN,
      { NODE_ENV: "production" }
    )).toBe(false);
  });

  it("rejects an HTTP download origin in production", () => {
    expect(trustedStorageDownloadUrl(
      "http://127.0.0.1:58333/ready/material.pdf?X-Amz-Signature=signed",
      60,
      "http://127.0.0.1:58333",
      { NODE_ENV: "production" }
    )).toBe(false);
  });
});
