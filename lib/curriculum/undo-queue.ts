/**
 * Desfazer em vez de diálogo de risco (#649).
 *
 * Excluir uma aula, um módulo ou tirar um arquivo da aula some da tela na
 * hora. O pedido ao servidor só sai depois do prazo, e "Desfazer" dentro dele
 * devolve o item sem nada ter sido enviado. Um aviso por vez: uma segunda
 * exclusão confirma a primeira na hora.
 *
 * Sair da tela (`flush`) confirma o que estava pendente, com `leaving` para o
 * pedido ir com `keepalive`. Se o navegador fechar antes disso, nada é
 * apagado: é a falha que não perde conteúdo.
 */

export interface UndoEntry {
  id: string;
  commit: (options: { leaving: boolean }) => Promise<void>;
  revert: () => void;
}

export interface UndoQueue {
  schedule(entry: UndoEntry): void;
  undo(id: string): boolean;
  flush(options?: { leaving?: boolean }): Promise<void>;
  pendingId(): string | null;
  dispose(): void;
}

export const UNDO_WINDOW_MS = 8_000;

export function createUndoQueue({
  delayMs = UNDO_WINDOW_MS,
  onFailure,
  onChange
}: {
  delayMs?: number;
  onFailure?: (id: string) => void;
  onChange?: (pendingId: string | null) => void;
} = {}): UndoQueue {
  let pending: { entry: UndoEntry; timer: ReturnType<typeof setTimeout> } | null = null;

  const run = async (entry: UndoEntry, leaving: boolean) => {
    try {
      await entry.commit({ leaving });
    } catch {
      entry.revert();
      onFailure?.(entry.id);
    }
  };

  const take = (): UndoEntry | null => {
    if (!pending) return null;
    clearTimeout(pending.timer);
    const { entry } = pending;
    pending = null;
    onChange?.(null);
    return entry;
  };

  return {
    schedule(entry) {
      const previous = take();
      if (previous) void run(previous, false);
      const timer = setTimeout(() => {
        const due = take();
        if (due) void run(due, false);
      }, delayMs);
      pending = { entry, timer };
      onChange?.(entry.id);
    },
    undo(id) {
      if (!pending || pending.entry.id !== id) return false;
      const entry = take();
      entry?.revert();
      return true;
    },
    async flush(options = {}) {
      const entry = take();
      if (entry) await run(entry, options.leaving ?? false);
    },
    pendingId() {
      return pending?.entry.id ?? null;
    },
    dispose() {
      if (pending) clearTimeout(pending.timer);
      pending = null;
    }
  };
}
