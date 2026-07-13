import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "..");
const manifest = JSON.parse(readFileSync(
  resolve(root, "docs/research/appsell-neuro-product.inactive.json"), "utf8"
)) as Record<string, unknown>;

describe("inactive Neuro.ai product manifest", () => {
  it("pins the exact same payload documented for future registration", () => {
    const research = readFileSync(resolve(root, "docs/research/appsell-pricing-neuro-pix.md"), "utf8");
    const documented = research.match(/```json\n([\s\S]*?)\n```/)?.[1];
    expect(documented).toBeDefined();
    expect(JSON.parse(documented!)).toEqual(manifest);
  });

  it("remains inactive and uses only exact production HTTPS origins", () => {
    expect(Object.keys(manifest).sort()).toEqual([
      "allowBackgroundAuth",
      "allowEmailPasswordLogin",
      "allowGithubLogin",
      "allowGoogleLogin",
      "allowPasskeyLogin",
      "allowedLogoutUris",
      "allowedOrigins",
      "allowedRedirectUris",
      "key",
      "name",
      "oauthClientId",
      "status"
    ]);
    expect(manifest).toEqual({
      key: "appsell",
      name: "AppSell by Autonom.ia",
      oauthClientId: "appsell-web",
      allowedRedirectUris: ["https://sell.autonomia.site/auth/callback"],
      allowedLogoutUris: ["https://sell.autonomia.site/auth/login"],
      allowedOrigins: ["https://sell.autonomia.site"],
      allowEmailPasswordLogin: true,
      allowGoogleLogin: false,
      allowGithubLogin: false,
      allowPasskeyLogin: false,
      allowBackgroundAuth: false,
      status: "inactive"
    });
    const serialized = JSON.stringify(manifest);
    expect(serialized).not.toContain("*");
    expect(serialized).not.toContain("http:");
    expect(serialized).not.toContain("localhost");
  });

  it("points callbacks and logout to routes that actually exist in AppSell", () => {
    expect(readFileSync(resolve(root, "app/auth/callback/route.ts"), "utf8")).toContain("export async function GET");
    expect(readFileSync(resolve(root, "app/auth/login/route.ts"), "utf8")).toContain("export async function GET");
  });
});
