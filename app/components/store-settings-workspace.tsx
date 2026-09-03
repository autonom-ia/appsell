"use client";

import { useCallback, useEffect, useState } from "react";

import { canConfigureStore } from "@/lib/shell-navigation";
import {
  MIN_ACCENT_CONTRAST,
  contrastRatio,
  isAccentReadable,
  normalizeAccentColor
} from "@/lib/store-brand";
import {
  STORE_LIMITS,
  parseStoreSettings,
  type StoreSettings,
  type StoreSettingsCsrf
} from "@/lib/store-settings";

import { StoreLogoPanel } from "./store-logo-panel";

// Configurações da loja (#352): o que a loja mostra para quem chega pelo link.
// Dono e admin editam; os outros papéis não chegam aqui pelo menu, e a API
// recusa se chegarem pela URL.

interface Seller {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

type Phase = "loading" | "no_seller" | "forbidden" | "unauthorized" | "unavailable" | "error" | "ready";
type SaveState = "idle" | "saving" | "saved" | "invalid" | "contrast" | "forbidden" | "error";

interface Fields {
  display_name: string;
  tagline: string;
  about: string;
  accent_color: string;
}

const FETCH_TIMEOUT_MS = 8_000;
const DEFAULT_ACCENT = "#1e3a8a";

function fieldsFrom(settings: StoreSettings): Fields {
  return {
    display_name: settings.display_name,
    tagline: settings.tagline ?? "",
    about: settings.about ?? "",
    accent_color: settings.accent_color ?? ""
  };
}

export function StoreSettingsWorkspace({ publicOrigin }: { publicOrigin: string }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [seller, setSeller] = useState<Seller | null>(null);
  const [settings, setSettings] = useState<StoreSettings | null>(null);
  const [csrf, setCsrf] = useState<StoreSettingsCsrf | null>(null);
  const [fields, setFields] = useState<Fields>({ display_name: "", tagline: "", about: "", accent_color: "" });
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [contrastMessage, setContrastMessage] = useState("");
  const [copied, setCopied] = useState<"store" | null>(null);
  const [logoBusy, setLogoBusy] = useState(false);

  const loadSettings = useCallback(async (activeSeller: Seller): Promise<boolean> => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(`/api/sellers/${encodeURIComponent(activeSeller.id)}/settings`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (response.status === 401) { setPhase("unauthorized"); return false; }
      if (response.status === 403) { setPhase("forbidden"); return false; }
      if (response.status === 503) { setPhase("unavailable"); return false; }
      if (!response.ok) { setPhase("error"); return false; }
      const payload = await response.json() as { settings?: unknown; csrf?: StoreSettingsCsrf };
      const parsed = parseStoreSettings(payload.settings);
      if (!parsed || !payload.csrf) { setPhase("error"); return false; }
      setSettings(parsed);
      setCsrf(payload.csrf);
      setFields(fieldsFrom(parsed));
      setPhase("ready");
      return true;
    } catch {
      setPhase("error");
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    void fetch("/api/sellers", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!active) return;
        if (response.status === 401) return setPhase("unauthorized");
        if (response.status === 503) return setPhase("unavailable");
        if (!response.ok) return setPhase("error");
        const payload = await response.json() as { sellers?: Seller[] };
        const sellers = payload.sellers ?? [];
        if (sellers.length === 0) return setPhase("no_seller");
        if (!canConfigureStore(sellers)) return setPhase("forbidden");
        setSeller(sellers[0]!);
        await loadSettings(sellers[0]!);
      })
      .catch(() => { if (active) setPhase("error"); })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [loadSettings]);

