"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";

import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import { createUndoQueue, type UndoQueue } from "@/lib/curriculum/undo-queue";

/**
 * O aviso "excluído · Desfazer" da jornada de montar o curso (#649, decisão D2).
 *
 * Um aviso por vez, no rodapé da tela, ao alcance do polegar. O pedido ao
 * servidor só sai quando o aviso some; sair da tela ou fechar a aba manda o
 * que estava pendente com `keepalive`. Uma exclusão que falha devolve o item
 * e diz qual foi, mesmo com outro aviso na tela.
 */

export interface UndoRequest {
  id: string;
  /** O nome do que saiu, para a falha dizer qual item voltou. */
  name: string;
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

const UNDO_REGION_ID = "curriculum-undo-region";

export function undoFailureMessage(name: string): string {
  return `Não deu para excluir “${name}”. ${JOURNEY_COPY.undoFailed}`;
}

/** O aviso sumiu com o foco dentro: o foco vai para o título da tela visível. */
function focusVisibleHeading() {
  window.requestAnimationFrame(() => {
    const lesson = document.querySelector<HTMLElement>(".lesson-editor:not([hidden]) .lesson-editor-heading");
    const target = lesson && lesson.getClientRects().length > 0 ? lesson : document.getElementById("curriculum-title");
    target?.focus();
  });
}

export function UndoProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<{ id: string; message: string } | null>(null);
  const [failures, setFailures] = useState<{ id: string; message: string }[]>([]);
  // Criados uma vez só, no primeiro render: a fila vive enquanto a tela vive.
  const [entries] = useState(() => new Map<string, { name: string; message: string }>());
  const [queue] = useState<UndoQueue>(() => createUndoQueue({
    onChange: (pendingId) => {
      if (!pendingId && document.getElementById(UNDO_REGION_ID)?.contains(document.activeElement)) focusVisibleHeading();
      setNotice(pendingId ? { id: pendingId, message: entries.get(pendingId)?.message ?? "" } : null);
    },
    onFailure: (id) => {
      const name = entries.get(id)?.name ?? "";
      entries.delete(id);
      setFailures((current) => [...current.filter((entry) => entry.id !== id), { id, message: undoFailureMessage(name) }]);
    }
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
    entries.set(request.id, { name: request.name, message: request.message });
    queue.schedule({
      id: request.id,
      revert: request.revert,
      commit: async (options) => {
        await request.commit(options);
        entries.delete(request.id);
      }
    });
  }, [entries, queue]);

  const undo = useCallback(() => {
    if (!notice) return;
    queue.undo(notice.id);
    entries.delete(notice.id);
  }, [entries, notice, queue]);

  return <UndoContext.Provider value={schedule}>
    {children}
    <div className="undo-region" id={UNDO_REGION_ID}>
      <div className="undo-failures" role="alert">
        {failures.map((failure) => <div key={failure.id} className="undo-toast undo-toast-failure">
          <p>{failure.message}</p>
          <button type="button" onClick={() => setFailures((current) => current.filter((entry) => entry.id !== failure.id))}>Entendi</button>
        </div>)}
      </div>
      <div aria-live="polite" aria-atomic="true">
        {notice ? <div className="undo-toast" role="status">
          <p>{notice.message}</p>
          <button type="button" onClick={undo}>{JOURNEY_COPY.undo}</button>
        </div> : null}
      </div>
    </div>
  </UndoContext.Provider>;
}
