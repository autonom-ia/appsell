import Redis from "ioredis";

import { hmacSha256, pkceChallenge, randomOpaque, sha256 } from "./crypto";
import { OAuthFlowError } from "./oauth-service";
import {
  OAuthRefreshProviderProtocolError,
  parseRefreshProviderResult,
  type OAuthTokenRefresher
} from "./oauth-refresh";
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
const DEFAULT_REFRESH_LEASE_TTL_MS = 2_000;
const DEFAULT_REFRESH_PROVIDER_TIMEOUT_MS = 1_000;
const DEFAULT_REFRESH_WAIT_TIMEOUT_MS = 5_000;
const DEFAULT_REFRESH_POLL_INTERVAL_MS = 20;
const MIN_REFRESH_COMMIT_MARGIN_MS = 50;
const MAX_REDIS_FENCE = "9223372036854775807";
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
  tokenRevision: number;
}

interface RefreshLease {
  owner: string;
  fence: string;
  operationId: string;
  leaseValue: string;
  intentValue: string;
}

type RefreshLeaseStatus =
  | "ADVANCED"
  | "AMBIGUOUS"
  | "BUSY"
  | "MISSING"
  | "PAIR_INTEGRITY_ERROR"
  | "REVISION_INTEGRITY_ERROR"
  | "INTENT_INTEGRITY_ERROR"
  | "REVISION_MISMATCH"
  | "TTL_ERROR"
  | "FENCE_ERROR"
  | "COORDINATION_ERROR";

type RefreshLeaseAcquisition =
  | { status: RefreshLeaseStatus }
  | { status: "ACQUIRED"; lease: RefreshLease };

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
  refreshLeaseTtlMs?: number;
  refreshProviderTimeoutMs?: number;
  refreshWaitTimeoutMs?: number;
  refreshPollIntervalMs?: number;
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
local session_ttl = redis.call('PTTL', KEYS[1])
local vault_ttl = redis.call('PTTL', KEYS[2])
if session_ttl <= 0 or vault_ttl <= 0 then return {'PAIR_TTL_ERROR'} end
return {session_json, vault_json, tostring(session_ttl), tostring(vault_ttl)}
`;

const ACQUIRE_REFRESH_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return {'MISSING'} end
if not session_json or not vault_json then return {'PAIR_INTEGRITY_ERROR'} end

local session = cjson.decode(session_json)
local current_revision = tonumber(session.tokenRevision)
local expected_revision = tonumber(ARGV[1])
if not current_revision or not expected_revision then return {'REVISION_INTEGRITY_ERROR'} end
if current_revision > expected_revision then return {'ADVANCED'} end
if current_revision ~= expected_revision then return {'REVISION_MISMATCH'} end

local session_ttl = redis.call('PTTL', KEYS[1])
local vault_ttl = redis.call('PTTL', KEYS[2])
if session_ttl <= 0 or vault_ttl <= 0 then return {'TTL_ERROR'} end
local pair_ttl = math.min(session_ttl, vault_ttl)

local operation_id = ARGV[4]
local intent = redis.call('GET', KEYS[5])
if intent then
  local intent_revision, existing_operation, intent_state = string.match(intent, '^(%d+):([A-Za-z0-9_-]+):([a-z]+)$')
  if not intent_revision or intent_revision ~= ARGV[1] then return {'INTENT_INTEGRITY_ERROR'} end
  if intent_state == 'ambiguous' then return {'AMBIGUOUS'} end
  if intent_state == 'inflight' then
    if redis.call('EXISTS', KEYS[3]) == 1 then return {'BUSY'} end
    local ambiguous_intent = ARGV[1] .. ':' .. existing_operation .. ':ambiguous'
    redis.call('PSETEX', KEYS[5], pair_ttl, ambiguous_intent)
    return {'AMBIGUOUS'}
  end
  if intent_state ~= 'prepared' and intent_state ~= 'retryable' then return {'INTENT_INTEGRITY_ERROR'} end
  operation_id = existing_operation
else
  intent = ARGV[1] .. ':' .. operation_id .. ':prepared'
  redis.call('PSETEX', KEYS[5], pair_ttl, intent)
end

if redis.call('EXISTS', KEYS[3]) == 1 then return {'BUSY'} end
redis.call('INCR', KEYS[4])
local fence = redis.call('GET', KEYS[4])
if not fence then return {'FENCE_ERROR'} end
local lease_value = ARGV[2] .. ':' .. fence
local lease_ttl = math.min(pair_ttl, tonumber(ARGV[3]))
local acquired = redis.call('SET', KEYS[3], lease_value, 'PX', lease_ttl, 'NX')
if not acquired then return {'BUSY'} end
redis.call('PEXPIRE', KEYS[4], pair_ttl)
return {'ACQUIRED', fence, operation_id}
`;

