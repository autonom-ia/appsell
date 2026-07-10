import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export function randomOpaque(bytes = 32) {
  return randomBytes(bytes).toString("base64url");
}

export function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("base64url");
}

export function hmacSha256(key: Buffer, value: string) {
  return createHmac("sha256", key).update(value, "utf8").digest("base64url");
}

export function pkceChallenge(verifier: string) {
  return sha256(verifier);
}

export function safeEqual(left: string, right: string) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}
