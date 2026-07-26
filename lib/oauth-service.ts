import { pkceChallenge, randomOpaque, safeEqual, sha256 } from "./crypto";

type Clock = () => number;

interface LoginTransaction {
  state: string;
  nonce: string;
  verifier: string;
  expiresAt: number;
}

interface AuthorizationCode {
  state: string;
  nonce: string;
  challenge: string;
  subject: string;
  expiresAt: number;
}

interface SessionRecord {
  subject: string;
  accessToken: string;
  refreshToken: string;
  createdAt: number;
  expiresAt: number;
  revokedAt?: number;
}

interface ServiceLimits {
  maxRecords?: number;
  rateWindowMs?: number;
  maxPerClient?: number;
  maxGlobal?: number;
}

interface RateWindow {
  count: number;
  resetsAt: number;
}

// "recoverable" marks a failure where the browser came back from the identity
// provider but this app no longer holds the login transaction — it expired, or
// the cookie is gone. Nothing is wrong with the user or the account: restarting
// the login completes silently while the provider session is still valid. This
// is the shape a sign-up hits, because creating an account and confirming the
// e-mail takes longer than the transaction TTL.
export type OAuthFlowFailure = "recoverable" | "final";

export class OAuthFlowError extends Error {
  readonly failure: OAuthFlowFailure;

  constructor(message: string, failure: OAuthFlowFailure = "final") {
    super(message);
    this.failure = failure;
  }
}

/** Development-only process-local OAuth transaction and token vault. */
export class DevOAuthService {
  private readonly transactions = new Map<string, LoginTransaction>();
  private readonly codes = new Map<string, AuthorizationCode>();
  private readonly sessions = new Map<string, SessionRecord>();
  private readonly clientRates = new Map<string, RateWindow>();
  private readonly globalRates = new Map<string, RateWindow>();
  private readonly maxRecords: number;
  private readonly rateWindowMs: number;
  private readonly maxPerClient: number;
  private readonly maxGlobal: number;

  constructor(
    private readonly now: Clock = Date.now,
    private readonly transactionTtlMs = 5 * 60_000,
    private readonly codeTtlMs = 60_000,
    private readonly sessionTtlMs = 60 * 60_000,
    limits: ServiceLimits = {}
  ) {
    this.maxRecords = limits.maxRecords ?? 1_000;
    this.rateWindowMs = limits.rateWindowMs ?? 60_000;
    this.maxPerClient = limits.maxPerClient ?? 120;
    this.maxGlobal = limits.maxGlobal ?? 1_000;
  }

  beginLogin(clientKey = "local") {
    this.maintain();
    this.assertCapacity(this.transactions, "login transactions");
    this.assertAllowed("login", clientKey);
    const transactionId = randomOpaque();
    const transaction = {
      state: randomOpaque(),
      nonce: randomOpaque(),
      verifier: randomOpaque(48),
      expiresAt: this.now() + this.transactionTtlMs
    };
    this.transactions.set(sha256(transactionId), transaction);
    return {
      transactionId,
      state: transaction.state,
      nonce: transaction.nonce,
      codeChallenge: pkceChallenge(transaction.verifier)
    };
  }

  authorize(
    input: { state: string; nonce: string; codeChallenge: string },
    clientKey = "local",
    subject = "dev-user-001"
  ) {
    if (!input.state || !input.nonce || !input.codeChallenge) throw new OAuthFlowError("Invalid authorization request.");
    this.maintain();
    this.assertCapacity(this.codes, "authorization codes");
    this.assertAllowed("authorize", clientKey);
    const code = randomOpaque();
    this.codes.set(sha256(code), {
      state: input.state,
      nonce: input.nonce,
      challenge: input.codeChallenge,
      subject,
      expiresAt: this.now() + this.codeTtlMs
    });
    return code;
  }

