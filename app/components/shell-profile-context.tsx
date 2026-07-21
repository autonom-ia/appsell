"use client";

import { createContext, useContext } from "react";
import type { DemoProfile } from "../../lib/demo-config";

export type SessionRuntime = "identity" | "local";
export type DemoShellProfile = Pick<DemoProfile, "persona" | "email" | "label" | "home">;
export type ProfileState =
  | { status: "loading" }
  | { status: "ready"; profile: DemoShellProfile | null; runtime: SessionRuntime }
  | { status: "failed" };

// AppShell resolves /api/session exactly once and shares the result here so
// pages never issue a redundant session request of their own.
export const ShellProfileContext = createContext<ProfileState>({ status: "loading" });

export function useShellProfile(): ProfileState {
  return useContext(ShellProfileContext);
}
