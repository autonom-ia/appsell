import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { closeSync, mkdtempSync, openSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { access, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import Redis from "ioredis";

const sellDirectory = process.cwd();
const worktreeLockPath = path.join(sellDirectory, ".appsell-identity-real.lock");
acquireWorktreeLock(worktreeLockPath);
const runTemporaryDirectory = mkdtempSync(path.join(os.tmpdir(), "appsell-identity-real-"));
process.once("exit", () => {
  releaseWorktreeLock(worktreeLockPath);
  rmSync(runTemporaryDirectory, { recursive: true, force: true });
});
const foundationDirectory = path.resolve(
  process.env.AUTONOMIA_IDENTITY_FOUNDATION_DIR ?? path.join(sellDirectory, "../autonomia-identity-foundation-issue-35")
);
const authDirectory = path.resolve(
  process.env.AUTONOMIA_IDENTITY_AUTH_DIR ?? path.join(sellDirectory, "../autonomia-identity-auth-issue-23-pr21")
);
const ports = await allocatePorts();
const services = [];
const suffix = `${process.pid}-${Date.now()}`;
const sellDistDirectory = `.next-identity-real-${suffix}`;
const demoPassword = `E2E-${randomBytes(24).toString("base64url")}`;
const e2eControlToken = randomBytes(32).toString("base64url");
const domainMigratorPassword = randomBytes(24).toString("hex");
const domainRuntimePassword = randomBytes(24).toString("hex");
const domainContextKey = randomBytes(32);
const domainBootstrapKey = randomBytes(32);
const domainInternalKey = randomBytes(32).toString("base64");
const domainServiceCredential = randomBytes(32).toString("base64");
const domainInternalIssuer = "appsell-bff-identity-e2e";
const domainInternalAudience = "appsell-api-identity-e2e";
const toolEnvironment = {
  ...selectToolEnvironment(process.env),
  UV_CACHE_DIR: path.join(runTemporaryDirectory, "uv-cache")
};
const redisEnvironment = {
  APPSELL_REDIS_URL: required(process.env.APPSELL_REDIS_URL, "APPSELL_REDIS_URL"),
  APPSELL_REDIS_PREFIX: required(process.env.APPSELL_REDIS_PREFIX, "APPSELL_REDIS_PREFIX"),
  APPSELL_SESSION_HMAC_KEY_B64: required(process.env.APPSELL_SESSION_HMAC_KEY_B64, "APPSELL_SESSION_HMAC_KEY_B64"),
  APPSELL_CSRF_HMAC_KEY_B64: required(process.env.APPSELL_CSRF_HMAC_KEY_B64, "APPSELL_CSRF_HMAC_KEY_B64"),
  APPSELL_VAULT_LOCAL_KEYRING_B64_JSON: required(
    process.env.APPSELL_VAULT_LOCAL_KEYRING_B64_JSON,
    "APPSELL_VAULT_LOCAL_KEYRING_B64_JSON"
  )
};
const generatedFiles = ["next-env.d.ts", "tsconfig.json"];
const generatedOriginals = await Promise.all(generatedFiles.map((file) => readFile(path.join(sellDirectory, file))));
const evidenceDirectory = path.join(sellDirectory, "output/playwright/identity-real-evidence");
const stagedEvidenceDirectory = path.join(sellDirectory, "output/playwright", `.identity-real-evidence-${suffix}`);
const evidenceBackupDirectory = path.join(sellDirectory, "output/playwright", `.identity-real-evidence-backup-${suffix}`);
let activeCommand;
let signalExitCode;

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    signalExitCode ??= signal === "SIGINT" ? 130 : 143;
    activeCommand?.kill(signal);
    for (const child of services) child.kill(signal);
  });
}