const BEGIN_REFRESH_PROVIDER_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return 'MISSING' end
if not session_json or not vault_json then return 'PAIR_INTEGRITY_ERROR' end
local session = cjson.decode(session_json)
if tonumber(session.tokenRevision) ~= tonumber(ARGV[2]) then return 'STALE_REVISION' end
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 'STALE_FENCE' end
if redis.call('PTTL', KEYS[3]) <= tonumber(ARGV[5]) then return 'STALE_FENCE' end
local intent = redis.call('GET', KEYS[4])
local intent_revision, operation_id, intent_state = string.match(intent or '', '^(%d+):([A-Za-z0-9_-]+):([a-z]+)$')
if intent_revision ~= ARGV[2] or operation_id ~= ARGV[3] then return 'INTENT_INTEGRITY_ERROR' end
if intent_state == 'inflight' and intent == ARGV[4] then return 'STARTED' end
if intent_state ~= 'prepared' and intent_state ~= 'retryable' then return 'INTENT_INTEGRITY_ERROR' end
local ttl = math.min(redis.call('PTTL', KEYS[1]), redis.call('PTTL', KEYS[2]))
if ttl <= 0 then return 'TTL_ERROR' end
redis.call('PSETEX', KEYS[4], ttl, ARGV[4])
return 'STARTED'
`;

const COMMIT_REFRESH_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return 'MISSING' end
if not session_json or not vault_json then return 'PAIR_INTEGRITY_ERROR' end
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 'STALE_FENCE' end
if redis.call('GET', KEYS[4]) ~= ARGV[2] then return 'INTENT_INTEGRITY_ERROR' end

local session = cjson.decode(session_json)
local expected_revision = tonumber(ARGV[3])
if tonumber(session.tokenRevision) ~= expected_revision then return 'STALE_REVISION' end
local next_session = cjson.decode(ARGV[4])
if tonumber(next_session.tokenRevision) ~= expected_revision + 1 then return 'REVISION_INTEGRITY_ERROR' end
if next_session.subject ~= session.subject or next_session.createdAt ~= session.createdAt or next_session.expiresAt ~= session.expiresAt then
  return 'SESSION_INTEGRITY_ERROR'
end

local session_ttl = redis.call('PTTL', KEYS[1])
local vault_ttl = redis.call('PTTL', KEYS[2])
local now_ms = tonumber(ARGV[6])
local refresh_expires_at = tonumber(ARGV[7])
if session_ttl <= 0 or vault_ttl <= 0 or not now_ms or not refresh_expires_at then return 'TTL_ERROR' end
local absolute_ttl = tonumber(session.expiresAt) - now_ms
local refresh_ttl = refresh_expires_at - now_ms
local final_ttl = math.floor(math.min(session_ttl, vault_ttl, absolute_ttl, refresh_ttl))
if final_ttl <= 0 then return 'TTL_ERROR' end

redis.call('PSETEX', KEYS[1], final_ttl, ARGV[4])
redis.call('PSETEX', KEYS[2], final_ttl, ARGV[5])
redis.call('DEL', KEYS[3], KEYS[4])
redis.call('PEXPIRE', KEYS[5], final_ttl)
return 'OK'
`;

const RELEASE_REFRESH_SCRIPT = `
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
`;

