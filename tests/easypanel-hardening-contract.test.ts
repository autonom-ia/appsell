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
    expect(verify).toContain('[[ "$cpu_limit" == "${cpu_limits[$service]}" ]]');
    expect(verify).toContain('[[ "$cpu_reservation" == "${cpu_reservations[$service]}" ]]');
    expect(verify).toContain('[[ "$memory_limit" == "${memory_limits[$service]}" ]]');
    expect(verify).toContain('[[ "$memory_reservation" == "${memory_reservations[$service]}" ]]');
    expect(verify).toContain("missing healthcheck");
    expect(verify).toContain("healthcheck command drift");
    expect(verify).toContain('[[ "$health_interval" == "15s" ]]');
    expect(verify).toContain('[[ "$health_timeout" == "5s" ]]');
    expect(verify).toContain('[[ "$health_retries" == "5" ]]');
    expect(verify).toContain('[[ "$health_start_period" == "20s" ]]');
    expect(verify).toContain('[[ "$health" == "healthy" ]]');
    expect(verify).toContain('label=com.docker.swarm.task.id=${running_tasks[0]}');
    expect(verify).toContain('[[ -z "$update_state" || "$update_state" == "completed" ]]');
    expect(verify).toContain('[[ "$desired_replicas" == "1" ]]');
  });

  it("preflights every scoped service and the verifier before the first mutation", () => {
    const preflight = harden.indexOf("# Fail before the first mutation");
    const update = harden.indexOf("docker service update");
    expect(preflight).toBeGreaterThanOrEqual(0);
    expect(update).toBeGreaterThan(preflight);
    const guard = harden.slice(preflight, update);
    expect(guard).toContain('docker service inspect "$service"');
    expect(guard).toContain('[[ -x "$verify_script" ]]');
    expect(guard).toContain('[[ "$desired_replicas" == "1" ]]');
    expect(guard).toContain('[[ "$ports" == "null" || "$ports" == "[]" ]]');
    expect(guard).toContain("has unexpected network before hardening");
  });

  it("waits for the new task and completed rolling update before success", () => {
    expect(harden).toContain("docker service update --detach=false");
    expect(harden).not.toContain("docker service update --detach=true");
    expect(harden).toContain('[[ "$update_state" == "completed" ]]');
    expect(harden).toContain('"${running_tasks[0]}" == "${previous_task_id[$service]}"');
    expect(harden).toContain('label=com.docker.swarm.task.id=${running_tasks[0]}');
    expect(harden).toContain('[[ "$converged" == "true" ]]');
  });
});
