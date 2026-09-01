"use client";

import { createContext, useContext } from "react";
import type { DemoProfile } from "../../lib/demo-config";

export type SessionRuntime = "identity" | "local";
export type DemoShellProfile = Pick<DemoProfile, "persona" | "email" | "label" | "home">;

export interface ShellSeller {
  readonly id: string;
  readonly slug: string;
  readonly displayName: string;
  readonly role: string;
}

export interface ShellIdentity {
  readonly email: string;
  readonly emailVerified: boolean;
  readonly platformRoles: readonly string[];
  readonly sellers: readonly ShellSeller[];
}

export type ProfileState =
  | { status: "loading" }
  | {
      status: "ready";
      profile: DemoShellProfile | null;
      identity: ShellIdentity | null;
      runtime: SessionRuntime;
    }
  | { status: "failed" };

// O papel de quem está logado, do jeito que a navegação precisa dele. São três
// estados, não dois: quem ainda não tem loja não é a mesma coisa que comprador,
// e tratar os dois como iguais fecharia a porta de entrada do vendedor.
export type ShellAudience = "seller" | "without-store" | "demo-buyer";

export function shellAudience(state: ProfileState): ShellAudience | null {
  if (state.status !== "ready") return null;
  if (state.profile) return state.profile.persona === "buyer" ? "demo-buyer" : "seller";
  if (!state.identity) return null;
  return state.identity.sellers.length > 0 ? "seller" : "without-store";
}

export function hasPlatformGrant(state: ProfileState): boolean {
  if (state.status !== "ready") return false;
  if (state.profile) return state.profile.persona === "superadmin";
  return state.identity?.platformRoles.includes("platform_superadmin") ?? false;
}

// AppShell resolves /api/session exactly once and shares the result here so
// pages never issue a redundant session request of their own.
export const ShellProfileContext = createContext<ProfileState>({ status: "loading" });

export function useShellProfile(): ProfileState {
  return useContext(ShellProfileContext);
}
