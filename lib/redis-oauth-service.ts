import Redis from "ioredis";

import { hmacSha256, pkceChallenge, randomOpaque, sha256 } from "./crypto";
import { OAuthFlowError } from "./oauth-service";
import {
  createLocalTokenVaultFromEnvironment,
  TokenVaultConfigurationError,
  TokenVaultError,
  type TokenBundle,
  type TokenVault,
  type TokenVaultContext
} from "./token-vault";

const DEFAULT_TRANSACTION_TTL_MS = 5 * 60_000;
const DEFAULT_CODE_TTL_MS = 60_000;
const DEFAULT_SESSION_TTL_MS = 60 * 60_000;
const DEFAULT_RATE_WINDOW_MS = 60_000;
const DEFAULT_MAX_PER_CLIENT = 120;
const DEFAULT_MAX_GLOBAL = 1_000;
const DEFAULT_RECEIPT_TTL_MS = 30_000;
const PREFIX_PATTERN = /^appsell:[a-z0-9][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9_-]{5,63}$/;

interface LoginTransaction {
  state: string;
  nonce: string;
  challenge: string;
}

interface AuthorizationCode {
  state: string;
  nonce: string;
  challenge: string;
  subject: string;
}

interface SessionRecord {
  subject: string;
  createdAt: number;
  expiresAt: number;
}

export interface RedisOAuthOptions {
  redisUrl: string;
  prefix: string;
  sessionHmacKey: Buffer;
  tokenVault: TokenVault;
  transactionTtlMs?: number;
  codeTtlMs?: number;
  sessionTtlMs?: number;
  rateWindowMs?: number;
  maxPerClient?: number;
  maxGlobal?: number;
  connectTimeoutMs?: number;
  receiptTtlMs?: number;
}

const RATE_LIMIT_SCRIPT = `
local global_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local client_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if global_count >= tonumber(ARGV[1]) or client_count >= tonumber(ARGV[2]) then
  return 0
end
global_count = redis.call('INCR', KEYS[1])
client_count = redis.call('INCR', KEYS[2])
if global_count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[3]) end
if client_count == 1 then redis.call('PEXPIRE', KEYS[2], ARGV[3]) end
return 1
`;

const CALLBACK_SCRIPT = `
local transaction_json = redis.call('GET', KEYS[1])
if not transaction_json then
  local receipt = redis.call('GET', KEYS[4])
  if receipt and receipt == ARGV[4] then
    local session_exists = redis.call('EXISTS', KEYS[3])
    local vault_exists = redis.call('EXISTS', KEYS[5])
    if session_exists == 1 and vault_exists == 1 then return 'RETRY_OK' end
    if session_exists ~= vault_exists then return 'PAIR_INTEGRITY_ERROR' end
  end
  return 'TRANSACTION_MISSING'
end
local transaction = cjson.decode(transaction_json)
if transaction.state ~= ARGV[1] then return 'INVALID_STATE' end

local code_json = redis.call('GET', KEYS[2])
if not code_json then return 'CODE_MISSING' end
local code = cjson.decode(code_json)
if code.state ~= transaction.state then return 'CODE_STATE_MISMATCH' end
if code.nonce ~= transaction.nonce then return 'NONCE_MISMATCH' end
if code.challenge ~= transaction.challenge then return 'PKCE_MISMATCH' end
local session = cjson.decode(ARGV[2])
if code.subject ~= session.subject then return 'SUBJECT_MISMATCH' end

if redis.call('EXISTS', KEYS[3]) == 1 or redis.call('EXISTS', KEYS[5]) == 1 then return 'PAIR_COLLISION' end
redis.call('PSETEX', KEYS[3], ARGV[3], ARGV[2])
redis.call('PSETEX', KEYS[5], ARGV[3], ARGV[6])
redis.call('PSETEX', KEYS[4], ARGV[5], ARGV[4])
redis.call('DEL', KEYS[1], KEYS[2])
return 'OK'
`;

