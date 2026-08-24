"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

import {
  parsePdfStudyState,
  type PdfStudyAnnotation,
  type PdfStudyMutation,
  type PdfStudyPoint,
  type PdfStudyState
} from "@/lib/pdf-study";

type StudyTool = "hand" | "pen" | "highlight" | "eraser" | "select";
type SaveStatus = "idle" | "saving" | "saved" | "error";
type HistoryCommand = {
  undo: PdfStudyMutation;
  redo: PdfStudyMutation;
  applyUndo: (state: PdfStudyState) => PdfStudyState;
  applyRedo: (state: PdfStudyState) => PdfStudyState;
};

const COLORS = ["#173B73", "#D73535", "#E59819", "#15946A", "#7C4DCC"];
const WIDTHS = { pen: [0.0015, 0.003, 0.005], highlight: [0.012, 0.022, 0.035] } as const;

export function useProtectedPdfStudy({
  viewHref,
  pageCount,
  activePage,
  pageHeightRatio,
  open,
  onPage
}: {
  viewHref: string;
  pageCount: number;
  activePage: number;
  pageHeightRatio: number;
  open: boolean;
  onPage: (page: number) => void;
}) {
  const [state, setState] = useState<PdfStudyState | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [tool, setTool] = useState<StudyTool>("hand");
  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState<number>(WIDTHS.pen[1]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PdfStudyAnnotation | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const stateRef = useRef<PdfStudyState | null>(null);
  const revisionRef = useRef(0);
  const csrfRef = useRef("");
  const queueRef = useRef(Promise.resolve());
  const pendingRef = useRef(0);
  const blockedRef = useRef(false);
  const historyRef = useRef<HistoryCommand[]>([]);
  const redoRef = useRef<HistoryCommand[]>([]);
  const [historyCounts, setHistoryCounts] = useState({ undo: 0, redo: 0 });
  const drawPointer = useRef<number | null>(null);
  const activeTouchPointers = useRef(new Set<number>());
  const activePageRef = useRef(activePage);
  const dragRef = useRef<{
    pointer: number;
    start: PdfStudyPoint;
    before: PdfStudyAnnotation;
  } | null>(null);
  const studyHref = useMemo(() => viewHref.replace(/\/pages$/, "/study"), [viewHref]);

  useEffect(() => {
    activePageRef.current = activePage;
  }, [activePage]);

  const installState = useCallback((next: PdfStudyState) => {
    stateRef.current = next;
    revisionRef.current = next.revision;
    setState(next);
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await fetch(studyHref, {
        cache: "no-store", credentials: "same-origin", referrerPolicy: "no-referrer"
      });
      if (response.status === 404) {
        setAvailable(false);
        return;
      }
      const payload: unknown = response.ok ? await response.json() : null;
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error();
      const envelope = payload as Record<string, unknown>;
      const next = parsePdfStudyState(envelope.state, assetIdFromHref(viewHref), pageCount);
      if (!next || typeof envelope.csrf !== "string" || envelope.csrf.length < 16) throw new Error();
      csrfRef.current = envelope.csrf;
      installState(next);
      setAvailable(true);
      setSaveStatus("idle");
    } catch {
      setAvailable(null);
      setSaveStatus("error");
    }
  }, [installState, pageCount, studyHref, viewHref]);

  useEffect(() => {
    if (!open || available === false || state) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [available, load, open, state]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setState(null);
      stateRef.current = null;
      setAvailable(null);
      setSelectedId(null);
      historyRef.current = [];
      redoRef.current = [];
      setHistoryCounts({ undo: 0, redo: 0 });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [studyHref]);

  const recover = useCallback(async () => {
    blockedRef.current = true;
    historyRef.current = [];
    redoRef.current = [];
    setHistoryCounts({ undo: 0, redo: 0 });
    setSelectedId(null);
    await load();
    blockedRef.current = false;
  }, [load]);

  const enqueue = useCallback((mutation: PdfStudyMutation) => {
    pendingRef.current += 1;
    setSaveStatus("saving");
    queueRef.current = queueRef.current.then(async () => {
      if (blockedRef.current) return;
      const response = await fetch(studyHref, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        referrerPolicy: "no-referrer",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          _csrf: csrfRef.current,
          idempotency_key: crypto.randomUUID(),
          expected_revision: revisionRef.current,
          mutation
        })
      });
      const payload: unknown = response.ok ? await response.json() : null;
      const next = payload && typeof payload === "object" && !Array.isArray(payload)
        ? parsePdfStudyState(
            (payload as Record<string, unknown>).state,
            assetIdFromHref(viewHref),
            pageCount
          )
        : null;
      if (!next) throw new Error(`pdf_study_${response.status}`);
      revisionRef.current = next.revision;
    }).catch(async () => {
      setSaveStatus("error");
      await recover();
    }).finally(() => {
      pendingRef.current = Math.max(0, pendingRef.current - 1);
      if (pendingRef.current === 0 && !blockedRef.current) {
        setSaveStatus("saved");
        window.setTimeout(() => setSaveStatus("idle"), 1_500);
      }
    });
  }, [pageCount, recover, studyHref, viewHref]);

  const applyLocal = useCallback((change: (current: PdfStudyState) => PdfStudyState) => {
    setState((current) => {
      if (!current) return current;
      const next = change(current);
      stateRef.current = next;
      return next;
    });
  }, []);

  const commit = useCallback((command: HistoryCommand) => {
    applyLocal(command.applyRedo);
    historyRef.current.push(command);
    if (historyRef.current.length > 100) historyRef.current.shift();
    redoRef.current = [];
    setHistoryCounts({ undo: historyRef.current.length, redo: 0 });
    enqueue(command.redo);
  }, [applyLocal, enqueue]);

  const upsertCommand = useCallback((before: PdfStudyAnnotation | null, after: PdfStudyAnnotation) => ({
    undo: before
      ? { action: "annotation.upsert", annotation: before } as PdfStudyMutation
      : { action: "annotation.delete", id: after.id } as PdfStudyMutation,
    redo: { action: "annotation.upsert", annotation: after } as PdfStudyMutation,
    applyUndo: (current: PdfStudyState) => before
      ? withAnnotation(current, before)
      : withoutAnnotation(current, after.id),
    applyRedo: (current: PdfStudyState) => withAnnotation(current, after)
  }), []);

  const deleteAnnotation = useCallback((annotation: PdfStudyAnnotation) => {
    commit({
      undo: { action: "annotation.upsert", annotation },
      redo: { action: "annotation.delete", id: annotation.id },
      applyUndo: (current) => withAnnotation(current, annotation),
      applyRedo: (current) => withoutAnnotation(current, annotation.id)
    });
    setSelectedId(null);
  }, [commit]);

  const undo = useCallback(() => {
    const command = historyRef.current.pop();
    if (!command) return;
    applyLocal(command.applyUndo);
    redoRef.current.push(command);
    setHistoryCounts({ undo: historyRef.current.length, redo: redoRef.current.length });
    enqueue(command.undo);
  }, [applyLocal, enqueue]);

  const redo = useCallback(() => {
    const command = redoRef.current.pop();
    if (!command) return;
    applyLocal(command.applyRedo);
    historyRef.current.push(command);
    setHistoryCounts({ undo: historyRef.current.length, redo: redoRef.current.length });
    enqueue(command.redo);
  }, [applyLocal, enqueue]);

  const toggleBookmark = useCallback(() => {
    const current = stateRef.current;
    if (!current) return;
    const page = activePageRef.current;
    const active = !current.bookmarks.includes(page);
    applyLocal((current) => ({
      ...current,
      bookmarks: active
        ? [...current.bookmarks, page].sort((a, b) => a - b)
        : current.bookmarks.filter((item) => item !== page)
    }));
    enqueue({ action: "bookmark.set", page, active });
  }, [applyLocal, enqueue]);

  const chooseTool = useCallback((next: StudyTool) => {
    setTool(next);
    setDraft(null);
    drawPointer.current = null;
    if (next === "pen") setWidth(WIDTHS.pen[1]);
    if (next === "highlight") setWidth(WIDTHS.highlight[1]);
    if (next !== "select") setSelectedId(null);
  }, []);

  const activePageAnnotationCount = state?.annotations.filter(
    (annotation) => annotation.page === activePage
  ).length ?? 0;
  const selectedOnActivePage = state?.annotations.some(
    (annotation) => annotation.id === selectedId && annotation.page === activePage
  ) ?? false;
  const toolHint = tool !== "select" ? ""
    : selectedOnActivePage
      ? "Marcação selecionada. Arraste para mover ou ajuste o estilo."
      : activePageAnnotationCount === 0
        ? "Sem marcações nesta página. Use Caneta ou Marca-texto primeiro."
        : "Clique ou arraste uma marcação para editar.";

  const updateSelectedStyle = useCallback((nextColor: string, nextWidth = width) => {
    const selected = stateRef.current?.annotations.find((item) => item.id === selectedId);
    if (!selected) return;
    const after = { ...selected, color: nextColor, width: nextWidth };
    setColor(nextColor);
    setWidth(nextWidth);
    commit(upsertCommand(selected, after));
  }, [commit, selectedId, upsertCommand, width]);

  const activePageBookmarked = state?.bookmarks.includes(activePage) ?? false;
  const toolbar = useMemo(() => available === false ? null : <div className="pdf-study-toolbar" aria-label="Ferramentas de estudo">
    <div className="pdf-study-tools" role="toolbar" aria-label="Anotar PDF">
      {(["hand", "pen", "highlight", "eraser", "select"] as const).map((name) => {
        const meta = toolMeta(name);
        return <button
          key={name}
          type="button"
          className={tool === name ? "is-active" : ""}
          aria-label={meta.label}
          title={meta.label}
          aria-pressed={tool === name}
          onClick={() => chooseTool(name)}
          disabled={!state}
        ><span className="pdf-study-icon" aria-hidden="true">{meta.icon}</span><span className="pdf-study-label">{meta.label}</span></button>;
      })}
    </div>
    {(tool === "pen" || tool === "highlight" || (tool === "select" && selectedOnActivePage))
      ? <div className="pdf-study-style" aria-label="Cor e espessura">
          {COLORS.map((item) => <button
            key={item}
            type="button"
            className={color === item ? "is-active" : ""}
            style={{ "--study-color": item } as React.CSSProperties}
            aria-label={`Cor ${item}`}
            onClick={() => tool === "select" ? updateSelectedStyle(item) : setColor(item)}
          />)}
          {(tool === "highlight" ? WIDTHS.highlight : WIDTHS.pen).map((item, index) =>
            <button
              key={item}
              type="button"
              className={`pdf-study-width ${width === item ? "is-active" : ""}`}
              aria-label={`Espessura ${index + 1}`}
              onClick={() => tool === "select"
                ? updateSelectedStyle(color, item)
                : setWidth(item)}
            ><span style={{ height: `${index + 2}px` }}/></button>)}
        </div>
      : null}
    {toolHint ? <small className="pdf-study-hint" role="status">{toolHint}</small> : null}
    <div className="pdf-study-actions">
      <button type="button" aria-label="Desfazer" title="Desfazer" onClick={undo} disabled={historyCounts.undo === 0}><span className="pdf-study-icon" aria-hidden="true">↶</span><span className="pdf-study-label">Desfazer</span></button>
      <button type="button" aria-label="Refazer" title="Refazer" onClick={redo} disabled={historyCounts.redo === 0}><span className="pdf-study-icon" aria-hidden="true">↷</span><span className="pdf-study-label">Refazer</span></button>
      {selectedOnActivePage ? <button type="button" onClick={() => {
        const selected = stateRef.current?.annotations.find((item) => item.id === selectedId);
        if (selected) deleteAnnotation(selected);
      }} aria-label="Excluir marcação" title="Excluir marcação"><span className="pdf-study-icon" aria-hidden="true">⌫</span><span className="pdf-study-label">Excluir</span></button> : null}
      <button
        type="button"
        className={activePageBookmarked ? "is-active" : ""}
        aria-label="Favorito"
        title="Favorito"
        aria-pressed={activePageBookmarked}
        onClick={toggleBookmark}
        disabled={!state}
      ><span className="pdf-study-icon" aria-hidden="true">{activePageBookmarked ? "★" : "☆"}</span><span className="pdf-study-label">Favorito</span></button>
      <button type="button" aria-label="Minhas marcações" title="Minhas marcações" onClick={() => setPanelOpen((value) => !value)} disabled={!state}><span className="pdf-study-icon" aria-hidden="true">☰</span><span className="pdf-study-label">Minhas marcações</span></button>
      <small className={`pdf-study-save is-${saveStatus}`} aria-live="polite">{saveLabel(saveStatus, available)}</small>
    </div>
  </div>, [
    activePageBookmarked, available, chooseTool, color, deleteAnnotation, historyCounts.redo,
    historyCounts.undo, redo, saveStatus, selectedId, selectedOnActivePage, state,
    toggleBookmark, tool, toolHint, undo, updateSelectedStyle, width
  ]);

  const layer = useCallback((page: number): ReactNode => {
    if (!state || available !== true) return null;
    const annotations = state.annotations.filter((annotation) => annotation.page === page);
    const drawing = draft?.page === page ? draft : null;
    const interactive = tool !== "hand";
    if (!interactive && annotations.length === 0 && !drawing) return null;

    const pointFromEvent = (event: ReactPointerEvent<SVGSVGElement>): PdfStudyPoint => {
      const bounds = event.currentTarget.getBoundingClientRect();
      return {
        x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
        y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1)
      };
    };
    const startDraw = (event: ReactPointerEvent<SVGSVGElement>) => {
      if ((tool !== "pen" && tool !== "highlight") || !event.isPrimary || event.button !== 0) return;
      if (event.pointerType === "touch" && activeTouchPointers.current.size > 1) return;
      const point = pointFromEvent(event);
      drawPointer.current = event.pointerId;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDraft({ id: crypto.randomUUID(), page, tool, color, width, points: [point, point] });
    };
    const movePointer = (event: ReactPointerEvent<SVGSVGElement>) => {
      if (event.pointerType === "touch" && activeTouchPointers.current.size > 1) return;
      if (drawPointer.current === event.pointerId && draft?.page === page) {
        const point = pointFromEvent(event);
        setDraft((current) => current && distance(current.points.at(-1)!, point) >= 0.001
          ? { ...current, points: [...current.points, point].slice(0, 1024) }
          : current);
      }
      const dragging = dragRef.current;
      if (dragging?.pointer === event.pointerId) {
        const point = pointFromEvent(event);
        const moved = moveAnnotation(dragging.before, point.x - dragging.start.x, point.y - dragging.start.y);
        applyLocal((current) => withAnnotation(current, moved));
      }
    };
    const finishPointer = (event: ReactPointerEvent<SVGSVGElement>) => {
      if (drawPointer.current === event.pointerId && draft?.page === page) {
        const completed = draft;
        drawPointer.current = null;
        setDraft(null);
        if (completed.points.length >= 2) commit(upsertCommand(null, completed));
      }
      const dragging = dragRef.current;
      if (dragging?.pointer === event.pointerId) {
        dragRef.current = null;
        const after = stateRef.current?.annotations.find((item) => item.id === dragging.before.id);
        if (after && !samePoints(after.points, dragging.before.points)) {
          historyRef.current.push(upsertCommand(dragging.before, after));
          redoRef.current = [];
          setHistoryCounts({ undo: historyRef.current.length, redo: 0 });
          enqueue({ action: "annotation.upsert", annotation: after });
        }
      }
    };

    return <svg
      className={`pdf-study-layer is-${tool}`}
      viewBox={`0 0 1 ${pageHeightRatio}`}
      preserveAspectRatio="none"
      aria-label={interactive ? `Camada de anotações da página ${page}` : undefined}
      aria-hidden={!interactive}
      onPointerDownCapture={(event) => {
        if (event.pointerType !== "touch") return;
        activeTouchPointers.current.add(event.pointerId);
        if (activeTouchPointers.current.size < 2) return;
        const drawingPointer = drawPointer.current;
        if (drawingPointer !== null && event.currentTarget.hasPointerCapture(drawingPointer)) {
          event.currentTarget.releasePointerCapture(drawingPointer);
        }
        drawPointer.current = null;
        dragRef.current = null;
        setDraft(null);
      }}
      onPointerDown={startDraw}
      onPointerMove={movePointer}
      onPointerUp={(event) => {
        finishPointer(event);
        activeTouchPointers.current.delete(event.pointerId);
      }}
      onPointerCancel={(event) => {
        activeTouchPointers.current.delete(event.pointerId);
        drawPointer.current = null;
        dragRef.current = null;
        setDraft(null);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
      }}
    >
      {[...annotations, ...(drawing ? [drawing] : [])].map((annotation) => {
        const points = annotation.points
          .map((point) => `${point.x},${point.y * pageHeightRatio}`)
          .join(" ");
        const hitPath = annotation.points
          .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y * pageHeightRatio}`)
          .join(" ");
        return <g key={annotation.id}>
          {!drawing ? <path
            d={hitPath}
            className="pdf-study-hit-target"
            fill="none"
            stroke="transparent"
            strokeWidth={Math.max(annotation.width, 0.025)}
            strokeLinecap="round"
            strokeLinejoin="round"
            onPointerDown={(event) => {
              if (tool !== "eraser" && tool !== "select") return;
              event.stopPropagation();
              if (tool === "eraser") {
                deleteAnnotation(annotation);
                return;
              }
              setSelectedId(annotation.id);
              setColor(annotation.color);
              setWidth(annotation.width);
              const svg = event.currentTarget.ownerSVGElement!;
              const bounds = svg.getBoundingClientRect();
              dragRef.current = {
                pointer: event.pointerId,
                start: {
                  x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
                  y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1)
                },
                before: annotation
              };
              svg.setPointerCapture(event.pointerId);
            }}
          /> : null}
          <polyline
            points={points}
            fill="none"
            stroke={annotation.color}
            strokeWidth={annotation.width}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={annotation.tool === "highlight" ? 0.32 : 0.96}
            className={selectedId === annotation.id ? "is-selected" : ""}
          />
        </g>;
      })}
    </svg>;
  }, [
    applyLocal, available, color, commit, deleteAnnotation, draft, enqueue, pageHeightRatio,
    selectedId, state, tool, upsertCommand, width
  ]);

  const pages = useMemo(() => {
    if (!state) return [];
    const values = new Set(state.bookmarks);
    state.annotations.forEach((annotation) => values.add(annotation.page));
    return [...values].sort((a, b) => a - b).map((page) => ({
      page,
      favorite: state.bookmarks.includes(page),
      count: state.annotations.filter((annotation) => annotation.page === page).length
    }));
  }, [state]);

  const panel = panelOpen && state ? <aside className="pdf-study-panel" aria-label="Minhas marcações">
    <header><div><small>Estudo</small><strong>Minhas marcações</strong></div><button type="button" onClick={() => setPanelOpen(false)} aria-label="Fechar marcações">×</button></header>
    {pages.length === 0
      ? <p>Suas anotações e páginas favoritas aparecerão aqui.</p>
      : <ol>{pages.map((item) => <li key={item.page}><button type="button" onClick={() => {
          onPage(item.page);
          if (window.innerWidth < 760) setPanelOpen(false);
        }}><span>{item.favorite ? "★" : "Página"}</span><strong>Página {item.page}</strong><small>{item.count === 1 ? "1 anotação" : `${item.count} anotações`}</small></button></li>)}</ol>}
  </aside> : null;

  return { toolbar, layer, panel, tool };
}

function withAnnotation(state: PdfStudyState, annotation: PdfStudyAnnotation): PdfStudyState {
  const exists = state.annotations.some((item) => item.id === annotation.id);
  return {
    ...state,
    annotations: exists
      ? state.annotations.map((item) => item.id === annotation.id ? annotation : item)
      : [...state.annotations, annotation]
  };
}

function withoutAnnotation(state: PdfStudyState, id: string): PdfStudyState {
  return { ...state, annotations: state.annotations.filter((item) => item.id !== id) };
}

function moveAnnotation(annotation: PdfStudyAnnotation, dx: number, dy: number) {
  const minX = Math.min(...annotation.points.map((point) => point.x));
  const maxX = Math.max(...annotation.points.map((point) => point.x));
  const minY = Math.min(...annotation.points.map((point) => point.y));
  const maxY = Math.max(...annotation.points.map((point) => point.y));
  const safeX = clamp(dx, -minX, 1 - maxX);
  const safeY = clamp(dy, -minY, 1 - maxY);
  return {
    ...annotation,
    points: annotation.points.map((point) => ({ x: point.x + safeX, y: point.y + safeY }))
  };
}

function samePoints(left: PdfStudyPoint[], right: PdfStudyPoint[]) {
  return left.length === right.length && left.every((point, index) =>
    point.x === right[index]?.x && point.y === right[index]?.y);
}

function assetIdFromHref(href: string) {
  return href.match(/\/assets\/([0-9a-f-]{36})\/pages$/)?.[1] ?? "";
}

function distance(left: PdfStudyPoint, right: PdfStudyPoint) {
  return Math.hypot(right.x - left.x, right.y - left.y);
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function toolMeta(tool: StudyTool) {
  return ({
    hand: { icon: "✋", label: "Mão" },
    pen: { icon: "✎", label: "Caneta" },
    highlight: { icon: "▰", label: "Marca-texto" },
    eraser: { icon: "⌫", label: "Borracha" },
    select: { icon: "⌖", label: "Editar marcação" }
  })[tool];
}

function saveLabel(status: SaveStatus, available: boolean | null) {
  if (available === null && status !== "error") return "Carregando…";
  return ({ idle: "", saving: "Salvando…", saved: "Salvo", error: "Não foi possível salvar" })[status];
}
