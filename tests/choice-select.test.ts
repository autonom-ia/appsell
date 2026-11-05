import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { choiceKeyAction, foldForTypeahead, typeaheadIndex } from "../app/components/ui/choice-keys";
import { ChoiceSelect } from "../app/components/ui/choice-select";
import { OFFER_BILLING_PERIODS } from "../lib/offer-price";

const labels = ["Pagamento único", "Mensal", "Anual", "Mensalidade"];

describe("choiceKeyAction — lista fechada", () => {
  it("abre na opção escolhida com ArrowDown, ArrowUp, Enter e Espaço", () => {
    for (const key of ["ArrowDown", "ArrowUp", "Enter", " "]) {
      expect(choiceKeyAction({ key, open: false, active: -1, selected: 2, count: 4 })).toEqual({ type: "open", active: 2 });
    }
  });

  it("abre na primeira opção quando nada está escolhido", () => {
    expect(choiceKeyAction({ key: "ArrowDown", open: false, active: -1, selected: -1, count: 4 })).toEqual({ type: "open", active: 0 });
  });

  it("abre nas pontas com Home e End", () => {
    expect(choiceKeyAction({ key: "Home", open: false, active: -1, selected: 2, count: 4 })).toEqual({ type: "open", active: 0 });
    expect(choiceKeyAction({ key: "End", open: false, active: -1, selected: 0, count: 4 })).toEqual({ type: "open", active: 3 });
  });

  it("deixa Tab e Escape passarem, para a navegação da página seguir", () => {
    expect(choiceKeyAction({ key: "Tab", open: false, active: -1, selected: 0, count: 4 })).toEqual({ type: "none" });
    expect(choiceKeyAction({ key: "Escape", open: false, active: -1, selected: 0, count: 4 })).toEqual({ type: "none" });
  });

  it("não faz nada sem opções", () => {
    expect(choiceKeyAction({ key: "ArrowDown", open: false, active: -1, selected: -1, count: 0 })).toEqual({ type: "none" });
  });
});

describe("choiceKeyAction — lista aberta", () => {
  it("anda sem dar a volta", () => {
    expect(choiceKeyAction({ key: "ArrowDown", open: true, active: 3, selected: 0, count: 4 })).toEqual({ type: "move", active: 3 });
    expect(choiceKeyAction({ key: "ArrowUp", open: true, active: 0, selected: 0, count: 4 })).toEqual({ type: "move", active: 0 });
    expect(choiceKeyAction({ key: "ArrowDown", open: true, active: 1, selected: 0, count: 4 })).toEqual({ type: "move", active: 2 });
  });

  it("pula de página e vai às pontas", () => {
    expect(choiceKeyAction({ key: "PageDown", open: true, active: 0, selected: 0, count: 30 })).toEqual({ type: "move", active: 10 });
    expect(choiceKeyAction({ key: "PageUp", open: true, active: 4, selected: 0, count: 30 })).toEqual({ type: "move", active: 0 });
    expect(choiceKeyAction({ key: "Home", open: true, active: 9, selected: 0, count: 30 })).toEqual({ type: "move", active: 0 });
    expect(choiceKeyAction({ key: "End", open: true, active: 0, selected: 0, count: 30 })).toEqual({ type: "move", active: 29 });
  });

  it("escolhe a opção ativa com Enter, Espaço, Alt+ArrowUp e Tab; só Tab mantém o foco andando", () => {
    for (const key of ["Enter", " ", "Tab"]) {
      expect(choiceKeyAction({ key, open: true, active: 1, selected: 0, count: 4 })).toEqual({ type: "commit", active: 1, keepDefault: key === "Tab" });
    }
    expect(choiceKeyAction({ key: "ArrowUp", altKey: true, open: true, active: 2, selected: 0, count: 4 })).toEqual({ type: "commit", active: 2, keepDefault: false });
  });

  it("fecha com Escape sem mudar o valor", () => {
    expect(choiceKeyAction({ key: "Escape", open: true, active: 3, selected: 0, count: 4 })).toEqual({ type: "close" });
  });
});

describe("busca por digitação", () => {
  it("ignora caixa e acento", () => {
    expect(foldForTypeahead("ÚNICO Ção")).toBe("unico cao");
    expect(typeaheadIndex(labels, "pag", -1)).toBe(0);
    expect(typeaheadIndex(labels, "ANU", -1)).toBe(2);
  });

  it("repetir a mesma letra alterna entre as opções que começam com ela", () => {
    expect(typeaheadIndex(labels, "m", 1)).toBe(3);
    expect(typeaheadIndex(labels, "mm", 3)).toBe(1);
  });

  it("devolve -1 quando nada começa com o texto", () => {
    expect(typeaheadIndex(labels, "zz", 0)).toBe(-1);
    expect(typeaheadIndex([], "a", -1)).toBe(-1);
  });
});

describe("ChoiceSelect — o que o leitor de tela recebe", () => {
  const markup = renderToStaticMarkup(createElement(ChoiceSelect<string>, {
    label: "Cobrança",
    id: "offer_billing_period",
    name: "offer_billing_period",
    value: "one_time",
    options: OFFER_BILLING_PERIODS,
    onChange: () => undefined,
    invalid: true
  }));

  it("é um combobox ligado à lista e ao rótulo, fechado de início", () => {
    expect(markup).toContain('role="combobox"');
    expect(markup).toContain('aria-haspopup="listbox"');
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('role="listbox"');
    expect(markup).toContain('id="offer_billing_period"');
    expect(markup).toContain('name="offer_billing_period"');
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain(">Cobrança<");
  });

  it("lista todas as opções e marca só a escolhida", () => {
    expect(markup.split('role="option"').length - 1).toBe(OFFER_BILLING_PERIODS.length);
    expect(markup.split('aria-selected="true"').length - 1).toBe(1);
    expect(markup).toContain(OFFER_BILLING_PERIODS.find((entry) => entry.value === "one_time")!.label);
  });

  it("é um botão de verdade, que o fieldset desabilitado também trava", () => {
    expect(markup).toContain('<button');
    expect(markup).toContain('type="button"');
  });
});

describe("editor de produto", () => {
  it("não usa mais combo nativo do navegador", () => {
    const editor = readFileSync(resolve(__dirname, "../app/components/product-editor-workspace.tsx"), "utf8");
    expect(editor).not.toContain("<select");
    expect(editor).not.toContain("<option");
    expect(editor).toContain("<ChoiceSelect");
  });
});
