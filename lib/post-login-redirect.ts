export function safePostLoginPath(value: string | null | undefined, origin: string) {
  if (!value || value.length > 512 || !value.startsWith("/") || value.startsWith("//")) return null;
  try {
    const parsed = new URL(value, origin);
    if (parsed.origin !== new URL(origin).origin) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}
