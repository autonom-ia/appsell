import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

const YIELD_EVERY_BYTES = 4 * 1024 * 1024;

export async function sha256File(
  file: File,
  signal?: AbortSignal,
  onProgress?: (percent: number) => void
) {
  const reader = file.stream().getReader();
  const digest = sha256.create();
  let consumed = 0;
  let yieldedAt = 0;
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("Hash cancelled", "AbortError");
      const { done, value } = await reader.read();
      if (done) break;
      digest.update(value);
      consumed += value.byteLength;
      onProgress?.(Math.min(100, Math.floor((consumed / file.size) * 100)));
      if (consumed - yieldedAt >= YIELD_EVERY_BYTES) {
        yieldedAt = consumed;
        await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
      }
    }
    if (consumed !== file.size) throw new Error("file_size_changed");
    return bytesToHex(digest.digest());
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
