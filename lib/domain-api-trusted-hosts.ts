export class TrustedInternalHostsConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TrustedInternalHostsConfigurationError";
  }
}

// Single-label service names only (Docker/Swarm internal DNS). A dot would make
// the entry resolvable as a public FQDN, which this allowlist must never trust.
const INTERNAL_HOST_PATTERN = /^[a-z0-9][a-z0-9_-]{0,62}$/;

export function trustedInternalApiHost(
  hostname: string,
  environment: Record<string, string | undefined> = process.env
): boolean {
  const raw = environment.APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS;
  if (raw === undefined || raw === "") return false;
  const entries = raw.split(",").map((entry) => entry.trim());
  if (entries.length === 0 || !entries.every((entry) => INTERNAL_HOST_PATTERN.test(entry))) {
    throw new TrustedInternalHostsConfigurationError(
      "APPSELL_DOMAIN_API_TRUSTED_INTERNAL_HOSTS must be a comma-separated list of " +
        "single-label internal service hostnames (lowercase letters, digits, '-', '_')."
    );
  }
  return entries.includes(hostname);
}
