"use client";

import { useEffect } from "react";

export const DIRTY_STATE_EVENT = "appsell:dirty-state";
export const DIRTY_NAVIGATION_REQUEST_EVENT = "appsell:dirty-navigation-request";
export const DIRTY_HISTORY_BLOCKED_EVENT = "appsell:dirty-history-blocked";
export const DIRTY_HISTORY_CONTINUE_EVENT = "appsell:dirty-history-continue";
export const DIRTY_NAVIGATION_ALLOWED_EVENT = "appsell:dirty-navigation-allowed";

export function allowUnsavedNavigation() {
  window.dispatchEvent(new Event(DIRTY_NAVIGATION_ALLOWED_EVENT));
}

export function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty } }));

    if (!dirty) return;

    const token = crypto.randomUUID();
    let restoring = false;
    let allowNavigation = false;
    history.pushState({ ...history.state, __appsellDirtyGuard: token }, "", window.location.href);

    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    const allow = () => { allowNavigation = true; };
    const continueHistory = () => {
      allowNavigation = true;
      history.go(-2);
    };
    const guardHistory = () => {
      if (allowNavigation) return;
      if (restoring) {
        restoring = false;
        window.dispatchEvent(new Event(DIRTY_HISTORY_BLOCKED_EVENT));
        return;
      }
      restoring = true;
      history.go(1);
    };
    window.addEventListener(DIRTY_NAVIGATION_ALLOWED_EVENT, allow);
    window.addEventListener(DIRTY_HISTORY_CONTINUE_EVENT, continueHistory);
    window.addEventListener("popstate", guardHistory);

    return () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
      window.removeEventListener(DIRTY_NAVIGATION_ALLOWED_EVENT, allow);
      window.removeEventListener(DIRTY_HISTORY_CONTINUE_EVENT, continueHistory);
      window.removeEventListener("popstate", guardHistory);
      if (!allowNavigation) {
        const targetUrl = window.location.href;
        window.addEventListener("popstate", () => {
          const { __appsellDirtyGuard: _guard, ...rest } = history.state ?? {};
          void _guard;
          history.replaceState(rest, "", targetUrl);
        }, { once: true });
        history.back();
      }
      window.dispatchEvent(new CustomEvent(DIRTY_STATE_EVENT, { detail: { dirty: false } }));
    };
  }, [dirty]);
}
