"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent, ReactNode } from "react";
import { AccessibleDialog } from "./accessible-dialog";
import { allowUnsavedNavigation, DIRTY_HISTORY_BLOCKED_EVENT, DIRTY_HISTORY_CONTINUE_EVENT, DIRTY_NAVIGATION_REQUEST_EVENT, DIRTY_STATE_EVENT } from "./use-unsaved-changes";
import {
  DEMO_ADMIN_EMAIL,
  DEMO_BUYER_EMAIL,
  DEMO_SUPERADMIN_EMAIL,
  type DemoProfile
} from "../../lib/demo-config";

type DemoShellProfile = Pick<DemoProfile, "persona" | "email" | "label" | "home">;
type ProfileState =
  | { status: "loading" }
  | { status: "ready"; profile: DemoShellProfile | null }
  | { status: "failed" };

const ASSET_DISCARD_REQUEST_EVENT = "appsell:asset-discard-request";

interface AssetDiscardRequestDetail {
  register(task: Promise<void>): void;
}

export type ConfirmedExitType = "navigate" | "history" | "logout";

interface AppShellProps {
  children: ReactNode;
}

const navigation = [
  { href: "/dashboard", label: "Visão geral", mark: "home" },
  { href: "/products", label: "Produtos", mark: "products" },
  { href: "/library", label: "Minha biblioteca", mark: "library" },
] as const;
const recoveryNavigation = [{ href: "/", label: "Entrada", mark: "home" }] as const;

const platformNavigation = [
  { href: "/platform/demo-inspection", label: "Inspeção demo", mark: "inspection" }
] as const;

type NavigationMark = (typeof navigation)[number]["mark"] | (typeof platformNavigation)[number]["mark"];

