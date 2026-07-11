import { createHash } from "node:crypto";
import { constants, createWriteStream } from "node:fs";
import { chmod, mkdtemp, open, realpath, rm, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

export interface PrivateLearningAssetMetadata {
  file_name: string;
  byte_size: number;
  sha256: string;
}

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
  temporaryRoot = tmpdir()
) {
  let sourceHandle: Awaited<ReturnType<typeof open>> | undefined;
  let verifiedHandle: Awaited<ReturnType<typeof open>> | undefined;
  let temporaryDirectory: string | undefined;
  let spoolPath: string | undefined;

  try {
    const root = await realpath(/*turbopackIgnore: true*/ configuredRoot);
    const candidate = resolve(/*turbopackIgnore: true*/ root, metadata.file_name);
    if (dirname(candidate) !== root) return null;

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

    verifiedHandle = await open(
      /*turbopackIgnore: true*/ spoolPath,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    const verifiedStat = await verifiedHandle.stat();
    if (!verifiedStat.isFile() || verifiedStat.size !== copiedBytes) return null;

    // Unlink before serving: only this private descriptor can address the verified bytes.
    await unlink(/*turbopackIgnore: true*/ spoolPath);
    spoolPath = undefined;
    await rm(/*turbopackIgnore: true*/ temporaryDirectory, { recursive: true, force: true });
    temporaryDirectory = undefined;
    const result = { handle: verifiedHandle, size: copiedBytes };
    verifiedHandle = undefined;
    return result;
  } catch {
    return null;
  } finally {
    await sourceHandle?.close().catch(() => undefined);
    await verifiedHandle?.close().catch(() => undefined);
    if (spoolPath) await unlink(/*turbopackIgnore: true*/ spoolPath).catch(() => undefined);
    if (temporaryDirectory) {
      await rm(/*turbopackIgnore: true*/ temporaryDirectory, {
        recursive: true,
        force: true
      }).catch(() => undefined);
    }
  }
}
