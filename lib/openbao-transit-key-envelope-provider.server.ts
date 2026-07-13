import "server-only";

import {
  OPENBAO_TRANSIT_PROVIDER_ID,
  TokenVaultAuthenticationError,
  TokenVaultConfigurationError,
  TokenVaultMalformedError,
  parseOpenBaoTransitCiphertextVersion,
  type KeyEnvelope,
  type KeyEnvelopeProvider,
  type OpenBaoTransitKeyEnvelope
} from "./token-vault";

const AES_KEY_BYTES = 32;
const DEFAULT_TIMEOUT_MS = 1_000;
const MIN_TIMEOUT_MS = 100;
const MAX_TIMEOUT_MS = 10_000;
const MAX_RESPONSE_BYTES = 16 * 1024;
const PATH_SEGMENT_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const TOKEN_PATTERN = /^[^\s\u0000-\u001f\u007f]{16,4096}$/;

export interface OpenBaoTransitConfiguration {
  address: string;
  mount: string;
  keyName: string;
  token: string;
  timeoutMs: number;
  nodeEnvironment?: string;
}

export class OpenBaoTransitKeyEnvelopeProvider implements KeyEnvelopeProvider {
  private readonly baseUrl: string;
  private readonly mount: string;
  private readonly keyName: string;
  private readonly token: string;
  private readonly timeoutMs: number;

  constructor(configuration: OpenBaoTransitConfiguration) {
    if (process.env.NODE_ENV === "production" || configuration.nodeEnvironment === "production") {
      throw new TokenVaultConfigurationError("OpenBao Transit vault provider is not certified for production.");
    }
    this.baseUrl = parseAddress(configuration.address);
    this.mount = parsePathSegment(configuration.mount, "OpenBao Transit mount is invalid.");
    this.keyName = parsePathSegment(configuration.keyName, "OpenBao Transit key name is invalid.");
    if (!TOKEN_PATTERN.test(configuration.token)) {
      throw new TokenVaultConfigurationError("OpenBao Transit token is invalid.");
    }
    if (
      !Number.isSafeInteger(configuration.timeoutMs) ||
      configuration.timeoutMs < MIN_TIMEOUT_MS ||
      configuration.timeoutMs > MAX_TIMEOUT_MS
    ) {
      throw new TokenVaultConfigurationError("OpenBao Transit timeout is invalid.");
    }
    this.token = configuration.token;
    this.timeoutMs = configuration.timeoutMs;
  }

  static fromEnvironment(environment: Partial<Record<string, string | undefined>> = process.env) {
    const timeoutRaw = environment.APPSELL_OPENBAO_HTTP_TIMEOUT_MS ?? String(DEFAULT_TIMEOUT_MS);
    if (!/^\d+$/.test(timeoutRaw)) {
      throw new TokenVaultConfigurationError("APPSELL_OPENBAO_HTTP_TIMEOUT_MS is invalid.");
    }
    return new OpenBaoTransitKeyEnvelopeProvider({
      address: environment.APPSELL_OPENBAO_ADDR ?? "",
      mount: environment.APPSELL_OPENBAO_TRANSIT_MOUNT ?? "",
      keyName: environment.APPSELL_OPENBAO_TRANSIT_KEY ?? "",
      token: environment.APPSELL_OPENBAO_TOKEN ?? "",
      timeoutMs: Number(timeoutRaw),
      nodeEnvironment: environment.NODE_ENV
    });
  }

  async wrapKey(dataKey: Buffer, authenticatedContext: Buffer): Promise<OpenBaoTransitKeyEnvelope> {
    validateDataKey(dataKey);
    validateAuthenticatedContext(authenticatedContext);
    const response = await this.request("encrypt", {
      plaintext: dataKey.toString("base64"),
      associated_data: transitAssociatedData(authenticatedContext, this.keyName).toString("base64")
    });
    const ciphertext = extractString(response, "ciphertext");
    let keyVersion: number;
    try {
      keyVersion = parseOpenBaoTransitCiphertextVersion(ciphertext);
    } catch {
      throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
    }
    return {
      provider: OPENBAO_TRANSIT_PROVIDER_ID,
      kid: this.keyName,
      alg: "OPENBAO_TRANSIT",
      ciphertext,
      keyVersion
    };
  }