function NavIcon({ name }: { name: NavigationMark }) {
  const paths = {
    home: <><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-7h6v7"/></>,
    products: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16M8 9h13"/></>,
    library: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></>,
    inspection: <><path d="M12 3 4.5 6v5.5c0 4.8 3.1 8 7.5 9.5 4.4-1.5 7.5-4.7 7.5-9.5V6L12 3Z"/><path d="M9.5 12.2 11.2 14l3.6-4"/></>
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function readCsrfCookie() {
  const prefix = `${process.env.NODE_ENV === "production" ? "__Host-appsell_csrf" : "appsell_csrf"}=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

function LogoutForm({ mobile = false, onAttempt }: { mobile?: boolean; onAttempt?: (event: FormEvent<HTMLFormElement>) => boolean }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    if (onAttempt?.(event) === false) return;
    event.preventDefault();
    if (submitting) return;
    // React clears SyntheticEvent.currentTarget after the synchronous handler
    // returns. Keep the concrete form across the asynchronous upload-discard
    // gate so the signed logout body cannot silently degrade to an empty form.
    const form = event.currentTarget;
    const input = form.elements.namedItem("_csrf");
    const token = readCsrfCookie();
    if (!(input instanceof HTMLInputElement) || !token) {
      setError("Não foi possível validar a saída. Recarregue a página.");
      return;
    }
    input.value = token;
    // Snapshot the signed body before setState can rerender the uncontrolled
    // hidden field back to its empty default while the discard gate awaits.
    const logoutBody = new URLSearchParams({ _csrf: token });
    setSubmitting(true);
    setError("");
    try {
      await discardActiveAssetUploads();
      // A native form POST inherits the global no-referrer policy and Chromium
      // serializes its Origin as "null". Fetch keeps the exact same-origin
      // Origin header required by the server while the CSRF token stays bound
      // to this opaque session.
      const response = await fetch("/auth/logout", {
        method: "POST",
        body: logoutBody,
        credentials: "same-origin",
        redirect: "follow"
      });
      if (!response.ok || !response.redirected) throw new Error("logout_failed");
      const destination = new URL(response.url);
      if (destination.origin !== window.location.origin || destination.pathname !== "/") {
        throw new Error("logout_redirect_invalid");
      }
      window.location.replace(destination.href);
    } catch {
      setSubmitting(false);
      setError("Não foi possível sair com segurança. Tente novamente.");
    }
  }
  return <form action="/auth/logout" method="post" onSubmit={(event) => void submit(event)}><input type="hidden" name="_csrf" defaultValue=""/><button className={mobile ? "mobile-logout" : "logout-button"} type="submit" aria-label={mobile ? "Sair com segurança" : undefined} disabled={submitting}>{submitting ? "Saindo…" : mobile ? "Sair" : "Sair com segurança"}</button>{error ? <span className="form-error" role="alert">{error}</span> : null}<noscript><small>Ative JavaScript para assinar o logout com segurança.</small></noscript></form>;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [pageDirty, setPageDirty] = useState(false);
  const [profileState, setProfileState] = useState<ProfileState>({ status: "loading" });
  const [pendingAction, setPendingAction] = useState<{ type: "navigate"; href: string } | { type: "logout"; form: HTMLFormElement } | { type: "history" } | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [discardError, setDiscardError] = useState("");
  const cancelDialogButton = useRef<HTMLButtonElement>(null);
  const dirtyDialogTrigger = useRef<HTMLElement | null>(null);
  const allowNextLogout = useRef(false);
  const demoProfile = profileState.status === "ready" ? profileState.profile : undefined;
  const platformMode = pathname.startsWith("/platform/");
  const visibleNavigation = profileState.status === "failed"
    ? recoveryNavigation
    : profileState.status !== "ready"
      ? []
      : platformMode
        ? platformNavigation
        : demoProfile?.persona === "buyer"
          ? navigation.filter((item) => item.href === "/library")
          : navigation;
  const home = profileState.status === "failed"
    ? "/"
    : platformMode
      ? "/platform/demo-inspection"
      : profileState.status === "ready" ? demoProfile?.home ?? "/dashboard" : pathname;

  const loadProfile = useCallback(async (signal?: AbortSignal) => {
    const controller = new AbortController();
    const abortFromParent = () => controller.abort();
    signal?.addEventListener("abort", abortFromParent, { once: true });
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch("/api/session", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("session_profile_unavailable");
      const payload: unknown = await response.json();
      if (!controller.signal.aborted) setProfileState(parseProfileState(payload));
    } catch {
      if (!signal?.aborted) setProfileState({ status: "failed" });
    } finally {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", abortFromParent);
    }
  }, []);

  function retryProfile() {
    setProfileState({ status: "loading" });
    void loadProfile();
  }

  useEffect(() => {
    const controller = new AbortController();
    const start = window.setTimeout(() => void loadProfile(controller.signal), 0);
    return () => {
      window.clearTimeout(start);
      controller.abort();
    };
  }, [loadProfile]);

  useEffect(() => {
    const updateDirtyState = (event: Event) => {
      const detail = (event as CustomEvent<{ dirty?: boolean }>).detail;
      setPageDirty(detail?.dirty === true);
    };
    window.addEventListener(DIRTY_STATE_EVENT, updateDirtyState);
    const requestNavigation = (event: Event) => {
      const detail = (event as CustomEvent<{ href?: string; trigger?: HTMLElement }>).detail;
      if (!detail?.href) return;
      dirtyDialogTrigger.current = detail.trigger ?? null;
      setPendingAction({ type: "navigate", href: detail.href });
    };
    const blockHistory = () => {
      dirtyDialogTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPendingAction({ type: "history" });
    };
    window.addEventListener(DIRTY_NAVIGATION_REQUEST_EVENT, requestNavigation);
    window.addEventListener(DIRTY_HISTORY_BLOCKED_EVENT, blockHistory);
    return () => {
      window.removeEventListener(DIRTY_STATE_EVENT, updateDirtyState);
      window.removeEventListener(DIRTY_NAVIGATION_REQUEST_EVENT, requestNavigation);
      window.removeEventListener(DIRTY_HISTORY_BLOCKED_EVENT, blockHistory);
    };
  }, []);

  function guardLink(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (!pageDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    dirtyDialogTrigger.current = event.currentTarget;
    setPendingAction({ type: "navigate", href });
  }

  function guardLogout(event: FormEvent<HTMLFormElement>) {
    if (allowNextLogout.current) {
      allowNextLogout.current = false;
      return true;
    }
    if (!pageDirty) return true;
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    dirtyDialogTrigger.current = submitter instanceof HTMLElement ? submitter : null;
    setPendingAction({ type: "logout", form: event.currentTarget });
    return false;
  }

  function closeDirtyDialog() {
    if (discarding) return;
    setPendingAction(null);
    setDiscardError("");
    queueMicrotask(() => dirtyDialogTrigger.current?.focus());
  }

  async function discardAndContinue() {
    const action = pendingAction;
    if (!action) return;
    setDiscarding(true);
    setDiscardError("");
    try {
      await continueConfirmedExit(action.type, {
        history: () => {
          window.dispatchEvent(new Event(DIRTY_HISTORY_CONTINUE_EVENT));
        },
        navigate: () => {
          allowUnsavedNavigation();
          router.push(action.type === "navigate" ? action.href : pathname);
        },
        logout: () => {
          if (action.type !== "logout") return;
          allowUnsavedNavigation();
          allowNextLogout.current = true;
          action.form.requestSubmit();
        }
      });
    } catch {
      setDiscarding(false);
      setDiscardError("Não foi possível concluir a operação com segurança. Tente novamente antes de sair.");
      return;
    }
    setDiscarding(false);
    setPendingAction(null);
    setPageDirty(false);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#conteudo">Pular para o conteúdo</a>
      <aside className="app-sidebar" aria-label="Navegação principal">
        <Link className="brand" href={home} aria-label="Autonom.ia Sell — início" onClick={(event) => guardLink(event, home)}>
          <span className="brand-mark" aria-hidden="true">a</span>
          <span><strong>Autonom.ia <span>Sell</span></strong><small>Produtos digitais</small></span>
        </Link>
        <nav>
          <p className="nav-eyebrow">{profileState.status === "loading" ? "Carregando acesso" : profileState.status === "failed" ? "Acesso indisponível" : platformMode ? "Operação de plataforma" : demoProfile?.persona === "buyer" ? "Área do aluno" : "Seu negócio"}</p>
          {visibleNavigation.map((item) => {
            const active = pathname === item.href;
            return <Link key={item.href} href={item.href} onClick={(event) => guardLink(event, item.href)} className={active ? "nav-link active" : "nav-link"} aria-current={active ? "page" : undefined}><NavIcon name={item.mark}/><span>{item.label}</span></Link>;
          })}
          {profileState.status === "failed" ? <div className="session-recovery" role="alert"><p>Não foi possível validar seu acesso.</p><button type="button" className="secondary-button" onClick={retryProfile}>Tentar novamente</button><Link href="/">Voltar para a entrada</Link></div> : null}
        </nav>
        <div className="sidebar-footer">
          <span className="avatar" aria-hidden="true">AU</span>
          <span><strong>{demoProfile ? `${demoProfile.label} demo` : profileState.status === "failed" ? "Perfil indisponível" : "Ambiente demo"}</strong><small>{demoProfile?.email ?? "Sessão protegida"}</small></span>
        </div>
        <LogoutForm onAttempt={guardLogout}/>
      </aside>
      <div className="app-content">
        <header className="mobile-topbar">
          <Link className="mobile-brand" href={home} aria-label="Autonom.ia Sell — início" onClick={(event) => guardLink(event, home)}><span className="brand-mark" aria-hidden="true">a</span><strong>Autonom.ia <span>Sell</span></strong></Link>
          <LogoutForm mobile onAttempt={guardLogout}/>
        </header>
        <div className="environment-banner" role="note"><strong>{platformMode ? "INSPEÇÃO SUPERADMIN · LOCAL/CI · READ-ONLY" : demoProfile ? `DEMO · ${demoProfile.label} · ${demoProfile.email} · Autonom.ia Demo` : "Ambiente local, não é grant."}</strong> {platformMode ? "Sem impersonação, membership ou acesso a PII/secret." : "Cobrança, checkout e Identity real continuam desativados."}</div>
        {profileState.status === "failed" ? <div className="mobile-session-recovery" role="alert"><p>Não foi possível validar seu acesso.</p><button type="button" className="secondary-button" onClick={retryProfile}>Tentar novamente</button><Link href="/">Voltar para a entrada</Link></div> : null}
        <main id="conteudo" className="app-main" tabIndex={-1}>{children}</main>
        <nav
          className="bottom-nav"
          aria-label="Navegação móvel"
          style={{ gridTemplateColumns: `repeat(${visibleNavigation.length}, minmax(0, 1fr))` }}
        >
          {visibleNavigation.map((item) => {
            const active = pathname === item.href;
            return <Link key={item.href} href={item.href} onClick={(event) => guardLink(event, item.href)} className={active ? "active" : ""} aria-current={active ? "page" : undefined}><NavIcon name={item.mark}/><span>{item.label}</span></Link>;
          })}
        </nav>
      </div>
      {pendingAction ? (
        <AccessibleDialog titleId="dirty-dialog-title" descriptionId="dirty-dialog-description" initialFocusRef={cancelDialogButton} returnFocusRef={dirtyDialogTrigger} onClose={closeDirtyDialog}>
            <p className="eyebrow">Operação pendente</p>
            <h2 id="dirty-dialog-title">Sair desta edição?</h2>
            <p id="dirty-dialog-description">Alterações locais não salvas serão descartadas. Operações já enviadas serão concluídas ou canceladas com segurança antes da saída.</p>
            {discardError ? <p className="form-error" role="alert">{discardError}</p> : null}
            <div className="dialog-actions">
              <button ref={cancelDialogButton} type="button" className="secondary-button" onClick={closeDirtyDialog} disabled={discarding}>Continuar editando</button>
              <button type="button" className="danger-button" onClick={() => void discardAndContinue()} disabled={discarding}>{discarding ? "Preparando saída segura…" : "Confirmar saída"}</button>
            </div>
        </AccessibleDialog>
      ) : null}
    </div>
  );
}

export async function discardActiveAssetUploads(target: EventTarget = window): Promise<void> {
  const tasks: Promise<void>[] = [];
  const detail: AssetDiscardRequestDetail = { register: (task) => tasks.push(task) };
  target.dispatchEvent(new CustomEvent<AssetDiscardRequestDetail>(ASSET_DISCARD_REQUEST_EVENT, { detail }));
  const results = await Promise.allSettled(tasks);
  const failures = results.filter((result): result is PromiseRejectedResult => result.status === "rejected");
  if (failures.length > 0) throw new AggregateError(failures.map((failure) => failure.reason), "asset_discard_failed");
}

export async function continueConfirmedExit(
  type: ConfirmedExitType,
  continuations: Record<ConfirmedExitType, () => void>,
  target: EventTarget = window
): Promise<void> {
  await discardActiveAssetUploads(target);
  continuations[type]();
}

function parseProfileState(payload: unknown): ProfileState {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return { status: "failed" };
  if ((payload as Record<string, unknown>).authenticated !== true) return { status: "failed" };
  const profile = (payload as Record<string, unknown>).demoProfile;
  if (profile === null) return { status: "ready", profile: null };
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) return { status: "failed" };
  const record = profile as Record<string, unknown>;
  const admin = record.persona === "admin" && record.email === DEMO_ADMIN_EMAIL &&
    record.label === "Administrador" && record.home === "/dashboard";
  const buyer = record.persona === "buyer" && record.email === DEMO_BUYER_EMAIL &&
    record.label === "Aluno" && record.home === "/library";
  const superadmin = record.persona === "superadmin" && record.email === DEMO_SUPERADMIN_EMAIL &&
    record.label === "Superadmin simulado" && record.home === "/platform/demo-inspection";
  return admin || buyer || superadmin
    ? { status: "ready", profile: record as unknown as DemoShellProfile }
    : { status: "failed" };
}
