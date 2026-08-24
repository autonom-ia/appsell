import "server-only";

import { createHash, randomBytes } from "node:crypto";

/**
 * Access link tokens are minted here and nowhere else.
 *
 * The raw token is handed to the seller's browser exactly once, in the response
 * that creates or rotates the link. What is persisted is its SHA-256 digest, so
 * a database dump, a log line or a backup cannot be turned back into working
 * access. The display prefix is the real first eight characters of the token, so
 * the seller can recognise a link they already copied without the system having
 * to keep the whole thing.
 */
const TOKEN_BYTES = 32;
const TOKEN_SHAPE = /^[0-9a-f]{64}$/;

export interface MintedAccessLinkToken {
  token: string;
  tokenSha256: string;
  tokenPrefix: string;
}

export function mintAccessLinkToken(): MintedAccessLinkToken {
  const token = randomBytes(TOKEN_BYTES).toString("hex");
  return {
    token,
    tokenSha256: accessLinkDigest(token),
    tokenPrefix: token.slice(0, 8)
  };
}

export function accessLinkDigest(token: string) {
  return createHash("sha256").update(token).digest("hex");
}

export function isAccessLinkToken(value: unknown): value is string {
  return typeof value === "string" && TOKEN_SHAPE.test(value);
}

export function accessLinkUrl(origin: string, token: string) {
  return `${origin.replace(/\/+$/, "")}/c/${token}`;
}
