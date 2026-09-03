"use client";

import { useEffect, useRef, useState } from "react";

import { STORE_LIMITS } from "@/lib/store-settings";

// A promessa da página de vendas (#352): a frase que abre a página do produto.
// Vazia, a página usa o nome do produto. Salva à parte do rascunho, com a
// versão esperada — trocar a frase não disputa com o autosave.

type State = "idle" | "saving" | "saved" | "invalid" | "conflict" | "forbidden" | "unavailable" | "error";

const SAVE_TIMEOUT_MS = 10_000;

export function ProductHeadlinePanel({
  sellerId,
  productId,
  headline,
  productVersion,
  archived,
  csrf,
  onCommitted
}: {
  sellerId: string;
  productId: string;
  headline: string | null;
  productVersion: number;
  archived: boolean;
  csrf: string;
  onCommitted: () => Promise<boolean>;
}) {
  const [value, setValue] = useState(headline ?? "");
  const [state, setState] = useState<State>("idle");
  // O servidor é a fonte: depois de recarregar o produto, o campo acompanha.
  // Ajuste de estado durante o render (não num efeito), como a documentação
  // do React recomenda para "prop mudou, estado derivado acompanha".
  const [seenHeadline, setSeenHeadline] = useState(headline);
  if (seenHeadline !== headline) {
    setSeenHeadline(headline);
    setValue(headline ?? "");
  }
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const trimmed = value.trim();
  const dirty = trimmed !== (headline ?? "");
  const tooShort = trimmed.length > 0 && trimmed.length < STORE_LIMITS.headline.min;

  async function save() {
    if (state === "saving" || !dirty || tooShort) return;
    setState("saving");
    const data = new FormData();
    data.set("_csrf", csrf);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("expected_product_version", String(productVersion));
    data.set("headline", trimmed);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), SAVE_TIMEOUT_MS);
    try {
      const response = await fetch(
        `/api/sellers/${encodeURIComponent(sellerId)}/products/${encodeURIComponent(productId)}/headline`,
        { method: "PATCH", body: data, signal: controller.signal }
      );
      if (!mounted.current) return;
      if (response.status === 409) return setState("conflict");
      if (response.status === 401 || response.status === 403) return setState("forbidden");
      if (response.status === 422 || response.status === 413) return setState("invalid");
      if (response.status === 503) return setState("unavailable");
      if (!response.ok) return setState("error");
      const reloaded = await onCommitted();
      if (mounted.current) setState(reloaded ? "saved" : "error");
    } catch {
      if (mounted.current) setState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  return (
    <section className="cover-panel" aria-labelledby="headline-heading">
      <h3 id="headline-heading">Promessa da página de vendas</h3>
      <p className="form-note">
        A frase que abre a página do produto. Diga o resultado, não o assunto: “Feche mais apólices usando IA
        no dia a dia” vende mais do que “Curso de IA”. Vazia, a página usa o nome do produto.
      </p>
      <label className="editor-field">
        <span className="sr-only">Promessa</span>
        <input
          name="headline"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={STORE_LIMITS.headline.max}
          disabled={archived || state === "saving"}
          placeholder="Ex.: Feche mais apólices usando IA no dia a dia, sem virar programador."
          aria-invalid={tooShort || state === "invalid"}
        />
      </label>
      <div className="cover-actions">
        <button type="button" className="secondary-button" disabled={!dirty || tooShort || archived || state === "saving"} onClick={() => void save()}>
          {state === "saving" ? "Salvando…" : "Salvar promessa"}
        </button>
      </div>
      <div className="form-feedback" aria-live="polite" aria-atomic="true">
        {tooShort ? <p className="form-error">A promessa precisa ter pelo menos {STORE_LIMITS.headline.min} caracteres, ou ficar vazia.</p> : null}
        {state === "saved" ? <p className="form-note">Salvo. A página do produto já mostra a frase.</p> : null}
        {state === "invalid" ? <p className="form-error">Não foi salvo. Revise o texto: sem espaços nas pontas, até {STORE_LIMITS.headline.max} caracteres.</p> : null}
        {state === "conflict" ? <p className="form-error">O produto mudou em outra aba. Recarregue antes de tentar de novo.</p> : null}
        {state === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente antes de tentar.</p> : null}
        {state === "unavailable" ? <p className="form-note">Não deu para salvar agora. Tente novamente; nada foi alterado.</p> : null}
        {state === "error" ? <p className="form-error">Não foi possível concluir. Tente novamente.</p> : null}
      </div>
    </section>
  );
}
