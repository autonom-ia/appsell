import { describe, expect, it } from "vitest";

import {
  atribuicaoDaUrl,
  JANELA_SEGUNDOS,
  lerAtribuicao,
  serializarAtribuicao,
  uuidCanonico
} from "@/lib/tracking/attribution";
import {
  aceitouOTextoAtual,
  lerEscolha,
  precisaPerguntar,
  serializarEscolha
} from "@/lib/tracking/consent";
import { apagarCookie, cookieDoNavegador, valorDoCookie } from "@/lib/tracking/cookies";
import { atribuicaoLiberada } from "@/lib/tracking/pilot";
import { atribuicaoDoPedido } from "@/lib/tracking/request-attribution";

const AGORA = 1_793_000_000;
const LINK = "0f0e0d0c-0b0a-4908-8706-050403020100";

function cookies(valores: Record<string, string>) {
  return {
    get: (nome: string) => (nome in valores ? { value: valores[nome]! } : undefined)
  };
}

describe("atribuição da URL", () => {
  it("lê os campos UTM e o link da loja", () => {
    const lida = atribuicaoDaUrl(
      new URLSearchParams(`utm_source=insta&utm_campaign=black%20friday&sl=${LINK.toUpperCase()}`),
      AGORA
    );
    expect(lida).toEqual({
      utm: { utm_source: "insta", utm_campaign: "black friday" },
      link: LINK,
      visitadoEm: AGORA
    });
  });

  it("sem UTM nem link, não há atribuição", () => {
    expect(atribuicaoDaUrl(new URLSearchParams("q=curso&sl=nao-uuid"), AGORA)).toBeNull();
  });

  it("descarta campo vazio, longo demais ou com caractere de controle", () => {
    const lida = atribuicaoDaUrl(
      new URLSearchParams({
        utm_source: "   ",
        utm_medium: "x".repeat(101),
        utm_term: "a\u0007b",
        utm_content: "ok"
      }),
      AGORA
    );
    expect(lida?.utm).toEqual({ utm_content: "ok" });
  });

  it("confere o UUID do link sem regex", () => {
    expect(uuidCanonico(LINK)).toBe(LINK);
    for (const ruim of ["", "0f0e0d0c0b0a490887060504030201000", "0f0e0d0c-0b0a-4908-8706-05040302010g"]) {
      expect(uuidCanonico(ruim)).toBeNull();
    }
  });
});

describe("cookie de atribuição", () => {
  const atribuicao = { utm: { utm_source: "a;b=c", utm_medium: "e-mail" }, link: LINK, visitadoEm: AGORA };

  it("vai e volta sem ; nem espaço no valor", () => {
    const valor = serializarAtribuicao(atribuicao);
    expect(valor).not.toContain(";");
    expect(valor).not.toContain(" ");
    expect(lerAtribuicao(valor, AGORA + 10)).toEqual(atribuicao);
  });

  it("vale por 7 dias e recusa data no futuro", () => {
    const valor = serializarAtribuicao(atribuicao);
    expect(lerAtribuicao(valor, AGORA + JANELA_SEGUNDOS)).not.toBeNull();
    expect(lerAtribuicao(valor, AGORA + JANELA_SEGUNDOS + 1)).toBeNull();
    expect(lerAtribuicao(valor, AGORA - 3600)).toBeNull();
  });

  it("recusa valor estranho", () => {
    for (const ruim of [undefined, "", "%E0%A4%A", "utm_source=x", "utm_source=x&t=abc"]) {
      expect(lerAtribuicao(ruim, AGORA)).toBeNull();
    }
  });
});

describe("escolha sobre o cookie", () => {
  it("sem escolha, pergunta e não aceita", () => {
    expect(precisaPerguntar(undefined)).toBe(true);
    expect(aceitouOTextoAtual(undefined)).toBe(false);
  });

  it("aceite da versão atual libera; recusa não", () => {
    expect(aceitouOTextoAtual(serializarEscolha("accepted"))).toBe(true);
    expect(precisaPerguntar(serializarEscolha("accepted"))).toBe(false);
    expect(aceitouOTextoAtual(serializarEscolha("refused"))).toBe(false);
    expect(precisaPerguntar(serializarEscolha("refused"))).toBe(false);
  });

  it("aceite de outra versão do texto pergunta de novo e não libera", () => {
    const antigo = serializarEscolha("accepted", "v0");
    expect(aceitouOTextoAtual(antigo)).toBe(false);
    expect(precisaPerguntar(antigo)).toBe(true);
  });

  it("valor estranho é tratado como sem escolha", () => {
    for (const ruim of ["v1", "v1.maybe", "v1.accepted.x", ".accepted"]) {
      expect(lerEscolha(ruim)).toBeNull();
    }
  });
});

describe("cookies", () => {
  it("lê o valor pelo nome exato", () => {
    expect(valorDoCookie("a=1; sell_utm=x%3Dy; sell_utm2=z", "sell_utm")).toBe("x%3Dy");
    expect(valorDoCookie("a=1", "sell_utm")).toBeUndefined();
  });

  it("grava first-party, Secure e Lax; apagar zera o prazo", () => {
    expect(cookieDoNavegador("sell_utm", "v", 604800)).toBe(
      "sell_utm=v; Max-Age=604800; Path=/; Secure; SameSite=Lax"
    );
    expect(apagarCookie("sell_utm")).toContain("Max-Age=0");
  });
});

describe("atribuição no início do checkout", () => {
  const valor = serializarAtribuicao({ utm: { utm_source: "insta" }, link: null, visitadoEm: AGORA });

  it("só com o aceite da versão atual", () => {
    expect(atribuicaoDoPedido(cookies({ sell_utm: valor }), AGORA)).toBeNull();
    expect(
      atribuicaoDoPedido(cookies({ sell_utm: valor, sell_cookie_choice: serializarEscolha("refused") }), AGORA)
    ).toBeNull();
    expect(
      atribuicaoDoPedido(cookies({ sell_utm: valor, sell_cookie_choice: serializarEscolha("accepted") }), AGORA)
    ).toEqual({ utm: { utm_source: "insta" }, link: null, visitadoEm: AGORA });
  });
});

describe("piloto", () => {
  it("desligado por padrão e ligado só para a loja da lista", () => {
    expect(atribuicaoLiberada(LINK, {})).toBe(false);
    expect(atribuicaoLiberada(LINK, { APPSELL_UTM_PILOT_SELLER_IDS: ` x , ${LINK} ` })).toBe(true);
    expect(atribuicaoLiberada("outra", { APPSELL_UTM_PILOT_SELLER_IDS: LINK })).toBe(false);
  });
});
