import { createHash } from "node:crypto";
import { constants, createWriteStream } from "node:fs";
import { chmod, mkdtemp, open, realpath, rm, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

export interface PrivateLearningAssetMetadata {
  file_name: string;
  byte_size: number;
  sha256: string;
}

interface VerifiedSpool {
  directory: string;
  path: string;
  size: number;
}

interface VerifiedSpoolCacheEntry {
  evicted: boolean;
  openers: number;
  promise: Promise<VerifiedSpool | null>;
  spool?: VerifiedSpool;
  state: "pending" | "ready" | "failed";
  timer?: NodeJS.Timeout;
}

const MAX_VERIFIED_SPOOL_CACHE_ENTRIES = 64;
const MAX_VERIFIED_SPOOL_CACHE_BYTES = 512 * 1024 * 1024;
const VERIFIED_SPOOL_CACHE_IDLE_MS = 5 * 60 * 1000;
const verifiedSpoolCache = new Map<string, VerifiedSpoolCacheEntry>();

export function configuredPrivateLearningAssetRoot(
  environment: NodeJS.ProcessEnv = process.env,
  workingDirectory = process.cwd()
) {
  const configured = environment.APPSELL_PRIVATE_LEARNING_ASSET_ROOT;
  if (!configured) {
    if (environment.NODE_ENV === "production") {
      throw new Error("private learning asset root is required in production");
    }
    return join(/*turbopackIgnore: true*/ workingDirectory, "output", "pdf", "codex");
  }
  if (!isAbsolute(configured) || configured.includes("\0")) {
    throw new Error("private learning asset root must be an absolute path");
  }
  return configured;
}

export async function prepareVerifiedPrivateLearningAsset(
  metadata: PrivateLearningAssetMetadata,
  configuredRoot = configuredPrivateLearningAssetRoot(),
  temporaryRoot?: string
) {
  try {
    const root = await realpath(/*turbopackIgnore: true*/ configuredRoot);
    const candidate = resolve(/*turbopackIgnore: true*/ root, metadata.file_name);
    if (dirname(candidate) !== root) return null;

    if (temporaryRoot !== undefined) {
      const spool = await createVerifiedSpool(candidate, metadata, temporaryRoot);
      return spool ? openDetachedSpool(spool) : null;
    }

    const cacheKey = [root, candidate, metadata.byte_size, metadata.sha256].join("\0");
    return acquireCachedSpool(cacheKey, candidate, metadata);
  } catch {
    return null;
  }
}

// Intended for deterministic test/process teardown. Open response descriptors stay valid
// after their cached path is unlinked, matching normal LRU and idle eviction behavior.
export async function clearPrivateLearningAssetCache() {
  const entries = [...verifiedSpoolCache.entries()];
  for (const [key, entry] of entries) evictCacheEntry(key, entry);
  await Promise.all(entries.map(([, entry]) => entry.promise.then(() => disposeCacheEntry(entry))));
}

async function acquireCachedSpool(
  cacheKey: string,
  candidate: string,
  metadata: PrivateLearningAssetMetadata
) {
  let entry = verifiedSpoolCache.get(cacheKey);
  if (!entry) {
    const created: VerifiedSpoolCacheEntry = {
      evicted: false,
      openers: 0,
      promise: Promise.resolve(null),
      state: "pending"
    };
    created.promise = createVerifiedSpool(candidate, metadata, tmpdir()).then((spool) => {
      if (!spool) {
        created.state = "failed";
        if (verifiedSpoolCache.get(cacheKey) === created) verifiedSpoolCache.delete(cacheKey);
        return null;
      }
      created.spool = spool;
      created.state = "ready";
      if (created.evicted) {
        void disposeCacheEntry(created);
      } else {
        touchCacheEntry(cacheKey, created);
        enforceCacheLimit();
      }
      return spool;
    });
    entry = created;
    verifiedSpoolCache.set(cacheKey, entry);
  } else {
    touchCacheEntry(cacheKey, entry);
  }

  entry.openers += 1;
  try {
    const spool = await entry.promise;
    if (!spool) return null;
    const handle = await open(
      /*turbopackIgnore: true*/ spool.path,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size !== spool.size) {
      await handle.close();
      evictCacheEntry(cacheKey, entry);
      return null;
    }
    return { handle, size: spool.size };
  } catch {
    evictCacheEntry(cacheKey, entry);
    return null;
  } finally {
    entry.openers -= 1;
    if (entry.evicted) await disposeCacheEntry(entry);
  }
}

async function createVerifiedSpool(
  candidate: string,
  metadata: PrivateLearningAssetMetadata,
  temporaryRoot: string
): Promise<VerifiedSpool | null> {
  let sourceHandle: Awaited<ReturnType<typeof open>> | undefined;
  let temporaryDirectory: string | undefined;
  let spoolPath: string | undefined;

  try {
    sourceHandle = await open(
      /*turbopackIgnore: true*/ candidate,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    const sourceStat = await sourceHandle.stat();
    if (!sourceStat.isFile() || sourceStat.size !== metadata.byte_size) return null;

    temporaryDirectory = await mkdtemp(
      /*turbopackIgnore: true*/ join(temporaryRoot, "appsell-learning-asset-")
    );
    await chmod(/*turbopackIgnore: true*/ temporaryDirectory, 0o700);
    spoolPath = join(/*turbopackIgnore: true*/ temporaryDirectory, "verified.bin");
    const hash = createHash("sha256");
    let copiedBytes = 0;
    const verifier = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        copiedBytes += chunk.length;
        hash.update(chunk);
        callback(null, chunk);
      }
    });
    await pipeline(
      sourceHandle.createReadStream({ start: 0, autoClose: true }),
      verifier,
      createWriteStream(/*turbopackIgnore: true*/ spoolPath, {
        flags: "wx",
        mode: 0o600
      })
    );
    sourceHandle = undefined;

    if (copiedBytes !== metadata.byte_size || hash.digest("hex") !== metadata.sha256) {
      return null;
    }

    const verifiedHandle = await open(
      /*turbopackIgnore: true*/ spoolPath,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    const verifiedStat = await verifiedHandle.stat().finally(() => verifiedHandle.close());
    if (!verifiedStat.isFile() || verifiedStat.size !== copiedBytes) return null;

    const result = {
      directory: temporaryDirectory,
      path: spoolPath,
      size: copiedBytes
    };
    temporaryDirectory = undefined;
    spoolPath = undefined;
    return result;
  } catch {
    return null;
  } finally {
    await sourceHandle?.close().catch(() => undefined);
    if (spoolPath) await unlink(/*turbopackIgnore: true*/ spoolPath).catch(() => undefined);
    if (temporaryDirectory) {
      await rm(/*turbopackIgnore: true*/ temporaryDirectory, {
        recursive: true,
        force: true
      }).catch(() => undefined);
    }
  }
}