try {
  assertDisposableRedis();
  await rm(stagedEvidenceDirectory, { recursive: true, force: true });
  await Promise.all([
    requireFile(path.join(foundationDirectory, "services/auth-api/src/local-ci/run-http-adapter.ts")),
    requireFile(path.join(authDirectory, "src/lib/auth/server/provider-session-broker-client.ts")),
    requireFile(path.join(sellDirectory, "scripts/run-real-domain-api-local.sh")),
  ]);
  process.stdout.write(
    `Autonom.ia Sell E2E ports: sell=${ports.sell} auth=${ports.auth} ` +
    `foundation=${ports.foundation} domain=${ports.domain}\n`
  );

  await run("corepack", ["pnpm", "build"], foundationDirectory, toolEnvironment);

  const foundation = start("foundation", process.execPath, [
    "scripts/with-isolated-postgres.mjs",
    "sh",
    "-c",
    'export DATABASE_URL="$TEST_DATABASE_URL"; export IDENTITY_LOCAL_DATABASE_SENTINEL="$IDENTITY_TEST_DATABASE_ISOLATED"; exec corepack pnpm local:http'
  ], foundationDirectory, {
    ...toolEnvironment,
    NODE_ENV: "test",
    APP_ENV: "ci",
    IDENTITY_LOCAL_HTTP_PORT: String(ports.foundation),
    IDENTITY_LOCAL_OIDC_ISSUER: "https://identity.local.test",
    IDENTITY_LOCAL_OAUTH_REDIRECT_URI: `http://localhost:${ports.sell}/auth/callback`,
    OAUTH_PROVIDER_SESSION_CALLER_ID: "identity-auth-local",
    OAUTH_PROVIDER_SESSION_CALLER_ARN: "arn:aws:iam::000000000000:role/identity-auth-local",
    AWS_REGION: "us-east-1",
    AWS_ACCESS_KEY_ID: "AKIDLOCALCI000000000",
    AWS_SECRET_ACCESS_KEY: "local-ci-secret-access-key-01234567890123456789"
  });
  await waitForUrl(`http://127.0.0.1:${ports.foundation}/health`, foundation);

  const domain = start("domain", process.execPath, [
    path.join(foundationDirectory, "scripts/with-isolated-postgres.mjs"),
    path.join(sellDirectory, "scripts/run-real-domain-api-local.sh")
  ], sellDirectory, {
    ...toolEnvironment,
    E2E_DOMAIN_API_PORT: String(ports.domain),
    APPSELL_MIGRATOR_PASSWORD: domainMigratorPassword,
    APPSELL_RUNTIME_PASSWORD: domainRuntimePassword,
    APPSELL_INTERNAL_ISSUER: domainInternalIssuer,
    APPSELL_INTERNAL_AUDIENCE: domainInternalAudience,
    APPSELL_IDENTITY_ISSUER: "https://identity.local.test",
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: JSON.stringify([
      { id: "identity-e2e-v1", key: domainInternalKey, active: true }
    ]),
    APPSELL_API_CONTEXT_HMAC_KEY_B64: domainContextKey.toString("base64"),
    APPSELL_SERVICE_CREDENTIAL_B64: domainServiceCredential,
    APPSELL_CONTEXT_SIGNING_KEY_HEX: domainContextKey.toString("hex"),
    APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX: domainBootstrapKey.toString("hex")
  });
  await waitForUrl(`http://127.0.0.1:${ports.domain}/health/ready`, domain, 120_000);

  const auth = start("auth", "corepack", [
    "pnpm", "exec", "next", "dev", "--hostname", "127.0.0.1", "-p", String(ports.auth)
  ], authDirectory, {
    ...toolEnvironment,
    IDENTITY_COGNITO_PROVIDER: "demo",
    IDENTITY_DEMO_PASSWORD: demoPassword,
    IDENTITY_SESSION_STORE_PROVIDER: "memory",
    IDENTITY_SESSION_PROTECTION_PROVIDER: "local",
    IDENTITY_SESSION_LOCAL_KEY_B64: randomBytes(32).toString("base64"),
    IDENTITY_PUBLIC_ORIGIN: `http://127.0.0.1:${ports.auth}`,
    IDENTITY_API_BASE_URL: `http://127.0.0.1:${ports.foundation}`,
    IDENTITY_PROVIDER_SESSION_BROKER_URL: `http://127.0.0.1:${ports.foundation}`,
    IDENTITY_PROVIDER_SESSION_BROKER_ALLOWED_HOSTS: "127.0.0.1",
    IDENTITY_PROVIDER_SESSION_CALLER_ID: "identity-auth-local",
    AWS_REGION: "us-east-1",
    AWS_ACCESS_KEY_ID: "AKIDLOCALCI000000000",
    AWS_SECRET_ACCESS_KEY: "local-ci-secret-access-key-01234567890123456789",
    NEXT_PUBLIC_USE_MOCKS: "false",
    NEXT_PUBLIC_ENABLE_LEGACY_BROWSER_TOKEN_FLOWS: "false"
  });
  await waitForUrl(`http://127.0.0.1:${ports.auth}/login/`, auth);

  const sell = start("sell", "corepack", [
    "pnpm", "exec", "next", "dev", "--hostname", "127.0.0.1", "-p", String(ports.sell)
  ], sellDirectory, {
    ...toolEnvironment,
    ...redisEnvironment,
    APPSELL_OAUTH_MODE: "identity",
    APPSELL_PUBLIC_ORIGIN: `http://localhost:${ports.sell}`,
    APPSELL_DEMO_ENABLED: "false",
    APPSELL_IDENTITY_DEMO_ENABLED: "true",
    APPSELL_IDENTITY_E2E_CONTROL_TOKEN: e2eControlToken,
    APPSELL_DOMAIN_API_URL: `http://127.0.0.1:${ports.domain}`,
    APPSELL_INTERNAL_ISSUER: domainInternalIssuer,
    APPSELL_INTERNAL_AUDIENCE: domainInternalAudience,
    APPSELL_INTERNAL_HMAC_KEYRING_B64_JSON: JSON.stringify([
      { id: "identity-e2e-v1", key: domainInternalKey, active: true }
    ]),
    APPSELL_SERVICE_CREDENTIAL_B64: domainServiceCredential,
    APPSELL_NEXT_DIST_DIR: sellDistDirectory,
    APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID: "appsell-web",
    APPSELL_IDENTITY_EXPECTED_AUDIENCE: "appsell-web",
    APPSELL_IDENTITY_ISSUER: "https://identity.local.test",
    APPSELL_IDENTITY_AUTHORIZATION_URL: `http://127.0.0.1:${ports.auth}/login/`,
    APPSELL_IDENTITY_TOKEN_URL: `http://127.0.0.1:${ports.foundation}/oauth/token`,
    APPSELL_IDENTITY_JWKS_URL: `http://127.0.0.1:${ports.foundation}/.well-known/jwks.json`,
    APPSELL_IDENTITY_ALLOWED_ORIGINS: [
      "https://identity.local.test",
      `http://127.0.0.1:${ports.auth}`,
      `http://127.0.0.1:${ports.foundation}`
    ].join(","),
    APPSELL_IDENTITY_REDIRECT_URI: `http://localhost:${ports.sell}/auth/callback`,
    APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS: "86400"
  });
  await waitForUrl(`http://localhost:${ports.sell}/`, sell);

  const result = await run("corepack", [
    "pnpm", "exec", "playwright", "test", "--config=playwright.identity-real.config.ts"
  ], sellDirectory, {
    ...toolEnvironment,
    E2E_REAL_IDENTITY_PASSWORD: demoPassword,
    E2E_REAL_CONTROL_TOKEN: e2eControlToken,
    E2E_REAL_EVIDENCE_DIR: stagedEvidenceDirectory,
    E2E_REAL_SELL_ORIGIN: `http://localhost:${ports.sell}`,
    E2E_REAL_AUTH_ORIGIN: `http://127.0.0.1:${ports.auth}`
  }, false);
  await assertRedisContainsNoProviderCredentials();
  if (result === 0) await promoteEvidence();
  process.exitCode = result;
} finally {
  for (const child of services.reverse()) await stop(child);
  await rm(path.join(sellDirectory, sellDistDirectory), { recursive: true, force: true });
  await Promise.all(generatedFiles.map((file, index) =>
    writeFile(path.join(sellDirectory, file), generatedOriginals[index])
  ));
  await assertGeneratedFilesRestored();
  await rm(stagedEvidenceDirectory, { recursive: true, force: true });
  await rm(evidenceBackupDirectory, { recursive: true, force: true });
  await rm(runTemporaryDirectory, { recursive: true, force: true });
  releaseWorktreeLock(worktreeLockPath);
  if (signalExitCode) process.exitCode = signalExitCode;
}