const MARK_REFRESH_RETRYABLE_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return 'MISSING' end
if not session_json or not vault_json then return 'PAIR_INTEGRITY_ERROR' end
local session = cjson.decode(session_json)
if tonumber(session.tokenRevision) ~= tonumber(ARGV[3]) then return 'STALE_REVISION' end
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 'STALE_FENCE' end
if redis.call('GET', KEYS[4]) ~= ARGV[2] then return 'INTENT_INTEGRITY_ERROR' end
local ttl = math.min(redis.call('PTTL', KEYS[1]), redis.call('PTTL', KEYS[2]))
if ttl <= 0 then return 'TTL_ERROR' end
redis.call('PSETEX', KEYS[4], ttl, ARGV[4])
redis.call('DEL', KEYS[3])
return 'RETRYABLE'
`;

const MARK_REFRESH_AMBIGUOUS_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return 'MISSING' end
if not session_json or not vault_json then return 'PAIR_INTEGRITY_ERROR' end
local session = cjson.decode(session_json)
if tonumber(session.tokenRevision) ~= tonumber(ARGV[3]) then return 'STALE_REVISION' end
local intent = redis.call('GET', KEYS[4])
if intent == ARGV[4] then return 'MARKED' end
if intent ~= ARGV[2] then return 'INTENT_INTEGRITY_ERROR' end
local ttl = math.min(redis.call('PTTL', KEYS[1]), redis.call('PTTL', KEYS[2]))
if ttl <= 0 then return 'TTL_ERROR' end
redis.call('PSETEX', KEYS[4], ttl, ARGV[4])
if redis.call('GET', KEYS[3]) == ARGV[1] then redis.call('DEL', KEYS[3]) end
return 'MARKED'
`;

const REVOKE_REFRESH_SCRIPT = `
local session_json = redis.call('GET', KEYS[1])
local vault_json = redis.call('GET', KEYS[2])
if not session_json and not vault_json then return 'MISSING' end
if not session_json or not vault_json then return 'PAIR_INTEGRITY_ERROR' end
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 'STALE_FENCE' end
if redis.call('GET', KEYS[4]) ~= ARGV[2] then return 'INTENT_INTEGRITY_ERROR' end
local session = cjson.decode(session_json)
if tonumber(session.tokenRevision) ~= tonumber(ARGV[3]) then return 'STALE_REVISION' end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
return 'REVOKED'
`;

