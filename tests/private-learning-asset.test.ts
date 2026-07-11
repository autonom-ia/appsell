import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, expect, test } from "vitest";

import {
  configuredPrivateLearningAssetRoot,
  prepareVerifiedPrivateLearningAsset
} from "../lib/private-learning-asset.server";

const temporaryPaths: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryPaths.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

test("production requires an explicit absolute private asset root", () => {
  expect(() => configuredPrivateLearningAssetRoot({ NODE_ENV: "production" }, "/workspace"))
    .toThrow("required in production");
  expect(() => configuredPrivateLearningAssetRoot({
    NODE_ENV: "production",
    APPSELL_PRIVATE_LEARNING_ASSET_ROOT: "relative/assets"
  }, "/workspace")).toThrow("must be an absolute path");
  expect(configuredPrivateLearningAssetRoot({ NODE_ENV: "development" }, "/workspace"))
    .toBe("/workspace/output/pdf/codex");
});

test("serves the verified spool even if the authorized source mutates afterward", async () => {
  const fixture = await assetFixture(Buffer.from("%PDF-1.7\noriginal verified bytes\n"));
  const prepared = await prepareVerifiedPrivateLearningAsset(
    fixture.metadata,
    fixture.assetRoot,
    fixture.spoolRoot
  );
  expect(prepared).not.toBeNull();

  await writeFile(fixture.assetPath, Buffer.from("x".repeat(fixture.metadata.byte_size)));
  expect(await prepared!.handle.readFile()).toEqual(fixture.bytes);
  await prepared!.handle.close();
  expect(await readdir(fixture.spoolRoot)).toEqual([]);
});

test("rejects a final symlink and invalid size or hash without leaving spool files", async () => {
  const fixture = await assetFixture(Buffer.from("%PDF-1.7\nprivate bytes\n"));
  const linkedName = "linked.pdf";
  await symlink(fixture.assetPath, join(fixture.assetRoot, linkedName));
  expect(await prepareVerifiedPrivateLearningAsset(
    { ...fixture.metadata, file_name: linkedName },
    fixture.assetRoot,
    fixture.spoolRoot
  )).toBeNull();
  expect(await prepareVerifiedPrivateLearningAsset(
    { ...fixture.metadata, byte_size: fixture.metadata.byte_size + 1 },
    fixture.assetRoot,
    fixture.spoolRoot
  )).toBeNull();
  expect(await prepareVerifiedPrivateLearningAsset(
    { ...fixture.metadata, sha256: "0".repeat(64) },
    fixture.assetRoot,
    fixture.spoolRoot
  )).toBeNull();
  expect(await readdir(fixture.spoolRoot)).toEqual([]);
});

test("closing a cancelled response stream closes its private descriptor", async () => {
  const fixture = await assetFixture(Buffer.alloc(128 * 1024, 7));
  const prepared = await prepareVerifiedPrivateLearningAsset(
    fixture.metadata,
    fixture.assetRoot,
    fixture.spoolRoot
  );
  expect(prepared).not.toBeNull();
  const stream = prepared!.handle.createReadStream({ autoClose: true, start: 0 });
  const closed = new Promise<void>((resolve) => stream.once("close", resolve));
  stream.destroy();
  await closed;
  await expect(prepared!.handle.stat()).rejects.toMatchObject({ code: "EBADF" });
});

async function assetFixture(bytes: Buffer) {
  const root = await mkdtemp(join(tmpdir(), "appsell-asset-test-"));
  temporaryPaths.push(root);
  const assetRoot = join(root, "assets");
  const spoolRoot = join(root, "spool");
  await Promise.all([mkdir(assetRoot), mkdir(spoolRoot)]);
  const file_name = "lesson.pdf";
  const assetPath = join(assetRoot, file_name);
  await writeFile(assetPath, bytes);
  return {
    assetRoot,
    assetPath,
    spoolRoot,
    bytes,
    metadata: {
      file_name,
      byte_size: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex")
    }
  };
}