  async unwrapKey(envelope: KeyEnvelope, authenticatedContext: Buffer): Promise<Buffer> {
    validateAuthenticatedContext(authenticatedContext);
    if (
      envelope.provider !== OPENBAO_TRANSIT_PROVIDER_ID ||
      envelope.alg !== "OPENBAO_TRANSIT" ||
      envelope.kid !== this.keyName
    ) {
      throw new TokenVaultMalformedError("Token vault key envelope provider is invalid.");
    }
    if (parseOpenBaoTransitCiphertextVersion(envelope.ciphertext) !== envelope.keyVersion) {
      throw new TokenVaultMalformedError("Token vault key envelope is invalid.");
    }
    const response = await this.request("decrypt", {
      ciphertext: envelope.ciphertext,
      associated_data: transitAssociatedData(authenticatedContext, this.keyName).toString("base64")
    });
    const plaintext = decodeCanonicalBase64(extractString(response, "plaintext"));
    if (plaintext.length !== AES_KEY_BYTES) {
      plaintext.fill(0);
      throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
    }
    return plaintext;
  }

  assertKeySeparation(forbiddenKeys: Buffer[]) {
    if (!Array.isArray(forbiddenKeys)) {
      throw new TokenVaultConfigurationError("Vault key separation inputs are invalid.");
    }
  }

  private async request(operation: "encrypt" | "decrypt", payload: Record<string, string>) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(
        `${this.baseUrl}/v1/${encodeURIComponent(this.mount)}/${operation}/${encodeURIComponent(this.keyName)}`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-vault-token": this.token
          },
          body: JSON.stringify(payload),
          cache: "no-store",
          credentials: "omit",
          redirect: "manual",
          signal: controller.signal
        }
      );
      const body = await readBoundedJson(response);
      if (!response.ok) {
        throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
      }
      return extractData(body);
    } catch (error) {
      if (error instanceof TokenVaultAuthenticationError || error instanceof TokenVaultMalformedError) throw error;
      throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
    } finally {
      clearTimeout(timeout);
    }
  }
}

function parseAddress(raw: string) {
  let address: URL;
  try {
    address = new URL(raw);
  } catch {
    throw new TokenVaultConfigurationError("OpenBao Transit address is invalid.");
  }
  const isLoopback = address.hostname === "127.0.0.1" || address.hostname === "localhost" || address.hostname === "[::1]";
  if (
    (address.protocol !== "https:" && !(address.protocol === "http:" && isLoopback)) ||
    address.username ||
    address.password ||
    address.pathname !== "/" ||
    address.search ||
    address.hash ||
    address.origin === "null"
  ) {
    throw new TokenVaultConfigurationError("OpenBao Transit address is invalid.");
  }
  return address.origin;
}

function parsePathSegment(raw: string, message: string) {
  if (!PATH_SEGMENT_PATTERN.test(raw)) throw new TokenVaultConfigurationError(message);
  return raw;
}

function validateDataKey(value: Buffer) {
  if (!Buffer.isBuffer(value) || value.length !== AES_KEY_BYTES) {
    throw new TokenVaultAuthenticationError("Token vault data key is invalid.");
  }
}

function validateAuthenticatedContext(value: Buffer) {
  if (!Buffer.isBuffer(value) || value.length === 0 || value.length > 8 * 1024) {
    throw new TokenVaultAuthenticationError("Token vault authenticated context is invalid.");
  }
}

function transitAssociatedData(authenticatedContext: Buffer, keyName: string) {
  return Buffer.from(JSON.stringify([
    "appsell-token-vault-key-provider",
    1,
    OPENBAO_TRANSIT_PROVIDER_ID,
    keyName,
    "OPENBAO_TRANSIT",
    authenticatedContext.toString("base64url")
  ]), "utf8");
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)) {
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
  if (!response.body) throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.length;
    if (length > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch {
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
}

function extractData(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
  const data = (value as Record<string, unknown>).data;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
  return data as Record<string, unknown>;
}

function extractString(data: Record<string, unknown>, key: string) {
  const value = data[key];
  if (typeof value !== "string" || value.length === 0 || Buffer.byteLength(value, "utf8") > MAX_RESPONSE_BYTES) {
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
  return value;
}

function decodeCanonicalBase64(value: string) {
  const decoded = Buffer.from(value, "base64");
  if (decoded.toString("base64") !== value) {
    decoded.fill(0);
    throw new TokenVaultAuthenticationError("OpenBao Transit operation failed.");
  }
  return decoded;
}
