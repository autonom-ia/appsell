import type { DemoProfile } from "./demo-config";

export function oauthCallbackHome(profile: DemoProfile | null) {
  return profile?.home ?? "/dashboard";
}
