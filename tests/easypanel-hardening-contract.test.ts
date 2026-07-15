import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const harden = readFileSync("deploy/easypanel/harden-sell-services.sh", "utf8");
const verify = readFileSync("deploy/easypanel/verify-sell-isolation.sh", "utf8");
const scripts = `${harden}\n${verify}`;

describe("EasyPanel Sell isolation guard", () => {
  it("is scoped to the three exact Sell services", () => {
    for (const service of [
      "sell-autonomia_postgres",
      "sell-autonomia_redis",
      "sell-autonomia_storage"
    ]) {
      expect(harden).toContain(service);
      expect(verify).toContain(service);
    }
    expect(scripts).not.toMatch(/\b(?:dados|flow)_/);
  });

  it("requires confirmation and never carries destructive fleet commands", () => {
    expect(harden).toContain("CONFIRM_SELL_AUTONOMIA_HARDENING");
    expect(harden).toContain("sell-autonomia-v1");
    expect(scripts).not.toMatch(/docker\s+(?:system|volume|network|container)\s+prune/);
    expect(scripts).not.toMatch(/docker\s+stack\s+rm/);
    expect(scripts).not.toMatch(/docker\s+service\s+rm/);
  });

  it("fails closed on network, ports, resources and health", () => {
    expect(verify).toContain("attached to global EasyPanel network");
    expect(verify).toContain("has published ports");
    expect(verify).toContain("missing memory limits");
    expect(verify).toContain("missing CPU limits");
    expect(verify).toContain("missing healthcheck");
    expect(verify).toContain('[[ "$health" == "healthy" ]]');
  });
});
