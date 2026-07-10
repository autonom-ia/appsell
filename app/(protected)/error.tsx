"use client";

import Link from "next/link";

export default function ProtectedError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section className="session-error" role="alert" aria-labelledby="protected-error-title">
      <span className="blocked-icon" aria-hidden="true">!</span>
      <h1 id="protected-error-title">Não foi possível abrir esta área.</h1>
      <p>Sua sessão não pôde ser validada agora. Tente novamente ou volte à entrada segura.</p>
      <div className="error-actions">
        <button className="primary-button" type="button" onClick={reset}>Tentar novamente</button>
        <Link className="secondary-button" href="/">Voltar ao início</Link>
      </div>
    </section>
  );
}
