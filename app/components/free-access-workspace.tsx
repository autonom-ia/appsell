"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";

type AccessState = "idle" | "submitting" | "success" | "error";

export function FreeAccessWorkspace({
  csrf,
  sellerId,
  productId,
  title,
  libraryHref = "/library"
}: {
  csrf: string;
  sellerId: string;
  productId: string;
  title: string;
  libraryHref?: string;
}) {
  const [state, setState] = useState<AccessState>("idle");
  const [message, setMessage] = useState("");
  const started = useRef(false);

  async function enroll() {
    if (state === "submitting" || state === "success") return;
    setState("submitting");
    setMessage("");
    const data = new FormData();
    data.set("_csrf", csrf);
    data.set("idempotency_key", crypto.randomUUID());
    data.set("seller_id", sellerId);
    data.set("product_id", productId);
    try {
      const response = await fetch("/api/library/enroll", { method: "POST", body: data });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (response.status === 401) {
        window.location.href = `/auth/login?next=${encodeURIComponent(window.location.pathname)}`;
        return;
      }
      if (!response.ok) {
        setState("error");
        setMessage(payload?.error === "offer_requires_checkout"
          ? "Este produto passou a exigir checkout. Volte à página do produto para continuar."
          : "Não foi possível liberar o acesso agora. Tente novamente.");
        return;
      }
      setState("success");
    } catch {
      setState("error");
      setMessage("Não foi possível liberar o acesso agora. Tente novamente.");
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void enroll();
  // The product/account identifiers are immutable for this page mount. The
  // guard prevents React Strict Mode from issuing a duplicate request.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void enroll();
  }

  return <div className="checkout-card free-access-card">
    <p className="eyebrow">Acesso gratuito</p>
    <h2>Entrar em {title}</h2>
    {state === "success"
      ? <>
          <p className="form-success" role="status">Acesso liberado. O produto já está na sua biblioteca.</p>
          <Link className="primary-button" href={libraryHref}>Abrir minha biblioteca</Link>
        </>
      : <>
          <p>Você não pagará nada. Estamos liberando o acesso nesta conta.</p>
          <form onSubmit={submit}>
            <button className="primary-button" type="submit" disabled={state === "submitting"}>{state === "submitting" ? "Liberando acesso…" : "Tentar novamente"}</button>
          </form>
          {state === "error" ? <p className="form-error" role="alert">{message}</p> : null}
        </>}
  </div>;
}
