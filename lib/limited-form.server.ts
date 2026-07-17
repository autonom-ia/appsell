const DEFAULT_MAX_BYTES = 16_384;

export class PayloadTooLargeError extends Error {}

export async function readLimitedForm(
  request: Request,
  maxBytes: number | undefined = DEFAULT_MAX_BYTES
): Promise<FormData | null> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
    throw new TypeError("Form body limit must be a positive safe integer.");
  }
  const contentType = request.headers.get("content-type") ?? "";
  const mediaType = contentType.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "multipart/form-data" &&
      mediaType !== "application/x-www-form-urlencoded") return null;

  const declaredHeader = request.headers.get("content-length");
  if (declaredHeader !== null) {
    const declared = Number(declaredHeader);
    if (!Number.isSafeInteger(declared) || declared < 0) return null;
    if (declared > maxBytes) throw new PayloadTooLargeError();
  }
  if (!request.body) return null;

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maxBytes) {
      await reader.cancel();
      throw new PayloadTooLargeError();
    }
    chunks.push(value);
  }
  const body = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
  return new Response(body, { headers: { "content-type": contentType } }).formData();
}
