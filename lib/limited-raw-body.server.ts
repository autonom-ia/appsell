const DEFAULT_MAX_BYTES = 64 * 1024;

export class RawBodyTooLargeError extends Error {}
export class RawBodyUnavailableError extends Error {}

export async function readLimitedRawBody(
  request: Request,
  maxBytes: number = DEFAULT_MAX_BYTES
): Promise<Buffer> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
    throw new TypeError("Raw body limit must be a positive safe integer.");
  }
  const declaredHeader = request.headers.get("content-length");
  if (declaredHeader !== null) {
    if (!/^(0|[1-9][0-9]*)$/.test(declaredHeader)) throw new RawBodyUnavailableError();
    const declared = Number(declaredHeader);
    if (!Number.isSafeInteger(declared)) throw new RawBodyUnavailableError();
    if (declared > maxBytes) throw new RawBodyTooLargeError();
  }
  if (!request.body) return Buffer.alloc(0);

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maxBytes) {
      await reader.cancel();
      throw new RawBodyTooLargeError();
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), length);
}
