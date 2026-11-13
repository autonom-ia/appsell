"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  autosaveReducer,
  initialAutosave,
  needsSave,
  type AutosaveFailure
} from "@/lib/curriculum/autosave";

/**
 * Um campo que salva sozinho um segundo depois de a pessoa parar de digitar, e
 * na hora em que ela sai do campo (#649, decisão D5).
 */

export const AUTOSAVE_DELAY_MS = 1_000;

export function useAutosave({
  serverValue,
  save,
  disabled
}: {
  serverValue: string;
  save: (value: string, options?: { keepalive?: boolean }) => Promise<{ ok: true } | { ok: false; reason: AutosaveFailure }>;
  disabled: boolean;
}) {
  const [state, dispatch] = useReducer(autosaveReducer, serverValue, initialAutosave);
  const stateRef = useRef(state);
  const saveRef = useRef(save);
  const timer = useRef<number | null>(null);
  const running = useRef(false);

  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => { saveRef.current = save; }, [save]);
  useEffect(() => { dispatch({ type: "external", value: serverValue }); }, [serverValue]);

  const flush = useCallback(async () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    if (running.current || disabled) return;
    running.current = true;
    try {
      while (needsSave(stateRef.current)) {
        const value = stateRef.current.value;
        dispatch({ type: "start" });
        stateRef.current = { ...stateRef.current, status: "saving", inFlight: value };
        const result = await saveRef.current(value);
        if (!result.ok) {
          dispatch({ type: "failure", reason: result.reason });
          return;
        }
        dispatch({ type: "success", value });
        const dirty = stateRef.current.value !== value;
        stateRef.current = { ...stateRef.current, saved: value, inFlight: null, status: dirty ? "pending" : "saved" };
      }
    } finally {
      running.current = false;
    }
  }, [disabled]);

  const onChange = useCallback((value: string) => {
    dispatch({ type: "edit", value });
    stateRef.current = autosaveReducer(stateRef.current, { type: "edit", value });
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), AUTOSAVE_DELAY_MS);
  }, [flush]);

  const retry = useCallback(() => {
    dispatch({ type: "edit", value: stateRef.current.value });
    stateRef.current = { ...stateRef.current, status: "pending" };
    void flush();
  }, [flush]);

  // Fechar a aba logo depois de digitar também não perde o texto: o pedido
  // sai com `keepalive`, que o navegador completa depois de a página sumir.
  // Lacuna conhecida (#656): com um salvamento em voo que já chegou ao
  // servidor, o `keepalive` sai com a versão do curso de antes dele e recebe
  // 409. O texto digitado depois do pedido em voo se perde nesse caso.
  useEffect(() => {
    const leave = () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      const pending = stateRef.current;
      if (disabled || pending.value === pending.saved || pending.status === "failed") return;
      // Com um salvamento em voo, o que foi digitado depois dele ainda não saiu.
      // O pedido em voo não tem `keepalive` e pode morrer junto com a aba;
      // mandar o valor atual agora é a melhor chance dele (ver a lacuna acima).
      if (running.current && pending.value === pending.inFlight) return;
      void saveRef.current(pending.value, { keepalive: true });
    };
    window.addEventListener("pagehide", leave);
    return () => window.removeEventListener("pagehide", leave);
  }, [disabled]);

  // Voltar para o curso logo depois de digitar não pode perder o texto: o que
  // ainda não foi salvo sai na hora em que o campo deixa a tela.
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    const pending = stateRef.current;
    if (!running.current && pending.value !== pending.saved && pending.status !== "failed") {
      void saveRef.current(pending.value);
    }
  }, []);

  return { state, onChange, flush, retry };
}
