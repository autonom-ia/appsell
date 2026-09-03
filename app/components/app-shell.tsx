"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent, ReactNode } from "react";
import { AccessibleDialog } from "./accessible-dialog";
import { ACCESSIBLE_SELLER_ROLES, PLATFORM_ROLES } from "../../lib/rbac-roles";
import { SellBrand } from "./sell-brand";
import { canConfigureStore } from "@/lib/shell-navigation";
import { allowUnsavedNavigation, DIRTY_HISTORY_BLOCKED_EVENT, DIRTY_HISTORY_CONTINUE_EVENT, DIRTY_NAVIGATION_REQUEST_EVENT, DIRTY_STATE_EVENT } from "./use-unsaved-changes";
import {
  DEMO_ADMIN_EMAIL,
  DEMO_BUYER_EMAIL,
  DEMO_SUPERADMIN_EMAIL
} from "../../lib/demo-config";
import {
  canOpenFirstStore,
  hasPlatformGrant,
  shellAudience,
  ShellProfileContext,
  type DemoShellProfile,
  type ProfileState,
  type SessionRuntime,
  type ShellIdentity,
  type ShellSeller
} from "./shell-profile-context";

const ASSET_DISCARD_REQUEST_EVENT = "appsell:asset-discard-request";

// A criação da loja tem rota própria porque o cartão que a oferecia vivia
// dentro de /dashboard e /products — as duas telas que esta navegação esconde
// de quem ainda não tem loja.
export const NEW_STORE_PATH = "/settings/store/new";
// A conta é de toda identidade real — aluno, vendedor ou quem ainda não tem
// loja. Só a persona demo de aluno fica de fora: o proxy a devolve para a
// biblioteca antes de a tela abrir.
export const ACCOUNT_PATH = "/settings/account";

interface AssetDiscardRequestDetail {
  register(task: Promise<void>): void;
}

export type ConfirmedExitType = "navigate" | "history" | "logout";

interface AppShellProps {
  children: ReactNode;
}

// A navegação era uma lista fixa e todo mundo via tudo: o aluno era recebido
// por "Recebimentos" e concluía que o produto que comprou estava quebrado
// (#305). Agora cada audiência tem a sua, e "sem loja" é uma audiência
// própria — é por ali que o cadastro do "Vender agora" (#341) segue para
// criar a loja, então ela nunca pode ficar sem saída.
const accountNavigation = { href: ACCOUNT_PATH, label: "Minha conta", mark: "account" } as const;
const sellerNavigation = [
  { href: "/dashboard", label: "Visão geral", mark: "home" },
  { href: "/products", label: "Produtos", mark: "products" },
  { href: "/settings/store", label: "Configurações", mark: "settings" },
  { href: "/library", label: "Minha biblioteca", mark: "library" },
  accountNavigation,
] as const;
// Editor de conteúdo e suporte não configuram a loja (#352): o item some do
// menu, e a API recusa se chegarem pela URL.
const sellerNavigationLimited = sellerNavigation.filter((item) => item.mark !== "settings");
const buyerNavigation = [
  { href: "/library", label: "Minha biblioteca", mark: "library" },
  accountNavigation,
] as const;
const demoBuyerNavigation = [
  { href: "/library", label: "Minha biblioteca", mark: "library" },
] as const;
const withoutStoreNavigation = [
  { href: "/library", label: "Minha biblioteca", mark: "library" },
  { href: NEW_STORE_PATH, label: "Criar minha loja", mark: "store" },
  accountNavigation,
] as const;
const recoveryNavigation = [{ href: "/", label: "Entrada", mark: "home" }] as const;

const platformNavigation = [
  { href: "/platform/demo-inspection", label: "Inspeção demo", mark: "inspection" }
] as const;

type NavigationMark =
  | (typeof sellerNavigation)[number]["mark"]
  | (typeof withoutStoreNavigation)[number]["mark"]
  | (typeof platformNavigation)[number]["mark"];