const READ_SESSION_VAULT_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return {} end
if not session_json or not vault_json then return {'PAIR_INTEGRITY_ERROR'} end
return {session_json, vault_json}
`;

export class OAuthStoreConfigurationError extends OAuthFlowError {}
export class OAuthStoreUnavailableError extends OAuthFlowError {}
export class OAuthVaultConfigurationError extends OAuthFlowError {}
export class OAuthVaultIntegrityError extends OAuthFlowError {}

export class RedisOAuthService {
  private readonly redis: Redis;
  private readonly prefix: string;
  private readonly transactionTtlMs: number;
  private readonly codeTtlMs: number;
  private readonly sessionTtlMs: number;
  private readonly rateWindowMs: number;
  private readonly maxPerClient: number;
  private readonly maxGlobal: number;
  private readonly sessionHmacKey: Buffer;
  private readonly tokenVault: TokenVault;
  private readonly receiptTtlMs: number;
  private connectPromise: Promise<void> | undefined;

  constructor(options: RedisOAuthOptions) {
    validateConfiguration(options);
    this.prefix = options.prefix;
    this.transactionTtlMs = options.transactionTtlMs ?? DEFAULT_TRANSACTION_TTL_MS;
    this.codeTtlMs = options.codeTtlMs ?? DEFAULT_CODE_TTL_MS;
    this.sessionTtlMs = options.sessionTtlMs ?? DEFAULT_SESSION_TTL_MS;
    this.rateWindowMs = options.rateWindowMs ?? DEFAULT_RATE_WINDOW_MS;
    this.maxPerClient = options.maxPerClient ?? DEFAULT_MAX_PER_CLIENT;
    this.maxGlobal = options.maxGlobal ?? DEFAULT_MAX_GLOBAL;
    this.sessionHmacKey = Buffer.from(options.sessionHmacKey);
    this.tokenVault = options.tokenVault;
    this.receiptTtlMs = options.receiptTtlMs ?? DEFAULT_RECEIPT_TTL_MS;
    this.redis = new Redis(options.redisUrl, {
      lazyConnect: true,
      enableOfflineQueue: false,
      maxRetriesPerRequest: 1,
      connectTimeout: options.connectTimeoutMs ?? 1_000,
      retryStrategy: () => null
    });
    this.redis.on("error", () => {
      // Commands translate transport errors into a non-sensitive public error.
    });
  }

  static fromEnvironment(environment: Partial<Record<string, string | undefined>> = process.env) {
    const sessionHmacKey = parseSessionHmacKey(environment.APPSELL_SESSION_HMAC_KEY_B64 ?? "");
    let tokenVault: TokenVault;
    try {
      tokenVault = createLocalTokenVaultFromEnvironment(environment, [sessionHmacKey]);
    } catch (error) {
      if (error instanceof TokenVaultConfigurationError) {
        throw new OAuthVaultConfigurationError("Token vault configuration is unavailable.");
      }
      throw error;
    }
    return new RedisOAuthService({
      redisUrl: environment.APPSELL_REDIS_URL ?? "",
      prefix: environment.APPSELL_REDIS_PREFIX ?? "",
      sessionHmacKey,
      tokenVault
    });
  }

  async beginLogin(clientKey = "local") {
    return this.withRedis(async () => {
      await this.assertAllowed("login", clientKey);
      const transactionId = randomOpaque();
      const verifier = randomOpaque(48);
      const transaction: LoginTransaction = {
        state: randomOpaque(),
        nonce: randomOpaque(),
        challenge: pkceChallenge(verifier)
      };
      await this.setUnique(this.key("tx", transactionId), transaction, this.transactionTtlMs);
      return {
        transactionId,
        state: transaction.state,
        nonce: transaction.nonce,
        codeChallenge: transaction.challenge
      };
    });
  }

  async authorize(input: { state: string; nonce: string; codeChallenge: string }, clientKey = "local") {
    if (!input.state || !input.nonce || !input.codeChallenge) {
      throw new OAuthFlowError("Invalid authorization request.");
    }
    return this.withRedis(async () => {
      await this.assertAllowed("authorize", clientKey);
      const code = randomOpaque();
      const record: AuthorizationCode = {
        state: input.state,
        nonce: input.nonce,
        challenge: input.codeChallenge,
        subject: "dev-user-001"
      };
      await this.setUnique(this.key("code", code), record, this.codeTtlMs);
      return code;
    });
  }

  async callback(input: { transactionId?: string; code?: string; state?: string }) {
    if (!input.transactionId || !input.code || !input.state) {
      throw new OAuthFlowError("Missing callback parameters.");
    }
    const transactionId = input.transactionId;
    const code = input.code;
    const state = input.state;
    return this.withRedis(async () => {
      const sessionId = hmacSha256(this.sessionHmacKey, transactionId);
      const callbackFingerprint = sha256(`${code}\0${state}`);
      const now = Date.now();
      const subject = "dev-user-001";
      const session: SessionRecord = {
        subject,
        createdAt: now,
        expiresAt: now + this.sessionTtlMs
      };
      const tokenBundle: TokenBundle = {
        subject,
        tokenType: "Bearer",
        accessToken: randomOpaque(32),
        refreshToken: randomOpaque(48),
        accessTokenExpiresAt: now + Math.max(1, Math.floor(this.sessionTtlMs / 2)),
        refreshTokenExpiresAt: now + Math.max(2, this.sessionTtlMs),
        scopes: ["openid", "profile"],
        revision: 1
      };
      const vaultEnvelope = await this.tokenVault.seal(tokenBundle, this.vaultContext(sessionId, subject));
      const result = await this.redis.eval(
        CALLBACK_SCRIPT,
        5,
        this.key("tx", transactionId),
        this.key("code", code),
        this.key("session", sessionId),
        this.key("receipt", transactionId),
        this.key("vault", sessionId),
        state,
        JSON.stringify(session),
        String(this.sessionTtlMs),
        callbackFingerprint,
        String(Math.min(this.receiptTtlMs, this.sessionTtlMs)),
        vaultEnvelope
      );
      switch (result) {
        case "OK": return sessionId;
        case "RETRY_OK": {
          const pair = await this.readSessionVaultPair(sessionId);
          if (!pair) throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
          const existingSession = parseSessionRecord(pair.session);
          await this.tokenVault.open(pair.vault, this.vaultContext(sessionId, existingSession.subject));
          return sessionId;
        }
        case "TRANSACTION_MISSING": throw new OAuthFlowError("Login transaction expired.");
        case "INVALID_STATE": throw new OAuthFlowError("Invalid OAuth state.");
        case "CODE_MISSING": throw new OAuthFlowError("Authorization code expired or replayed.");
        case "CODE_STATE_MISMATCH": throw new OAuthFlowError("Authorization code state mismatch.");
        case "NONCE_MISMATCH": throw new OAuthFlowError("Authorization nonce mismatch.");
        case "PKCE_MISMATCH": throw new OAuthFlowError("PKCE validation failed.");
        case "SUBJECT_MISMATCH": throw new OAuthFlowError("Authorization subject mismatch.");
        case "PAIR_INTEGRITY_ERROR": throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
        case "PAIR_COLLISION": throw new OAuthFlowError("Could not create session vault.");
        default: throw new OAuthFlowError("Could not create session.");
      }
    });
  }

  async getSession(sessionId?: string) {
    if (!sessionId) return null;
    return this.withRedis(async () => {
      const pair = await this.readSessionVaultPair(sessionId);
      if (!pair) return null;
      const session = parseSessionRecord(pair.session);
      await this.tokenVault.open(pair.vault, this.vaultContext(sessionId, session.subject));
      return { subject: session.subject, createdAt: session.createdAt, expiresAt: session.expiresAt };
    });
  }

  async getServerTokenBundle(sessionId?: string) {
    if (!sessionId) return null;
    return this.withRedis(async () => {
      const pair = await this.readSessionVaultPair(sessionId);
      if (!pair) return null;
      const session = parseSessionRecord(pair.session);
      return this.tokenVault.open(pair.vault, this.vaultContext(sessionId, session.subject));
    });
  }

  async logout(sessionId?: string) {
    if (!sessionId) return;
    await this.withRedis(async () => {
      await this.redis.del(this.key("session", sessionId), this.key("vault", sessionId));
    });
  }

  async close() {
    if (this.redis.status === "ready") await this.redis.quit();
    else this.redis.disconnect();
  }

  private key(kind: string, opaqueId: string) {
    return `${this.scope()}:${kind}:${sha256(opaqueId)}`;
  }

  private scope() {
    return `${this.prefix}:{${this.prefix}}`;
  }

  private vaultContext(sessionId: string, subject: string): TokenVaultContext {
    return { namespace: this.prefix, sessionHash: sha256(sessionId), subject };
  }

  private async assertAllowed(action: string, clientKey: string) {
    const window = Math.floor(Date.now() / this.rateWindowMs);
    const result = await this.redis.eval(
      RATE_LIMIT_SCRIPT,
      2,
      `${this.scope()}:rate:${action}:global:${window}`,
      `${this.scope()}:rate:${action}:client:${sha256(clientKey)}:${window}`,
      String(this.maxGlobal),
      String(this.maxPerClient),
      String(this.rateWindowMs + 1_000)
    );
    if (result !== 1) throw new OAuthFlowError("Too many OAuth requests.");
  }

  private async setUnique(key: string, value: object, ttlMs: number) {
    const result = await this.redis.set(key, JSON.stringify(value), "PX", ttlMs, "NX");
    if (result !== "OK") throw new OAuthFlowError("Opaque identifier collision.");
  }

  private async ensureConnected() {
    if (this.redis.status === "ready") return;
    this.connectPromise ??= this.redis.connect().finally(() => {
      this.connectPromise = undefined;
    });
    await this.connectPromise;
  }

  private async readSessionVaultPair(sessionId: string) {
    const result = await this.redis.eval(
      READ_SESSION_VAULT_SCRIPT,
      2,
      this.key("session", sessionId),
      this.key("vault", sessionId)
    );
    if (!Array.isArray(result) || result.length === 0) return null;
    if (result.length === 1 && result[0] === "PAIR_INTEGRITY_ERROR") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    if (result.length !== 2 || typeof result[0] !== "string" || typeof result[1] !== "string") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    return { session: result[0], vault: result[1] };
  }

  private async withRedis<T>(operation: () => Promise<T>) {
    try {
      await this.ensureConnected();
      return await operation();
    } catch (error) {
      if (error instanceof OAuthFlowError) throw error;
      if (error instanceof TokenVaultConfigurationError) {
        throw new OAuthVaultConfigurationError("Token vault configuration is unavailable.");
      }
      if (error instanceof TokenVaultError) {
        throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
      }
      throw new OAuthStoreUnavailableError("OAuth store unavailable.");
    }
  }
}

export function isOAuthStoreUnavailable(error: unknown) {
  return error instanceof OAuthStoreUnavailableError ||
    error instanceof OAuthStoreConfigurationError ||
    error instanceof OAuthVaultConfigurationError ||
    error instanceof OAuthVaultIntegrityError;
}

function validateConfiguration(options: RedisOAuthOptions) {
  let url: URL;
  try {
    url = new URL(options.redisUrl);
  } catch {
    throw new OAuthStoreConfigurationError("APPSELL_REDIS_URL is required and must be a Redis URL.");
  }
  if (url.protocol !== "redis:" && url.protocol !== "rediss:") {
    throw new OAuthStoreConfigurationError("APPSELL_REDIS_URL must use redis:// or rediss://.");
  }
  if (!PREFIX_PATTERN.test(options.prefix)) {
    throw new OAuthStoreConfigurationError(
      "APPSELL_REDIS_PREFIX must be unique and match appsell:<environment>:<instance>."
    );
  }
  for (const [label, value] of [
    ["transaction TTL", options.transactionTtlMs ?? DEFAULT_TRANSACTION_TTL_MS],
    ["code TTL", options.codeTtlMs ?? DEFAULT_CODE_TTL_MS],
    ["session TTL", options.sessionTtlMs ?? DEFAULT_SESSION_TTL_MS],
    ["rate window", options.rateWindowMs ?? DEFAULT_RATE_WINDOW_MS],
    ["per-client limit", options.maxPerClient ?? DEFAULT_MAX_PER_CLIENT],
    ["global limit", options.maxGlobal ?? DEFAULT_MAX_GLOBAL],
    ["receipt TTL", options.receiptTtlMs ?? DEFAULT_RECEIPT_TTL_MS]
  ] as const) {
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new OAuthStoreConfigurationError(`${label} must be a positive integer.`);
    }
  }
  if (!Buffer.isBuffer(options.sessionHmacKey) || options.sessionHmacKey.length !== 32) {
    throw new OAuthStoreConfigurationError("APPSELL_SESSION_HMAC_KEY_B64 must decode to exactly 32 bytes.");
  }
  if (!options.tokenVault || typeof options.tokenVault.seal !== "function" || typeof options.tokenVault.open !== "function") {
    throw new OAuthVaultConfigurationError("A server-side token vault is required.");
  }
  try {
    options.tokenVault.assertKeySeparation([options.sessionHmacKey]);
  } catch (error) {
    if (error instanceof TokenVaultConfigurationError) {
      throw new OAuthVaultConfigurationError("Token vault key separation is invalid.");
    }
    throw error;
  }
}

function parseSessionRecord(value: string): SessionRecord {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
  }
  const record = parsed as Record<string, unknown>;
  if (
    Object.keys(record).sort().join(",") !== "createdAt,expiresAt,subject" ||
    typeof record.subject !== "string" ||
    record.subject.length === 0 ||
    !Number.isSafeInteger(record.createdAt) ||
    !Number.isSafeInteger(record.expiresAt) ||
    (record.expiresAt as number) <= (record.createdAt as number)
  ) {
    throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
  }
  return record as unknown as SessionRecord;
}

function parseSessionHmacKey(encoded: string) {
  const decoded = Buffer.from(encoded, "base64");
  if (decoded.length !== 32 || decoded.toString("base64") !== encoded) {
    throw new OAuthStoreConfigurationError("APPSELL_SESSION_HMAC_KEY_B64 must be canonical base64 for exactly 32 bytes.");
  }
  return decoded;
}
