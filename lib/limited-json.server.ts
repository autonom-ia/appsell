const DEFAULT_MAX_BYTES = 65_536;

export class JsonPayloadTooLargeError extends Error {}

export async function readLimitedJson(request: Request, maxBytes = DEFAULT_MAX_BYTES) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new TypeError("Invalid JSON limit.");
  if (request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !==
      "application/json") return null;
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    const size = Number(declared);
    if (!Number.isSafeInteger(size) || size < 0) return null;
    if (size > maxBytes) throw new JsonPayloadTooLargeError();
  }
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new JsonPayloadTooLargeError();
    }
    chunks.push(value);
  }
  try {
    return JSON.parse(Buffer.concat(chunks.map((chunk) => Buffer.from(chunk))).toString("utf8"));
  } catch {
    return null;
  }
}