  callback(input: { transactionId?: string; code?: string; state?: string }) {
    if (!input.transactionId || !input.code || !input.state) throw new OAuthFlowError("Missing callback parameters.");
    this.maintain();
    const transactionKey = sha256(input.transactionId);
    const transaction = this.transactions.get(transactionKey);
    if (!transaction) throw new OAuthFlowError("Login transaction expired.");
    if (!safeEqual(transaction.state, input.state)) throw new OAuthFlowError("Invalid OAuth state.");

    const codeKey = sha256(input.code);
    const authorizationCode = this.codes.get(codeKey);
    if (!authorizationCode) throw new OAuthFlowError("Authorization code expired or replayed.");
    if (!safeEqual(authorizationCode.state, transaction.state)) throw new OAuthFlowError("Authorization code state mismatch.");
    if (!safeEqual(authorizationCode.nonce, transaction.nonce)) throw new OAuthFlowError("Authorization nonce mismatch.");
    if (!safeEqual(authorizationCode.challenge, pkceChallenge(transaction.verifier))) throw new OAuthFlowError("PKCE validation failed.");
    this.assertCapacity(this.sessions, "sessions");

    // All checks happen before either single-use credential is consumed. This
    // synchronous compare-and-consume is atomic within one JavaScript process.
    this.transactions.delete(transactionKey);
    this.codes.delete(codeKey);
    const sessionId = randomOpaque();
    this.sessions.set(sha256(sessionId), {
      subject: authorizationCode.subject,
      accessToken: randomOpaque(),
      refreshToken: randomOpaque(48),
      createdAt: this.now(),
      expiresAt: this.now() + this.sessionTtlMs
    });
    return sessionId;
  }

  getSession(sessionId?: string) {
    if (!sessionId) return null;
    this.maintain();
    const session = this.sessions.get(sha256(sessionId));
    if (!session) return null;
    return { subject: session.subject, createdAt: session.createdAt, expiresAt: session.expiresAt };
  }

  logout(sessionId?: string) {
    if (!sessionId) return;
    this.sessions.delete(sha256(sessionId));
  }

  assertNoRawSessionKey(sessionId: string) {
    return !this.sessions.has(sessionId) && this.sessions.has(sha256(sessionId));
  }

  tamperCodeChallengeForTest(code: string) {
    const record = this.codes.get(sha256(code));
    if (record) record.challenge = "invalid";
  }

  recordCountsForTest() {
    return {
      transactions: this.transactions.size,
      codes: this.codes.size,
      sessions: this.sessions.size
    };
  }

  private maintain() {
    const now = this.now();
    for (const [key, value] of this.transactions) if (value.expiresAt <= now) this.transactions.delete(key);
    for (const [key, value] of this.codes) if (value.expiresAt <= now) this.codes.delete(key);
    for (const [key, value] of this.sessions) {
      if (value.revokedAt || value.expiresAt <= now) this.sessions.delete(key);
    }
    for (const [key, value] of this.clientRates) if (value.resetsAt <= now) this.clientRates.delete(key);
    for (const [key, value] of this.globalRates) if (value.resetsAt <= now) this.globalRates.delete(key);
  }

  private assertCapacity(map: Map<string, unknown>, label: string) {
    if (map.size >= this.maxRecords) throw new OAuthFlowError(`Too many active ${label}.`);
  }

  private assertAllowed(action: string, clientKey: string) {
    const clientRateKey = `${action}:${clientKey}`;
    const global = this.nextRate(this.globalRates, action, this.maxGlobal);
    const client = this.nextRate(this.clientRates, clientRateKey, this.maxPerClient);
    this.globalRates.set(action, global);
    this.clientRates.set(clientRateKey, client);
  }

  private nextRate(map: Map<string, RateWindow>, key: string, limit: number) {
    const now = this.now();
    const current = map.get(key);
    if (!current || current.resetsAt <= now) {
      return { count: 1, resetsAt: now + this.rateWindowMs };
    }
    if (current.count >= limit) throw new OAuthFlowError("Too many OAuth requests.");
    return { count: current.count + 1, resetsAt: current.resetsAt };
  }
}

export function isSameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return Boolean(origin && origin === new URL(request.url).origin);
}
