export type ByteRange = { start: number; end: number };

export function parseSingleByteRange(value: string | null, size: number): ByteRange | null | "unsatisfiable" {
  if (value === null) return null;
  if (!Number.isSafeInteger(size) || size < 1) return "unsatisfiable";
  const separator = value.indexOf("=");
  if (separator < 0 || value.slice(0, separator).toLowerCase() !== "bytes") return null;
  const match = /^(\d*)-(\d*)$/.exec(value.slice(separator + 1));
  if (!match || (match[1] === "" && match[2] === "")) return "unsatisfiable";

  if (match[1] === "") {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return "unsatisfiable";
    return { start: Math.max(0, size - suffix), end: size - 1 };
  }

  const start = Number(match[1]);
  const requestedEnd = match[2] === "" ? size - 1 : Number(match[2]);
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(requestedEnd) || start >= size || requestedEnd < start) {
    return "unsatisfiable";
  }
  return { start, end: Math.min(requestedEnd, size - 1) };
}