async function openDetachedSpool(spool: VerifiedSpool) {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      /*turbopackIgnore: true*/ spool.path,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size !== spool.size) return null;
    await disposeSpool(spool);
    const result = { handle, size: spool.size };
    handle = undefined;
    return result;
  } catch {
    return null;
  } finally {
    await handle?.close().catch(() => undefined);
    await disposeSpool(spool);
  }
}

function touchCacheEntry(cacheKey: string, entry: VerifiedSpoolCacheEntry) {
  if (verifiedSpoolCache.get(cacheKey) !== entry || entry.evicted) return;
  verifiedSpoolCache.delete(cacheKey);
  verifiedSpoolCache.set(cacheKey, entry);
  if (entry.state !== "ready") return;
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => evictCacheEntry(cacheKey, entry), VERIFIED_SPOOL_CACHE_IDLE_MS);
  entry.timer.unref?.();
}

function enforceCacheLimit() {
  const ready = [...verifiedSpoolCache.values()].filter(
    (entry): entry is VerifiedSpoolCacheEntry & { spool: VerifiedSpool } =>
      entry.state === "ready" && entry.spool !== undefined
  );
  let readyEntries = ready.length;
  let readyBytes = ready.reduce((total, entry) => total + entry.spool.size, 0);
  if (
    readyEntries <= MAX_VERIFIED_SPOOL_CACHE_ENTRIES &&
    readyBytes <= MAX_VERIFIED_SPOOL_CACHE_BYTES
  ) {
    return;
  }
  for (const [key, entry] of verifiedSpoolCache) {
    if (entry.state !== "ready" || !entry.spool) continue;
    readyBytes -= entry.spool.size;
    evictCacheEntry(key, entry);
    readyEntries -= 1;
    if (
      readyEntries <= MAX_VERIFIED_SPOOL_CACHE_ENTRIES &&
      readyBytes <= MAX_VERIFIED_SPOOL_CACHE_BYTES
    ) {
      break;
    }
  }
}

function evictCacheEntry(cacheKey: string, entry: VerifiedSpoolCacheEntry) {
  if (verifiedSpoolCache.get(cacheKey) === entry) verifiedSpoolCache.delete(cacheKey);
  entry.evicted = true;
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = undefined;
  void disposeCacheEntry(entry);
}

async function disposeCacheEntry(entry: VerifiedSpoolCacheEntry) {
  if (!entry.evicted || entry.openers > 0 || !entry.spool) return;
  const spool = entry.spool;
  entry.spool = undefined;
  await disposeSpool(spool);
}

async function disposeSpool(spool: VerifiedSpool) {
  await unlink(/*turbopackIgnore: true*/ spool.path).catch(() => undefined);
  await rm(/*turbopackIgnore: true*/ spool.directory, { recursive: true, force: true }).catch(
    () => undefined
  );
}