  const dirty = settings !== null && (
    fields.display_name !== settings.display_name ||
    fields.tagline !== (settings.tagline ?? "") ||
    fields.about !== (settings.about ?? "") ||
    fields.accent_color !== (settings.accent_color ?? "")
  );
  const normalizedAccent = normalizeAccentColor(fields.accent_color);
  const accentInvalid = fields.accent_color.trim() !== "" && normalizedAccent === null;
  const accentUnreadable = normalizedAccent !== null && !isAccentReadable(normalizedAccent);
  const accentRatio = normalizedAccent ? contrastRatio(normalizedAccent, "#ffffff") : null;

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!seller || !csrf || saveState === "saving") return;
    if (accentInvalid) { setInvalidField("accent_color"); setSaveState("invalid"); return; }
    if (accentUnreadable) { setSaveState("contrast"); setContrastMessage(""); return; }
    setSaveState("saving");
    setInvalidField("");
    const data = new FormData();
    data.set("_csrf", csrf.update);
    data.set("display_name", fields.display_name.trim());
    data.set("tagline", fields.tagline.trim());
    data.set("about", fields.about.trim());
    data.set("accent_color", normalizedAccent ?? "");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(`/api/sellers/${encodeURIComponent(seller.id)}/settings`, {
        method: "PATCH",
        body: data,
        signal: controller.signal
      });
      if (response.status === 422) {
        const payload = await response.json().catch(() => null) as { error?: string; field?: string; message?: string } | null;
        if (payload?.error === "accent_contrast") {
          setContrastMessage(payload.message ?? "");
          setSaveState("contrast");
        } else {
          setInvalidField(payload?.field ?? "form");
          setSaveState("invalid");
        }
        return;
      }
      if (response.status === 401 || response.status === 403) return setSaveState("forbidden");
      if (!response.ok) return setSaveState("error");
      const payload = await response.json() as { settings?: unknown };
      const parsed = parseStoreSettings(payload.settings);
      if (!parsed) return setSaveState("error");
      setSettings(parsed);
      setFields(fieldsFrom(parsed));
      setSaveState("saved");
    } catch {
      setSaveState("error");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function copyStoreLink() {
    if (!settings) return;
    try {
      await navigator.clipboard.writeText(`${publicOrigin}/s/${settings.slug}`);
      setCopied("store");
    } catch {
      setCopied(null);
    }
  }

  if (phase === "loading") return <p className="empty-state" role="status">Carregando as configurações da loja…</p>;
  if (phase === "unauthorized") return <p className="empty-state" role="alert">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p>;
  if (phase === "no_seller") return <p className="empty-state">Você ainda não tem uma loja. <a href="/settings/store/new">Crie a sua</a> para configurá-la.</p>;
  if (phase === "forbidden") return <p className="empty-state" role="alert">Só o dono ou um administrador da loja pode alterar estas configurações.</p>;
  if (phase === "unavailable") return <p className="empty-state" role="alert">A API está indisponível no momento. Tente novamente em instantes.</p>;
  if (phase !== "ready" || !settings || !seller || !csrf) return <p className="empty-state" role="alert">Não foi possível carregar as configurações. Recarregue a página.</p>;

  const storeUrl = `${publicOrigin}/s/${settings.slug}`;
  const previewAccent = normalizedAccent && !accentUnreadable ? normalizedAccent : DEFAULT_ACCENT;

  return (
    <div className="store-settings">
      <form className="store-settings-card" onSubmit={(event) => void save(event)} aria-labelledby="store-identity-title">
        <div>
          <p className="eyebrow">Identidade</p>
          <h2 id="store-identity-title">Como a loja se apresenta</h2>
        </div>
        <label className="editor-field">
          <span>Nome da loja</span>
          <input
            name="display_name"
            value={fields.display_name}
            onChange={(event) => setFields({ ...fields, display_name: event.target.value })}
            minLength={STORE_LIMITS.displayName.min}
            maxLength={STORE_LIMITS.displayName.max}
            required
            aria-invalid={invalidField === "display_name"}
          />
        </label>
        <div className="editor-field">
          <span>Endereço público</span>
          <p className="store-slug"><code>{storeUrl}</code></p>
          <small className="form-note">O endereço não muda: ele já está nos links que seus alunos receberam.</small>
        </div>
        <label className="editor-field">
          <span>Frase de apresentação</span>
          <input
            name="tagline"
            value={fields.tagline}
            onChange={(event) => setFields({ ...fields, tagline: event.target.value })}
            maxLength={STORE_LIMITS.tagline.max}
            placeholder="Ex.: IA e playbooks para corretores de seguros venderem mais."
            aria-invalid={invalidField === "tagline"}
          />
          <small className="form-note">Aparece embaixo do nome na sua loja. Uma frase.</small>
        </label>
        <label className="editor-field">
          <span>Sobre quem ensina</span>
          <textarea
            name="about"
            value={fields.about}
            onChange={(event) => setFields({ ...fields, about: event.target.value })}
            maxLength={STORE_LIMITS.about.max}
            rows={3}
            placeholder="Quem você é e por que vale ouvir você. Duas ou três linhas."
            aria-invalid={invalidField === "about"}
          />
          <small className="form-note">Aparece na página de cada produto, ao lado do logo.</small>
        </label>
        <div className="editor-field">
          <span>Cor de destaque</span>
          <div className="store-accent-row">
            <input
              type="color"
              aria-label="Escolher cor de destaque"
              value={normalizedAccent ?? DEFAULT_ACCENT}
              onChange={(event) => setFields({ ...fields, accent_color: event.target.value })}
            />
            <input
              name="accent_color"
              value={fields.accent_color}
              onChange={(event) => setFields({ ...fields, accent_color: event.target.value })}
              placeholder="#1e3a8a"
              maxLength={7}
              aria-invalid={invalidField === "accent_color" || accentInvalid}
              aria-describedby="accent-help"
            />
            <span className="store-accent-swatch" style={{ background: previewAccent }} aria-hidden="true">Aa</span>
            {fields.accent_color ? <button type="button" className="secondary-button" onClick={() => setFields({ ...fields, accent_color: "" })}>Usar o padrão</button> : null}
          </div>
          <small id="accent-help" className={accentUnreadable || accentInvalid ? "form-error" : "form-note"}>
            {accentInvalid
              ? "Use o formato #rrggbb."
              : accentUnreadable && accentRatio !== null
                ? `Essa cor não contrasta o bastante com o texto branco dos botões (${accentRatio.toFixed(1)}:1; o mínimo é ${MIN_ACCENT_CONTRAST}:1). Escolha um tom mais escuro.`
                : normalizedAccent && accentRatio !== null
                  ? `Contraste ${accentRatio.toFixed(1)}:1 com texto branco. Vai para os botões de compra e para o fundo da sua marca.`
                  : "Sem cor própria, a loja usa o tom padrão da Autonom.ia."}
          </small>
        </div>
        <div className="form-feedback" aria-live="polite" aria-atomic="true">
          {saveState === "saved" ? <p className="form-note">Salvo. Sua loja já mostra as mudanças.</p> : null}
          {saveState === "invalid" ? <p className="form-error">Não foi salvo. Corrija o campo destacado{invalidField && invalidField !== "form" ? ` (${fieldLabel(invalidField)})` : ""}.</p> : null}
          {saveState === "contrast" ? <p className="form-error">{contrastMessage || "A cor escolhida não contrasta o bastante com texto branco. Escolha um tom mais escuro."}</p> : null}
          {saveState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente antes de tentar.</p> : null}
          {saveState === "error" ? <p className="form-error">Não foi possível salvar. Tente novamente.</p> : null}
        </div>
        <div className="editor-actions">
          <button className="primary-button" type="submit" disabled={!dirty || saveState === "saving" || logoBusy}>
            {saveState === "saving" ? "Salvando…" : "Salvar"}
          </button>
        </div>
      </form>

      <div className="store-settings-card">
        <StoreLogoPanel
          sellerId={seller.id}
          storeName={settings.display_name}
          logo={settings.logo}
          csrf={{ intent: csrf.logo_intent, confirm: csrf.logo_confirm, clear: csrf.logo_clear }}
          onBusyChange={setLogoBusy}
          onCommitted={() => loadSettings(seller)}
        />
      </div>

      <section className="store-settings-card" aria-labelledby="store-links-title">
        <div>
          <p className="eyebrow">Seus links</p>
          <h2 id="store-links-title">Para onde mandar as pessoas</h2>
        </div>
        <div className="store-links">
          <div>
            <strong>Sua loja</strong>
            <p className="form-note">Todos os seus produtos numa página. Para quem ainda não sabe o que quer.</p>
            <p><code>{storeUrl}</code></p>
            <div className="cover-actions">
              <button type="button" className="secondary-button" onClick={() => void copyStoreLink()}>
                {copied === "store" ? "Endereço copiado" : "Copiar endereço"}
              </button>
              <a className="secondary-button" href={storeUrl} target="_blank" rel="noreferrer">Ver minha loja</a>
            </div>
          </div>
          <div>
            <strong>Cada produto</strong>
            <p className="form-note">
              O botão <em>Compartilhar</em>, em Produtos, copia o endereço direto do produto — para pôr na sua
              própria página ou num anúncio.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

function fieldLabel(field: string) {
  return { display_name: "Nome da loja", tagline: "Frase de apresentação", about: "Sobre quem ensina", accent_color: "Cor de destaque" }[field] ?? field;
}