function NavIcon({ name }: { name: NavigationMark }) {
  const paths = {
    home: <><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-7h6v7"/></>,
    products: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16M8 9h13"/></>,
    library: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></>,
    inspection: <><path d="M12 3 4.5 6v5.5c0 4.8 3.1 8 7.5 9.5 4.4-1.5 7.5-4.7 7.5-9.5V6L12 3Z"/><path d="M9.5 12.2 11.2 14l3.6-4"/></>,
    store: <><path d="M4 9.5 5.5 4.5h13L20 9.5"/><path d="M4 9.5h16v10H4Z"/><path d="M9.5 19.5v-5h5v5"/></>,
    account: <><circle cx="12" cy="8.5" r="4"/><path d="M4.5 20.5c1.2-3.6 4-5.5 7.5-5.5s6.3 1.9 7.5 5.5"/></>
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

// Quem não tem loja e não comprou nada chega pela porta do vendedor: mandá-lo
// para uma biblioteca vazia faria parecer que ele entrou no lugar errado.
function defaultHome(audience: ReturnType<typeof shellAudience>) {
  if (audience === "demo-buyer") return "/library";
  // Grant de plataforma não cria membership, então o superadmin cai em
  // "sem loja" — mas convidá-lo a abrir uma loja seria ler mal o que ele veio
  // fazer aqui.
  // Sem loja, a casa é a biblioteca — inclusive para quem só comprou um curso,
  // que é indistinguível daqui de quem ainda não criou a loja. Criar a loja
  // continua a um clique, pelo item do menu.
  if (audience === "without-store") return "/library";
  return "/dashboard";
}

// O nome da loja é o que o vendedor reconhece como "onde eu estou"; sem loja,
// o rótulo não pode fingir que existe uma.
function storeLabel(identity: ShellIdentity) {
  const [first, ...rest] = identity.sellers as readonly ShellSeller[];
  if (!first) return "Sua conta";
  return rest.length ? `${first.displayName} +${rest.length}` : first.displayName;
}

// Com nome, o rodapé identifica a pessoa e usa a linha de baixo para dizer
// onde ela está. Sem nome, a loja assume o topo e o e-mail identifica.
function storeDetail(identity: ShellIdentity) {
  const [first, ...rest] = identity.sellers as readonly ShellSeller[];
  if (!first) return identity.email;
  return rest.length ? `${first.displayName} +${rest.length}` : first.displayName;
}

// "Configurações" cobre as duas áreas de /settings, menos Minha conta, que é
// item próprio.
function navigationActive(pathname: string, href: string) {
  if (pathname === href) return true;
  return href === "/settings/store" && pathname.startsWith("/settings/") && !pathname.startsWith(ACCOUNT_PATH);
}

function accountInitials(value: string) {
  const local = value.includes("@") ? value.split("@")[0] ?? "" : value;
  const parts = local.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.length > 1
    ? `${parts[0][0]}${parts[1][0]}`
    : local.slice(0, 2);
  return letters.toUpperCase() || "??";
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
  const realIdentity = profileState.status === "ready" && !profileState.profile &&
    profileState.runtime === "identity";
  const platformMode = pathname.startsWith("/platform/");
  const audience = shellAudience(profileState);
  const platformGrant = hasPlatformGrant(profileState);
  const canOpenStore = canOpenFirstStore(profileState);
  // Sessão pronta mas sem audiência resolvida é sessão que não sabemos ler:
  // falha fechada, com o caminho de recuperação, em vez de adivinhar o papel.
  const unresolved = profileState.status === "ready" && audience === null;
  const visibleNavigation = profileState.status === "failed" || unresolved
    ? recoveryNavigation
    : profileState.status !== "ready"
      ? []
      : platformMode
        ? platformNavigation
        : audience === "demo-buyer"
          ? demoBuyerNavigation
          : audience === "without-store"
            ? (canOpenStore ? withoutStoreNavigation : buyerNavigation)
            : (canConfigureStore(profileState.status === "ready" ? profileState.identity?.sellers : null)
                ? sellerNavigation
                : sellerNavigationLimited);
  const identity = profileState.status === "ready" ? profileState.identity : null;
  // O rodapé mostrava "Sua conta / Sessão protegida" e as iniciais AU fixas no
  // código para toda identidade real: o shell não sabia sequer o e-mail.
  const accountLabel = profileState.status === "failed" || unresolved
    ? { initials: "–", title: "Perfil indisponível", detail: "Sessão não validada" }
    : demoProfile
      ? { initials: accountInitials(demoProfile.email), title: `${demoProfile.label} demo`, detail: demoProfile.email }
      : identity
        ? {
            initials: accountInitials(identity.displayName ?? identity.email),
            title: identity.displayName ?? storeLabel(identity),
            detail: identity.displayName ? storeDetail(identity) : identity.email
          }
        : { initials: "AU", title: "Ambiente demo", detail: "Sessão protegida" };
  const home = profileState.status === "failed" || unresolved
    ? "/"
    : platformMode
      ? "/platform/demo-inspection"
      : profileState.status === "ready"
        ? demoProfile?.home ?? defaultHome(audience)
        : pathname;

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
    <ShellProfileContext.Provider value={profileState}>
    <div className="app-shell">
      <a className="skip-link" href="#conteudo">Pular para o conteúdo</a>
      <aside className="app-sidebar" aria-label="Navegação principal">
        <Link className="brand" href={home} aria-label="sell. by Autonom.ia: início" onClick={(event) => guardLink(event, home)}>
          <SellBrand />
        </Link>
        <nav>
          <p className="nav-eyebrow">{profileState.status === "loading" ? "Carregando acesso" : profileState.status === "failed" || unresolved ? "Acesso indisponível" : platformMode ? "Operação de plataforma" : audience === "demo-buyer" ? "Área do aluno" : audience === "without-store" ? (platformGrant ? "Operação de plataforma" : "Comece por aqui") : "Seu negócio"}</p>
          {visibleNavigation.map((item) => {
            const active = navigationActive(pathname, item.href);
            return <Link key={item.href} href={item.href} onClick={(event) => guardLink(event, item.href)} className={active ? "nav-link active" : "nav-link"} aria-current={active ? "page" : undefined}><NavIcon name={item.mark}/><span>{item.label}</span></Link>;
          })}
          {profileState.status === "failed" || unresolved ? <div className="session-recovery" role="alert"><p>Não foi possível validar seu acesso.</p><button type="button" className="secondary-button" onClick={retryProfile}>Tentar novamente</button><Link href="/">Voltar para a entrada</Link></div> : null}
        </nav>
        <div className="sidebar-footer">
          <span className="avatar" aria-hidden="true">{accountLabel.initials}</span>
          <span><strong>{accountLabel.title}</strong><small>{accountLabel.detail}</small></span>
        </div>
        <LogoutForm onAttempt={guardLogout}/>
      </aside>
      <div className="app-content">
        <header className="mobile-topbar">
          <Link className="mobile-brand" href={home} aria-label="sell. by Autonom.ia: início" onClick={(event) => guardLink(event, home)}><SellBrand onLight /></Link>
          <LogoutForm mobile onAttempt={guardLogout}/>
        </header>
        {platformMode || (profileState.status === "ready" && !realIdentity) ? (
          <div className="environment-banner" role="note"><strong>{platformMode ? "INSPEÇÃO SUPERADMIN · LOCAL/CI · READ-ONLY" : demoProfile ? `DEMO · ${demoProfile.label} · ${demoProfile.email} · Autonom.ia Demo` : "Ambiente local, não é grant."}</strong> {platformMode ? "Sem impersonação, membership ou acesso a PII/secret." : "Cobrança, checkout e Identity real continuam desativados."}</div>
        ) : null}
        {profileState.status === "failed" || unresolved ? <div className="mobile-session-recovery" role="alert"><p>Não foi possível validar seu acesso.</p><button type="button" className="secondary-button" onClick={retryProfile}>Tentar novamente</button><Link href="/">Voltar para a entrada</Link></div> : null}
        <main id="conteudo" className="app-main" tabIndex={-1}>{children}</main>
        <nav
          className="bottom-nav"
          aria-label="Navegação móvel"
          style={{ gridTemplateColumns: `repeat(${visibleNavigation.length}, minmax(0, 1fr))` }}
        >
          {visibleNavigation.map((item) => {
            const active = navigationActive(pathname, item.href);
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
    </ShellProfileContext.Provider>
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

export function parseProfileState(payload: unknown): ProfileState {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return { status: "failed" };
  const session = payload as Record<string, unknown>;
  if (session.authenticated !== true) return { status: "failed" };
  // Unknown runtimes fall back to "local" so the conservative environment
  // banner keeps rendering instead of silently claiming real production.
  const runtime: SessionRuntime = session.runtime === "identity" ? "identity" : "local";
  const profile = session.demoProfile;
  if (profile === null) {
    const identity = parseIdentity(session.identity);
    return identity === undefined
      ? { status: "failed" }
      : { status: "ready", profile: null, identity, runtime };
  }
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) return { status: "failed" };
  const record = profile as Record<string, unknown>;
  const admin = record.persona === "admin" && record.email === DEMO_ADMIN_EMAIL &&
    record.label === "Administrador" && record.home === "/dashboard";
  const buyer = record.persona === "buyer" && record.email === DEMO_BUYER_EMAIL &&
    record.label === "Aluno" && record.home === "/library";
  const superadmin = record.persona === "superadmin" && record.email === DEMO_SUPERADMIN_EMAIL &&
    record.label === "Superadmin simulado" && record.home === "/platform/demo-inspection";
  return admin || buyer || superadmin
    ? { status: "ready", profile: record as unknown as DemoShellProfile, identity: null, runtime }
    : { status: "failed" };
}

// undefined = payload malformado (sessão inválida). null = servidor não
// conseguiu resolver o papel; o shell trata como não validada em vez de
// adivinhar, porque adivinhar aqui é mostrar o menu do vendedor a um aluno.
function parseIdentity(value: unknown): ShellIdentity | null | undefined {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const email = record.email;
  const roles = record.platformRoles;
  const sellers = record.sellers;
  if (typeof email !== "string" || email.length === 0) return undefined;
  if (!Array.isArray(roles) || roles.some((role) => typeof role !== "string")) return undefined;
  if (!Array.isArray(sellers)) return undefined;
  const parsed: ShellSeller[] = [];
  for (const seller of sellers) {
    if (!seller || typeof seller !== "object" || Array.isArray(seller)) return undefined;
    const item = seller as Record<string, unknown>;
    if (typeof item.id !== "string" || item.id.length === 0 ||
        typeof item.slug !== "string" || item.slug.length === 0 ||
        typeof item.displayName !== "string" || item.displayName.length === 0 ||
        typeof item.role !== "string" || !ACCESSIBLE_SELLER_ROLES.has(item.role)) {
      return undefined;
    }
    parsed.push({ id: item.id, slug: item.slug, displayName: item.displayName, role: item.role });
  }
  const displayName = record.displayName;
  return {
    email,
    displayName: typeof displayName === "string" && displayName.trim() ? displayName.trim() : null,
    emailVerified: record.emailVerified === true,
    platformRoles: (roles as string[]).filter((role) => PLATFORM_ROLES.has(role)),
    sellers: parsed
  };
}
