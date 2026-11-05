"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { choiceKeyAction, typeaheadIndex } from "./choice-keys";
import "./choice-select.css";

export type ChoiceOption<T extends string> = { value: T; label: string; description?: string };

export type ChoiceSelectProps<T extends string> = {
  label: string;
  value: T;
  options: readonly ChoiceOption<T>[];
  onChange: (value: T) => void;
  /** Id e name do botão: o editor foca o campo pelo nome quando a API recusa o valor. */
  id?: string;
  name?: string;
  disabled?: boolean;
  invalid?: boolean;
  note?: string;
};

const TYPEAHEAD_MS = 600;
const LIST_MAX_HEIGHT = 320;
const OPTION_HEIGHT = 52;

/**
 * Escolha única do Sell. O `<select>` nativo não é usado no produto: a lista
 * dele ignora o visual da marca e muda de navegador para navegador. Teclado no
 * padrão WAI-ARIA select-only combobox; opções com 44 px de toque ou mais.
 */
export function ChoiceSelect<T extends string>({
  label, value, options, onChange, id, name, disabled = false, invalid = false, note
}: ChoiceSelectProps<T>) {
  const baseId = useId();
  const labelId = `${baseId}-label`;
  const listId = `${baseId}-list`;
  const noteId = `${baseId}-note`;
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [above, setAbove] = useState(false);
  const typed = useRef({ text: "", at: 0, from: -1 });
  const selected = options.findIndex((option) => option.value === value);
  const current = options[selected];

  function show(index: number) {
    const rect = button.current?.getBoundingClientRect();
    // Abre para cima só quando não cabe embaixo e há mais espaço em cima.
    if (rect) {
      const below = window.innerHeight - rect.bottom;
      setAbove(below < Math.min(LIST_MAX_HEIGHT, options.length * OPTION_HEIGHT + 16) && rect.top > below);
    }
    setActive(index);
    setOpen(true);
  }

  function close(focus = true) {
    setOpen(false);
    setActive(-1);
    if (focus) button.current?.focus({ preventScroll: true });
  }

  function commit(index: number, focus = true) {
    const option = options[index];
    close(focus);
    if (option && option.value !== value) onChange(option.value);
  }

  useLayoutEffect(() => {
    if (open && active >= 0) list.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  });

  function onTypeahead(key: string) {
    const now = Date.now();
    const session = typed.current;
    if (now - session.at > TYPEAHEAD_MS) {
      session.text = "";
      session.from = open ? active : selected;
    }
    session.text += key;
    session.at = now;
    const from = session.text.length === 1 ? (open ? active : selected) : session.from;
    const match = typeaheadIndex(options.map((option) => option.label), session.text, from);
    if (match < 0) return;
    if (open) setActive(match);
    else show(match);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    const plainKey = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (plainKey && event.key !== " ") {
      event.preventDefault();
      onTypeahead(event.key);
      return;
    }
    // Espaço no meio de uma busca por digitação continua a busca.
    if (event.key === " " && typed.current.text && Date.now() - typed.current.at <= TYPEAHEAD_MS) {
      event.preventDefault();
      onTypeahead(" ");
      return;
    }
    const action = choiceKeyAction({ key: event.key, altKey: event.altKey, open, active, selected, count: options.length });
    if (action.type === "none") return;
    if (action.type === "commit" && action.keepDefault) {
      commit(action.active, false);
      return;
    }
    event.preventDefault();
    if (action.type === "open") show(action.active);
    else if (action.type === "move") setActive(action.active);
    else if (action.type === "commit") commit(action.active);
    else close();
  }

  return (
    <div className="choice-field" ref={root} data-open={open}>
      <span className="choice-label" id={labelId}>{label}</span>
      <div className="choice-anchor">
        <button
          ref={button}
          id={id}
          name={name}
          type="button"
          role="combobox"
          className="choice-trigger"
          aria-labelledby={labelId}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open && active >= 0 ? `${baseId}-option-${active}` : undefined}
          aria-describedby={note ? noteId : undefined}
          aria-invalid={invalid || undefined}
          data-value={value}
          disabled={disabled}
          onClick={() => (open ? close() : show(selected >= 0 ? selected : 0))}
          onKeyDown={onKeyDown}
          onBlur={(event) => {
            if (open && !root.current?.contains(event.relatedTarget as Node)) close(false);
          }}
        >
          <span className="choice-value">{current?.label ?? "Escolha uma opção"}</span>
          <span className="choice-chevron" aria-hidden="true" />
        </button>
        <ul ref={list} id={listId} role="listbox" aria-labelledby={labelId} className="choice-list" data-above={above} hidden={!open} tabIndex={-1}>
          {options.map((option, index) => (
            <li
              key={option.value}
              id={`${baseId}-option-${index}`}
              role="option"
              aria-selected={index === selected}
              data-value={option.value}
              data-active={index === active}
              onPointerDown={(event) => event.preventDefault()}
              onPointerMove={() => {
                if (active !== index) setActive(index);
              }}
              onClick={() => commit(index)}
            >
              <span className="choice-option-copy">
                <span>{option.label}</span>
                {option.description ? <small>{option.description}</small> : null}
              </span>
              {index === selected ? <span className="choice-check" aria-hidden="true" /> : null}
            </li>
          ))}
        </ul>
      </div>
      {note ? <small className="choice-note" id={noteId}>{note}</small> : null}
    </div>
  );
}