export class OAuthStoreConfigurationError extends OAuthFlowError {}
export class OAuthStoreUnavailableError extends OAuthFlowError {}
export class OAuthVaultConfigurationError extends OAuthFlowError {}
export class OAuthVaultIntegrityError extends OAuthFlowError {}
export class OAuthRefreshUnavailableError extends OAuthFlowError {}
export class OAuthRefreshAmbiguousError extends OAuthFlowError {}
export class OAuthRefreshInvalidGrantError extends OAuthFlowError {}
export class OAuthRefreshRevisionError extends OAuthFlowError {}

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
  private readonly refreshLeaseTtlMs: number;
  private readonly refreshProviderTimeoutMs: number;
  private readonly refreshWaitTimeoutMs: number;
  private readonly refreshPollIntervalMs: number;
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
    this.refreshLeaseTtlMs = options.refreshLeaseTtlMs ?? DEFAULT_REFRESH_LEASE_TTL_MS;
    this.refreshProviderTimeoutMs = options.refreshProviderTimeoutMs ?? DEFAULT_REFRESH_PROVIDER_TIMEOUT_MS;
    this.refreshWaitTimeoutMs = options.refreshWaitTimeoutMs ?? DEFAULT_REFRESH_WAIT_TIMEOUT_MS;
    this.refreshPollIntervalMs = options.refreshPollIntervalMs ?? DEFAULT_REFRESH_POLL_INTERVAL_MS;
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
        expiresAt: now + this.sessionTtlMs,
        tokenRevision: 1
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
      const vaultEnvelope = await this.tokenVault.seal(
        tokenBundle,
        this.vaultContext(sessionId, subject, session.tokenRevision)
      );
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
          const bundle = await this.tokenVault.open(
            pair.vault,
            this.vaultContext(sessionId, existingSession.subject, existingSession.tokenRevision)
          );
          assertSessionBundleRevision(existingSession, bundle);
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
      const current = await this.readValidatedSessionBundle(sessionId);
      if (!current) return null;
      const { session } = current;
      return {
        subject: session.subject,
        createdAt: session.createdAt,
        expiresAt: session.expiresAt,
        tokenRevision: session.tokenRevision
      };
    });
  }

  async getServerTokenBundle(sessionId?: string) {
    if (!sessionId) return null;
    return this.withRedis(async () => {
      return (await this.readValidatedSessionBundle(sessionId))?.bundle ?? null;
    });
  }

  async refreshSession(sessionId: string | undefined, expectedRevision: number, refresher: OAuthTokenRefresher) {
    if (!sessionId || !positiveSafeInteger(expectedRevision) || expectedRevision >= Number.MAX_SAFE_INTEGER) {
      throw new OAuthRefreshRevisionError("Token refresh revision is invalid.");
    }
    if (!refresher || typeof refresher.refresh !== "function") {
      throw new OAuthRefreshUnavailableError("Token refresh provider is unavailable.");
    }
    return this.withRedis(async () => {
      const initial = await this.readValidatedSessionBundle(sessionId);
      if (!initial) throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
      if (initial.bundle.revision > expectedRevision) return initial.bundle;
      if (initial.bundle.revision !== expectedRevision) {
        throw new OAuthRefreshRevisionError("Token refresh revision does not match the session.");
      }

      const deadline = Date.now() + this.refreshWaitTimeoutMs;
      while (Date.now() <= deadline) {
        const acquisition = await this.acquireRefreshLease(sessionId, expectedRevision);
        switch (acquisition.status) {
          case "ADVANCED": return this.readRefreshWinner(sessionId, expectedRevision);
          case "AMBIGUOUS": throw new OAuthRefreshAmbiguousError("Token refresh outcome is ambiguous.");
          case "BUSY":
            await sleep(this.refreshPollIntervalMs + Math.floor(Math.random() * this.refreshPollIntervalMs));
            continue;
          case "ACQUIRED":
            return this.refreshAsLeaseOwner(sessionId, expectedRevision, refresher, acquisition.lease);
          case "MISSING": throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
          case "PAIR_INTEGRITY_ERROR":
          case "REVISION_INTEGRITY_ERROR":
          case "INTENT_INTEGRITY_ERROR":
            throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
          case "REVISION_MISMATCH": throw new OAuthRefreshRevisionError("Token refresh revision does not match the session.");
          default: throw new OAuthRefreshUnavailableError("Token refresh coordination is unavailable.");
        }
      }
      throw new OAuthRefreshUnavailableError("Timed out waiting for token refresh coordination.");
    });
  }

  async logout(sessionId?: string) {
    if (!sessionId) return;
    await this.withRedis(async () => {
      await this.redis.del(
        this.key("session", sessionId),
        this.key("vault", sessionId),
        this.key("refresh-lease", sessionId),
        this.key("refresh-intent", sessionId),
        this.key("refresh-fence", sessionId)
      );
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

  private vaultContext(sessionId: string, subject: string, revision: number): TokenVaultContext {
    return { namespace: this.prefix, sessionHash: sha256(sessionId), subject, revision };
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
    if (result.length === 1 && (result[0] === "PAIR_INTEGRITY_ERROR" || result[0] === "PAIR_TTL_ERROR")) {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    if (
      result.length !== 4 ||
      typeof result[0] !== "string" ||
      typeof result[1] !== "string" ||
      typeof result[2] !== "string" ||
      typeof result[3] !== "string" ||
      !positiveSafeInteger(Number(result[2])) ||
      !positiveSafeInteger(Number(result[3]))
    ) {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    return { session: result[0], vault: result[1], sessionTtlMs: Number(result[2]), vaultTtlMs: Number(result[3]) };
  }

  private async readValidatedSessionBundle(sessionId: string) {
    const pair = await this.readSessionVaultPair(sessionId);
    if (!pair) return null;
    const session = parseSessionRecord(pair.session);
    const bundle = await this.tokenVault.open(
      pair.vault,
      this.vaultContext(sessionId, session.subject, session.tokenRevision)
    );
    assertSessionBundleRevision(session, bundle);
    return { session, bundle };
  }

  private async acquireRefreshLease(
    sessionId: string,
    expectedRevision: number
  ): Promise<RefreshLeaseAcquisition> {
    const owner = randomOpaque(24);
    const proposedOperationId = randomOpaque(24);
    const result = await this.redis.eval(
      ACQUIRE_REFRESH_SCRIPT,
      5,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-fence", sessionId),
      this.key("refresh-intent", sessionId),
      String(expectedRevision),
      owner,
      String(this.refreshLeaseTtlMs),
      proposedOperationId
    );
    if (!Array.isArray(result) || typeof result[0] !== "string") {
      return { status: "COORDINATION_ERROR" as const };
    }
    const status = result[0];
    if (status !== "ACQUIRED") {
      if (!isRefreshLeaseStatus(status)) return { status: "COORDINATION_ERROR" };
      return { status };
    }
    const fence = result[1];
    const operationId = result[2];
    if (typeof fence !== "string" || !canonicalRedisFence(fence) || !validOpaque(operationId)) {
      return { status: "COORDINATION_ERROR" as const };
    }
    const leaseValue = `${owner}:${fence}`;
    const intentValue = `${expectedRevision}:${operationId}:inflight`;
    return {
      status: "ACQUIRED" as const,
      lease: { owner, fence, operationId, leaseValue, intentValue }
    };
  }

  private async refreshAsLeaseOwner(
    sessionId: string,
    expectedRevision: number,
    refresher: OAuthTokenRefresher,
    lease: RefreshLease
  ): Promise<TokenBundle> {
    const current = await this.readValidatedSessionBundle(sessionId);
    if (!current) throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
    if (current.bundle.revision > expectedRevision) {
      await this.releaseRefreshLease(sessionId, lease.leaseValue);
      return current.bundle;
    }
    if (current.bundle.revision !== expectedRevision) {
      await this.releaseRefreshLease(sessionId, lease.leaseValue);
      throw new OAuthRefreshRevisionError("Token refresh revision does not match the session.");
    }

    const advanced = await this.beginRefreshProvider(sessionId, expectedRevision, lease);
    if (advanced) return advanced;

    const providerResult = await this.invokeRefreshProvider(refresher, current.bundle, lease.operationId);
    if (providerResult.kind === "transient") {
      const transition = await this.markRefreshRetryable(sessionId, expectedRevision, lease);
      if (transition) return transition;
      throw new OAuthRefreshUnavailableError("Token refresh provider is temporarily unavailable.");
    }
    if (providerResult.kind === "ambiguous") {
      return this.markRefreshAmbiguous(sessionId, expectedRevision, lease);
    }
    if (providerResult.kind === "invalid_grant") {
      return this.revokeAfterInvalidGrant(sessionId, expectedRevision, lease);
    }

    const now = Date.now();
    const normalizedScopes = [...new Set(providerResult.scopes)].sort();
    if (
      normalizedScopes.length !== providerResult.scopes.length ||
      normalizedScopes.some((scope) => !current.bundle.scopes.includes(scope)) ||
      providerResult.accessTokenExpiresAt <= now ||
      providerResult.refreshTokenExpiresAt <= providerResult.accessTokenExpiresAt
    ) {
      return this.markRefreshAmbiguous(sessionId, expectedRevision, lease);
    }
    const nextRevision = expectedRevision + 1;
    const nextBundle: TokenBundle = {
      subject: current.bundle.subject,
      tokenType: "Bearer",
      accessToken: providerResult.accessToken,
      refreshToken: providerResult.refreshToken,
      accessTokenExpiresAt: providerResult.accessTokenExpiresAt,
      refreshTokenExpiresAt: providerResult.refreshTokenExpiresAt,
      scopes: normalizedScopes,
      revision: nextRevision
    };
    const nextSession: SessionRecord = { ...current.session, tokenRevision: nextRevision };
    let nextEnvelope: string;
    try {
      nextEnvelope = await this.tokenVault.seal(
        nextBundle,
        this.vaultContext(sessionId, nextSession.subject, nextRevision)
      );
    } catch {
      return this.markRefreshAmbiguous(sessionId, expectedRevision, lease);
    }

    const commit = await this.redis.eval(
      COMMIT_REFRESH_SCRIPT,
      5,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-intent", sessionId),
      this.key("refresh-fence", sessionId),
      lease.leaseValue,
      lease.intentValue,
      String(expectedRevision),
      JSON.stringify(nextSession),
      nextEnvelope,
      String(now),
      String(nextBundle.refreshTokenExpiresAt)
    );
    switch (commit) {
      case "OK": return nextBundle;
      case "PAIR_INTEGRITY_ERROR":
      case "INTENT_INTEGRITY_ERROR":
      case "REVISION_INTEGRITY_ERROR":
      case "SESSION_INTEGRITY_ERROR":
        throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
      case "STALE_REVISION": return this.readRefreshWinner(sessionId, expectedRevision);
      case "STALE_FENCE": return this.markRefreshAmbiguous(sessionId, expectedRevision, lease);
      case "MISSING": throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
      default: throw new OAuthRefreshUnavailableError("Token refresh commit is unavailable.");
    }
  }

  private async invokeRefreshProvider(
    refresher: OAuthTokenRefresher,
    current: TokenBundle,
    operationId: string
  ) {
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      const raw = await Promise.race([
        refresher.refresh({
          subject: current.subject,
          refreshToken: current.refreshToken,
          scopes: [...current.scopes],
          revision: current.revision,
          idempotencyKey: operationId,
          signal: controller.signal
        }),
        new Promise<{ kind: "ambiguous" }>((resolve) => {
          timeout = setTimeout(() => {
            controller.abort();
            resolve({ kind: "ambiguous" });
          }, this.refreshProviderTimeoutMs);
        })
      ]);
      return parseRefreshProviderResult(raw);
    } catch (error) {
      if (error instanceof OAuthRefreshProviderProtocolError) return { kind: "ambiguous" } as const;
      return { kind: "ambiguous" } as const;
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  }

  private async beginRefreshProvider(
    sessionId: string,
    expectedRevision: number,
    lease: RefreshLease
  ): Promise<TokenBundle | null> {
    const result = await this.redis.eval(
      BEGIN_REFRESH_PROVIDER_SCRIPT,
      4,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-intent", sessionId),
      lease.leaseValue,
      String(expectedRevision),
      lease.operationId,
      lease.intentValue,
      String(this.refreshProviderTimeoutMs + MIN_REFRESH_COMMIT_MARGIN_MS)
    );
    if (result === "STARTED") return null;
    if (result === "STALE_REVISION") return this.readRefreshWinner(sessionId, expectedRevision);
    if (result === "PAIR_INTEGRITY_ERROR" || result === "INTENT_INTEGRITY_ERROR") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    throw new OAuthRefreshUnavailableError("Token refresh provider coordination is unavailable.");
  }

  private async releaseRefreshLease(sessionId: string, leaseValue: string) {
    await this.redis.eval(
      RELEASE_REFRESH_SCRIPT,
      1,
      this.key("refresh-lease", sessionId),
      leaseValue
    );
  }

  private async markRefreshRetryable(
    sessionId: string,
    expectedRevision: number,
    lease: RefreshLease
  ): Promise<TokenBundle | null> {
    const result = await this.redis.eval(
      MARK_REFRESH_RETRYABLE_SCRIPT,
      4,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-intent", sessionId),
      lease.leaseValue,
      lease.intentValue,
      String(expectedRevision),
      `${expectedRevision}:${lease.operationId}:retryable`
    );
    if (result === "RETRYABLE") return null;
    if (result === "STALE_REVISION") return this.readRefreshWinner(sessionId, expectedRevision);
    if (result === "PAIR_INTEGRITY_ERROR" || result === "INTENT_INTEGRITY_ERROR") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    throw new OAuthRefreshUnavailableError("Token refresh retry coordination is unavailable.");
  }

  private async markRefreshAmbiguous(
    sessionId: string,
    expectedRevision: number,
    lease: RefreshLease
  ): Promise<TokenBundle> {
    const ambiguousIntent = `${expectedRevision}:${lease.operationId}:ambiguous`;
    const result = await this.redis.eval(
      MARK_REFRESH_AMBIGUOUS_SCRIPT,
      4,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-intent", sessionId),
      lease.leaseValue,
      lease.intentValue,
      String(expectedRevision),
      ambiguousIntent
    );
    if (result === "MARKED") throw new OAuthRefreshAmbiguousError("Token refresh outcome is ambiguous.");
    if (result === "STALE_REVISION") return this.readRefreshWinner(sessionId, expectedRevision);
    if (result === "PAIR_INTEGRITY_ERROR" || result === "INTENT_INTEGRITY_ERROR") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
  }

  private async revokeAfterInvalidGrant(
    sessionId: string,
    expectedRevision: number,
    lease: RefreshLease
  ): Promise<TokenBundle> {
    const result = await this.redis.eval(
      REVOKE_REFRESH_SCRIPT,
      5,
      this.key("session", sessionId),
      this.key("vault", sessionId),
      this.key("refresh-lease", sessionId),
      this.key("refresh-intent", sessionId),
      this.key("refresh-fence", sessionId),
      lease.leaseValue,
      lease.intentValue,
      String(expectedRevision)
    );
    if (result === "REVOKED") throw new OAuthRefreshInvalidGrantError("Token refresh grant is invalid.");
    if (result === "STALE_REVISION") return this.readRefreshWinner(sessionId, expectedRevision);
    if (result === "STALE_FENCE") return this.markRefreshAmbiguous(sessionId, expectedRevision, lease);
    if (result === "PAIR_INTEGRITY_ERROR" || result === "INTENT_INTEGRITY_ERROR") {
      throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
    }
    throw new OAuthRefreshUnavailableError("OAuth session is unavailable for refresh.");
  }

  private async readRefreshWinner(sessionId: string, expectedRevision: number) {
    const current = await this.readValidatedSessionBundle(sessionId);
    if (current && current.bundle.revision > expectedRevision) return current.bundle;
    throw new OAuthRefreshUnavailableError("Token refresh winner is not available.");
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
    ["receipt TTL", options.receiptTtlMs ?? DEFAULT_RECEIPT_TTL_MS],
    ["refresh lease TTL", options.refreshLeaseTtlMs ?? DEFAULT_REFRESH_LEASE_TTL_MS],
    ["refresh provider timeout", options.refreshProviderTimeoutMs ?? DEFAULT_REFRESH_PROVIDER_TIMEOUT_MS],
    ["refresh wait timeout", options.refreshWaitTimeoutMs ?? DEFAULT_REFRESH_WAIT_TIMEOUT_MS],
    ["refresh poll interval", options.refreshPollIntervalMs ?? DEFAULT_REFRESH_POLL_INTERVAL_MS]
  ] as const) {
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new OAuthStoreConfigurationError(`${label} must be a positive integer.`);
    }
  }
  const refreshLeaseTtlMs = options.refreshLeaseTtlMs ?? DEFAULT_REFRESH_LEASE_TTL_MS;
  const refreshProviderTimeoutMs = options.refreshProviderTimeoutMs ?? DEFAULT_REFRESH_PROVIDER_TIMEOUT_MS;
  const refreshWaitTimeoutMs = options.refreshWaitTimeoutMs ?? DEFAULT_REFRESH_WAIT_TIMEOUT_MS;
  const refreshPollIntervalMs = options.refreshPollIntervalMs ?? DEFAULT_REFRESH_POLL_INTERVAL_MS;
  if (refreshLeaseTtlMs <= refreshProviderTimeoutMs + MIN_REFRESH_COMMIT_MARGIN_MS) {
    throw new OAuthStoreConfigurationError("Refresh lease TTL must exceed provider timeout and commit margin.");
  }
  if (refreshWaitTimeoutMs < refreshPollIntervalMs) {
    throw new OAuthStoreConfigurationError("Refresh wait timeout must be at least one poll interval.");
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
    Object.keys(record).sort().join(",") !== "createdAt,expiresAt,subject,tokenRevision" ||
    typeof record.subject !== "string" ||
    record.subject.length === 0 ||
    !Number.isSafeInteger(record.createdAt) ||
    !Number.isSafeInteger(record.expiresAt) ||
    (record.expiresAt as number) <= (record.createdAt as number) ||
    !Number.isSafeInteger(record.tokenRevision) ||
    (record.tokenRevision as number) <= 0
  ) {
    throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
  }
  return record as unknown as SessionRecord;
}

function assertSessionBundleRevision(session: SessionRecord, bundle: TokenBundle) {
  if (bundle.revision !== session.tokenRevision || bundle.subject !== session.subject) {
    throw new OAuthVaultIntegrityError("OAuth session vault integrity failure.");
  }
}

function parseSessionHmacKey(encoded: string) {
  const decoded = Buffer.from(encoded, "base64");
  if (decoded.length !== 32 || decoded.toString("base64") !== encoded) {
    throw new OAuthStoreConfigurationError("APPSELL_SESSION_HMAC_KEY_B64 must be canonical base64 for exactly 32 bytes.");
  }
  return decoded;
}

function positiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function canonicalRedisFence(value: string) {
  if (!/^[1-9]\d{0,18}$/.test(value)) return false;
  return value.length < MAX_REDIS_FENCE.length ||
    (value.length === MAX_REDIS_FENCE.length && value <= MAX_REDIS_FENCE);
}

function validOpaque(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{32}$/.test(value);
}

function isRefreshLeaseStatus(value: string): value is RefreshLeaseStatus {
  return [
    "ADVANCED",
    "AMBIGUOUS",
    "BUSY",
    "MISSING",
    "PAIR_INTEGRITY_ERROR",
    "REVISION_INTEGRITY_ERROR",
    "INTENT_INTEGRITY_ERROR",
    "REVISION_MISMATCH",
    "TTL_ERROR",
    "FENCE_ERROR",
    "COORDINATION_ERROR"
  ].includes(value);
}

function sleep(milliseconds: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
}
