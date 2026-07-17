import { hmacSha256, safeEqual } from "./crypto";
import type { DemoPersona } from "./demo-config";

function bindingKey(environment: Partial<Record<string, string | undefined>> = process.env) {
  const encoded = environment.APPSELL_SESSION_HMAC_KEY_B64 ?? "";
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32 || key.toString("base64") !== encoded) {
    throw new Error("APPSELL_SESSION_HMAC_KEY_B64 must be canonical base64 for exactly 32 bytes.");
  }
  return key;
}

export function signDemoPersonaBinding(
  state: string,
  persona: DemoPersona,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return hmacSha256(bindingKey(environment), `${state}\0${persona}`);
}

export function verifyDemoPersonaBinding(
  state: string,
  persona: DemoPersona,
  signature: string | null,
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  return Boolean(signature) && safeEqual(
    signDemoPersonaBinding(state, persona, environment),
    signature ?? ""
  );
}
