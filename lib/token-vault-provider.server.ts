import "server-only";

import { OpenBaoTransitKeyEnvelopeProvider } from "./openbao-transit-key-envelope-provider.server";
import {
  EnvelopeTokenVault,
  LocalAesKeyEnvelopeProvider,
  TokenVaultConfigurationError,
  type TokenVault
} from "./token-vault";

export function createTokenVaultFromEnvironment(
  environment: Partial<Record<string, string | undefined>> = process.env,
  forbiddenKeys: Buffer[] = []
): TokenVault {
  const provider = environment.APPSELL_VAULT_PROVIDER ?? "local";
  if (provider === "local") {
    return new EnvelopeTokenVault(LocalAesKeyEnvelopeProvider.fromEnvironment(environment, forbiddenKeys));
  }
  if (provider === "openbao-transit") {
    const transit = OpenBaoTransitKeyEnvelopeProvider.fromEnvironment(environment);
    transit.assertKeySeparation(forbiddenKeys);
    return new EnvelopeTokenVault(transit);
  }
  throw new TokenVaultConfigurationError("APPSELL_VAULT_PROVIDER is invalid.");
}
