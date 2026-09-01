"use client";

import { useState, type FormEvent } from "react";

const CAMPOS = [
  { id: "nome", rotulo: "Nome", tipo: "text", auto: "name", dica: "Como podemos te chamar", erro: "Escreva o seu nome." },
  { id: "email", rotulo: "E-mail", tipo: "email", auto: "email", dica: "voce@empresa.com.br", erro: "Escreva um e-mail válido, no formato voce@empresa.com.br." },
  { id: "loja", rotulo: "Empresa ou loja", tipo: "text", auto: "organization", dica: "Como o aluno te conhece", erro: "Escreva o nome da sua empresa ou loja." }
] as const;

export function LpContato() {
  const [erro, setErro] = useState("");
  const [ok, setOk] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setErro("");
    setOk("");
    const form = evento.currentTarget;

    const obrigatorios = [
      ...CAMPOS.map((c) => ({ id: c.id as string, erro: c.erro as string })),
      { id: "mensagem", erro: "Conte em pelo menos vinte caracteres o que você vende." }
    ];
    for (const campo of obrigatorios) {
      const el = form.elements.namedItem(campo.id) as HTMLInputElement | HTMLTextAreaElement | null;
      el?.removeAttribute("aria-invalid");
      if (el && !el.checkValidity()) {
        setErro(campo.erro);
        el.setAttribute("aria-invalid", "true");
        el.focus();
        return;
      }
    }

    setEnviando(true);
    try {
      const resposta = await fetch("/api/contato", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form).entries()))
      });
      if (!resposta.ok) throw new Error(String(resposta.status));
      form.reset();
      setOk("Recebemos a sua mensagem. Respondemos em até um dia útil.");
    } catch {
      // Fallback honesto: se a mensagem nao sai daqui, a pessoa precisa saber
      // por onde falar, em vez de acreditar que alguem vai responder.
      setErro("Não conseguimos enviar agora. Escreva para contato@autonomia.site e a gente responde do mesmo jeito.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={enviar} noValidate>
      <div className="form">
        {CAMPOS.map((campo, i) => (
          <div className={i === 2 ? "f full" : "f"} key={campo.id}>
            <label htmlFor={campo.id}>
              {campo.rotulo} <i aria-hidden="true">*</i>
            </label>
            <input
              id={campo.id}
              name={campo.id}
              type={campo.tipo}
              autoComplete={campo.auto}
              required
              placeholder={campo.dica}
            />
          </div>
        ))}
        <div className="f full">
          <label htmlFor="mensagem">
            Mensagem <i aria-hidden="true">*</i>
          </label>
          <textarea
            id="mensagem"
            name="mensagem"
            required
            minLength={20}
            placeholder="Conte o que você vende e como entrega o conteúdo hoje."
          />
          <span className="hint">Quanto mais concreto, melhor a primeira resposta.</span>
        </div>
      </div>

      <div className="isca" aria-hidden="true">
        <label htmlFor="site_empresa">Site da empresa</label>
        <input id="site_empresa" name="site_empresa" type="text" tabIndex={-1} autoComplete="off" />
      </div>

      <p className="f-erro" role="alert">{erro}</p>
      <p className="f-ok" role="status">{ok}</p>

      <div className="envio">
        <button type="submit" className="cta" disabled={enviando}>
          {enviando ? "Enviando..." : "Enviar mensagem"}
        </button>
        <p>
          Ao enviar, você concorda com a <a href="/privacidade">Política de Privacidade</a>. Usamos os dados só para responder.
        </p>
      </div>
    </form>
  );
}