function assertDisposableRedis() {
  const url = new URL(required(process.env.APPSELL_REDIS_URL, "APPSELL_REDIS_URL"));
  if (url.protocol !== "redis:" || url.hostname !== "127.0.0.1" || !url.port || url.pathname !== "/0") {
    throw new Error("Cross-repo E2E requires the wrapper-provided disposable loopback Redis.");
  }
  if (!required(process.env.APPSELL_REDIS_PREFIX, "APPSELL_REDIS_PREFIX").startsWith("appsell:e2e:run-")) {
    throw new Error("Cross-repo E2E Redis prefix is not disposable.");
  }
}

async function requireFile(file) {
  await access(file);
}

function required(value, name) {
  if (!value?.trim()) throw new Error(`${name} is required.`);
  return value;
}

function selectToolEnvironment(source) {
  const allowed = [
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD",
    "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR",
    "COREPACK_HOME", "PNPM_HOME", "NVM_DIR", "VOLTA_HOME",
    "UV_CACHE_DIR", "UV_PYTHON", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "PLAYWRIGHT_BROWSERS_PATH",
    "PYTHONUTF8"
  ];
  const environment = Object.fromEntries(
    allowed.flatMap((key) => source[key] === undefined ? [] : [[key, source[key]]])
  );
  return { ...environment, NEXT_TELEMETRY_DISABLED: "1" };
}

