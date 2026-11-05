"use client";

import { useState } from "react";

type Estado = "pronto" | "enviando" | "feito" | "sem_link" | "falhou";

const MENSAGENS: Record<Exclude<Estado, "pronto" | "enviando">, string> = {
  feito: "Pronto. Você não vai mais receber estes e-mails.",
  sem_link: "Este link está incompleto. Abra de novo o link do e-mail que você recebeu.",
  falhou: "Não conseguimos registrar agora. Tente de novo em alguns minutos."
};

function tokenDoFragmento(): string | null {
  const fragmento = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const token = new URLSearchParams(fragmento).get("t");
  return token && token.length >= 16 && token.length <= 128 ? token : null;
}

export function ConfirmarDescadastro() {
  const [estado, setEstado] = useState<Estado>("pronto");

  async function confirmar() {
    const token = tokenDoFragmento();
    if (!token) {
      setEstado("sem_link");
      return;
    }
    setEstado("enviando");
    try {
      const resposta = await fetch("/api/email/unsubscribe", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
        referrerPolicy: "no-referrer",
        cache: "no-store"
      });
      setEstado(resposta.ok ? "feito" : "falhou");
    } catch {
      setEstado("falhou");
    }
  }

  if (estado === "pronto" || estado === "enviando") {
    return (
      <button
        type="button"
        className="legal-voltar"
        style={{ background: "var(--navy)", border: 0, cursor: "pointer", padding: "0 20px" }}
        onClick={confirmar}
        disabled={estado === "enviando"}
        aria-busy={estado === "enviando"}
      >
        {estado === "enviando" ? "Registrando…" : "Parar de receber"}
      </button>
    );
  }
  return <p role="status">{MENSAGENS[estado]}</p>;
}
