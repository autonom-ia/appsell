"use client";

import { useState } from "react";

// A conta que a pagina mostra ao lado do numero. Os dois valores vivem aqui e
// no texto da secao; se a cobranca mudar, os dois mudam juntos.
const TAXA = 0.0699;
const FIXA = 1.99;
const VALORES = [97, 197, 497] as const;

function real(n: number) {
  return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function LpPreco() {
  const [valor, setValor] = useState<number>(197);
  const liquido = valor - (valor * TAXA + FIXA);

  return (
    <div className="conta">
      <div className="conta-vals" role="group" aria-label="Valor do curso no exemplo">
        {VALORES.map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={valor === v}
            onClick={() => setValor(v)}
          >
            R$ {v}
          </button>
        ))}
      </div>
      <p className="conta-linha" role="status">
        R$ {real(valor)} vendidos <b>&rarr;</b> R$ {real(liquido)} na sua conta
      </p>
    </div>
  );
}