function acquireWorktreeLock(lockPath) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const descriptor = openSync(lockPath, "wx", 0o600);
      writeFileSync(descriptor, `${process.pid}\n`, "utf8");
      closeSync(descriptor);
      return;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      const owner = Number(readFileSync(lockPath, "utf8").trim());
      if (Number.isSafeInteger(owner) && processExists(owner)) {
        throw new Error(`Cross-repo E2E is already running in this worktree (pid ${owner}).`);
      }
      unlinkSync(lockPath);
    }
  }
  throw new Error("Could not acquire the cross-repo E2E worktree lock.");
}

function releaseWorktreeLock(lockPath) {
  try {
    if (Number(readFileSync(lockPath, "utf8").trim()) === process.pid) unlinkSync(lockPath);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function processExists(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

async function assertGeneratedFilesRestored() {
  const restored = await Promise.all(generatedFiles.map((file) => readFile(path.join(sellDirectory, file))));
  if (restored.some((content, index) => !content.equals(generatedOriginals[index]))) {
    throw new Error("Cross-repo E2E did not restore Next.js generated configuration files.");
  }
}

async function assertRedisContainsNoProviderCredentials() {
  const redis = new Redis(redisEnvironment.APPSELL_REDIS_URL, {
    lazyConnect: true,
    maxRetriesPerRequest: 1,
    retryStrategy: () => null
  });
  const pattern = /demo-(?:access|id|refresh)-[A-Za-z0-9_-]{20,}/i;
  let cursor = "0";
  let inspected = 0;
  try {
    await redis.connect();
    do {
      const [nextCursor, keys] = await redis.scan(
        cursor,
        "MATCH",
        `${redisEnvironment.APPSELL_REDIS_PREFIX}*`,
        "COUNT",
        100
      );
      cursor = nextCursor;
      for (const key of keys) {
        const type = await redis.type(key);
        if (type !== "string") {
          throw new Error(`Disposable Redis key ${key} has unexpected type ${type}.`);
        }
        const value = await redis.get(key);
        if (value === null) continue;
        if (pattern.test(key) || pattern.test(value)) {
          throw new Error(`Provider credential leaked into disposable Redis key ${key}.`);
        }
        inspected += 1;
      }
    } while (cursor !== "0");
  } finally {
    redis.disconnect();
  }
  if (inspected < 2) throw new Error("Disposable Redis evidence was unexpectedly empty.");
  process.stdout.write(`Redis provider-credential scan: ${inspected} keys clean\n`);
}

async function promoteEvidence() {
  await access(stagedEvidenceDirectory);
  await mkdir(path.dirname(evidenceDirectory), { recursive: true });
  let previousMoved = false;
  try {
    await rename(evidenceDirectory, evidenceBackupDirectory);
    previousMoved = true;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  try {
    await rename(stagedEvidenceDirectory, evidenceDirectory);
  } catch (error) {
    if (previousMoved) await rename(evidenceBackupDirectory, evidenceDirectory).catch(() => undefined);
    throw error;
  }
  if (previousMoved) await rm(evidenceBackupDirectory, { recursive: true, force: true });
}

async function allocatePorts() {
  const reserved = new Set();
  const sell = await preferredOrUnusedPort("E2E_REAL_SELL_PORT", 3_200, reserved);
  reserved.add(sell);
  const auth = await preferredOrUnusedPort("E2E_REAL_AUTH_PORT", 3_217, reserved);
  reserved.add(auth);
  const foundation = await preferredOrUnusedPort("E2E_REAL_FOUNDATION_PORT", 3_218, reserved);
  reserved.add(foundation);
  const domain = await preferredOrUnusedPort("E2E_REAL_DOMAIN_PORT", 8_100, reserved);
  return { sell, auth, foundation, domain };
}

async function preferredOrUnusedPort(environmentName, preferred, reserved) {
  const explicit = process.env[environmentName];
  if (explicit) {
    const port = Number(explicit);
    if (!Number.isSafeInteger(port) || port < 1_024 || port > 65_535 || reserved.has(port) || !await portIsFree(port)) {
      throw new Error(`${environmentName} must be one unique free non-privileged loopback port.`);
    }
    return port;
  }
  if (!reserved.has(preferred) && await portIsFree(preferred)) return preferred;
  while (true) {
    const port = await unusedPort();
    if (!reserved.has(port)) return port;
  }
}

function portIsFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Could not allocate a loopback port.")));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function start(label, command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  child.label = label;
  child.diagnostics = "";
  for (const stream of [child.stdout, child.stderr]) {
    stream?.on("data", (chunk) => {
      const text = String(chunk);
      child.diagnostics = `${child.diagnostics}${text}`.slice(-8_000);
      process.stderr.write(`[${label}] ${text}`);
    });
  }
  services.push(child);
  return child;
}

async function waitForUrl(url, child) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`${child.label} exited early.\n${child.diagnostics}`);
    try {
      const response = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(1_000) });
      if (response.status >= 200 && response.status < 500) return;
    } catch {
      // Process is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${child.label} did not become ready.\n${child.diagnostics}`);
}

function run(command, args, cwd, env, failOnError = true) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: "inherit" });
    activeCommand = child;
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (activeCommand === child) activeCommand = undefined;
      const result = code ?? (signal ? 1 : 0);
      if (failOnError && result !== 0) reject(new Error(`${command} exited with ${result}.`));
      else resolve(result);
    });
  });
}

async function stop(child) {
  if (child.exitCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 8_000))]);
  if (child.exitCode === null) {
    child.kill("SIGKILL");
    await exited;
  }
}
