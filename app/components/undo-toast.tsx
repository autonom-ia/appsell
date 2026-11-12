"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import { createUndoQueue, type UndoQueue } from "@/lib/curriculum/undo-queue";

/**
 * O aviso "excluído · Desfazer" da jornada de montar o curso (#649, decisão D2).
 *
 * Um aviso por vez, no rodapé da tela, ao alcance do polegar. O pedido ao
 * servidor só sai quando o aviso some; sair da tela ou fechar a aba manda o
 * que estava pendente com `keepalive`.
 */

export interface UndoRequest {
  id: string;
  message: string;
  commit: (options: { leaving: boolean }) => Promise<void>;
  revert: () => void;
}

type ScheduleUndo = (request: UndoRequest) => void;

const UndoContext = createContext<ScheduleUndo | null>(null);

export function useScheduleUndo(): ScheduleUndo {
  const schedule = useContext(UndoContext);
  if (!schedule) throw new Error("useScheduleUndo fora do UndoProvider");
  return schedule;
}

export function UndoProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<{ id: string; message: string } | null>(null);
  const [failure, setFailure] = useState(false);
  // Criados uma vez só, no primeiro render: a fila vive enquanto a tela vive.
  const [messages] = useState(() => new Map<string, string>());
  const [queue] = useState<UndoQueue>(() => createUndoQueue({
    onChange: (pendingId) => {
      setNotice(pendingId ? { id: pendingId, message: messages.get(pendingId) ?? "" } : null);
    },
    onFailure: () => setFailure(true)
  }));

  useEffect(() => {
    const leave = () => { void queue.flush({ leaving: true }); };
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pagehide", leave);
      void queue.flush({ leaving: true });
    };
  }, [queue]);

  const schedule = useCallback<ScheduleUndo>((request) => {
    messages.set(request.id, request.message);
    setFailure(false);
    queue.schedule({ id: request.id, commit: request.commit, revert: request.revert });
  }, [messages, queue]);

  const undo = useCallback(() => {
    if (notice) queue.undo(notice.id);
  }, [notice, queue]);

  const value = useMemo(() => schedule, [schedule]);

  return <UndoContext.Provider value={value}>
    {children}
    <div className="undo-region" aria-live="polite" aria-atomic="true">
      {notice ? <div className="undo-toast" role="status">
        <p>{notice.message}</p>
        <button type="button" onClick={undo}>{JOURNEY_COPY.undo}</button>
      </div> : null}
      {failure && !notice ? <div className="undo-toast undo-toast-failure" role="alert">
        <p>{JOURNEY_COPY.undoFailed}</p>
        <button type="button" onClick={() => setFailure(false)}>Entendi</button>
      </div> : null}
    </div>
  </UndoContext.Provider>;
}
