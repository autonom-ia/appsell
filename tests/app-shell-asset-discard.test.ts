import { describe, expect, it, vi } from "vitest";
import { continueConfirmedExit, discardActiveAssetUploads, type ConfirmedExitType } from "../app/components/app-shell";

describe("app shell asset discard gate", () => {
  it("settles every active panel before succeeding", async () => {
    const target = new EventTarget();
    const order: string[] = [];
    target.addEventListener("appsell:asset-discard-request", (event) => {
      const detail = (event as CustomEvent<{ register(task: Promise<void>): void }>).detail;
      detail.register(Promise.resolve().then(() => { order.push("first"); }));
      detail.register(Promise.resolve().then(() => { order.push("second"); }));
    });
    await discardActiveAssetUploads(target);
    expect(order).toEqual(["first", "second"]);
  });

  it("fails closed when any panel cannot cancel", async () => {
    const target = new EventTarget();
    target.addEventListener("appsell:asset-discard-request", (event) => {
      const detail = (event as CustomEvent<{ register(task: Promise<void>): void }>).detail;
      detail.register(Promise.resolve());
      detail.register(Promise.reject(new Error("cancel failed")));
    });
    await expect(discardActiveAssetUploads(target)).rejects.toThrow("asset_discard_failed");
  });

  it("succeeds without issuing work when no upload is active", async () => {
    const target = new EventTarget();
    const listener = vi.fn();
    target.addEventListener("appsell:asset-discard-request", listener);
    await expect(discardActiveAssetUploads(target)).resolves.toBeUndefined();
    expect(listener).toHaveBeenCalledOnce();
  });
});

describe("confirmed navigation discard gate", () => {
  it.each<ConfirmedExitType>(["navigate", "history", "logout"])("waits before continuing %s", async (type) => {
    const target = new EventTarget();
    const order: string[] = [];
    let release!: () => void;
    target.addEventListener("appsell:asset-discard-request", (event) => {
      const detail = (event as CustomEvent<{ register(task: Promise<void>): void }>).detail;
      detail.register(new Promise<void>((resolve) => { release = () => { order.push("discarded"); resolve(); }; }));
    });
    const continuations = {
      navigate: () => { order.push("navigate"); },
      history: () => { order.push("history"); },
      logout: () => { order.push("logout"); }
    };
    const pending = continueConfirmedExit(type, continuations, target);
    await Promise.resolve();
    expect(order).toEqual([]);
    release();
    await pending;
    expect(order).toEqual(["discarded", type]);
  });

  it.each<ConfirmedExitType>(["navigate", "history", "logout"])("blocks %s when discard fails", async (type) => {
    const target = new EventTarget();
    target.addEventListener("appsell:asset-discard-request", (event) => {
      const detail = (event as CustomEvent<{ register(task: Promise<void>): void }>).detail;
      detail.register(Promise.reject(new Error("cancel unavailable")));
    });
    const continuations = { navigate: vi.fn(), history: vi.fn(), logout: vi.fn() };
    await expect(continueConfirmedExit(type, continuations, target)).rejects.toThrow("asset_discard_failed");
    expect(continuations.navigate).not.toHaveBeenCalled();
    expect(continuations.history).not.toHaveBeenCalled();
    expect(continuations.logout).not.toHaveBeenCalled();
  });
});
