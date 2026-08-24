"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

interface ProtectedPdfAsset {
  view_href: string;
  download_href: string | null;
  title: string;
}

type ManifestState = { pageCount: number } | "loading" | "error";

const TENANT_PDF_VIEW_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/pages$/;
const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;

export function ProtectedPdfReader({ asset }: { asset: ProtectedPdfAsset }) {
  const [manifest, setManifest] = useState<ManifestState>("loading");
  const [pageNumber, setPageNumber] = useState(1);
  const [pageDraft, setPageDraft] = useState("1");
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [readerOpen, setReaderOpen] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [failedPages, setFailedPages] = useState<Set<number>>(() => new Set());
  const [policyFeedback, setPolicyFeedback] = useState("");
  const [viewportWidth, setViewportWidth] = useState(0);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const readerRef = useRef<HTMLElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const pageElements = useRef(new Map<number, HTMLElement>());
  const pinchStartDistance = useRef<number | null>(null);
  const pinchStartZoom = useRef(MIN_ZOOM);
  const zoomRef = useRef(MIN_ZOOM);
  const lastTouchTap = useRef(0);
  const pageNumberRef = useRef(1);
  const programmaticPage = useRef<number | null>(null);

  const pageCount = typeof manifest === "object" ? manifest.pageCount : 0;
  const storageKey = useMemo(
    () => `sell:protected-pdf:v1:${asset.view_href}`,
    [asset.view_href]
  );

  const loadManifest = useCallback(async () => {
    setManifest("loading");
    setPolicyFeedback("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(asset.view_href, {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      const payload: unknown = response.ok ? await response.json() : null;
      const expectedAssetId = asset.view_href.match(TENANT_PDF_VIEW_HREF)?.[2];
      if (!validPdfManifest(payload, expectedAssetId)) throw new Error("invalid_pdf_manifest");
      const restored = restoreReaderSession(storageKey, payload.page_count);
      setManifest({ pageCount: payload.page_count });
      pageNumberRef.current = restored.page;
      setPageNumber(restored.page);
      setPageDraft(String(restored.page));
      setZoom(restored.zoom);
      setPreviewError(false);
      setFailedPages(new Set());
    } catch {
      setManifest("error");
    } finally {
      window.clearTimeout(timeout);
    }
  }, [asset.view_href, storageKey]);

  useEffect(() => {
    const start = window.setTimeout(() => void loadManifest(), 0);
    return () => window.clearTimeout(start);
  }, [loadManifest]);

  useEffect(() => {
    if (typeof manifest !== "object") return;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        version: 1,
        page: pageNumber,
        zoom
      }));
    } catch {
      // The reader remains fully usable when browser storage is unavailable.
    }
  }, [manifest, pageNumber, storageKey, zoom]);

  useEffect(() => {
    if (typeof manifest !== "object") return;
    const preloaders = [pageNumber - 1, pageNumber + 1]
      .filter((page) => page >= 1 && page <= manifest.pageCount)
      .map((page) => {
        const image = new Image();
        image.referrerPolicy = "no-referrer";
        image.src = pageHref(asset.view_href, page);
        return image;
      });
    return () => {
      preloaders.forEach((image) => image.removeAttribute("src"));
    };
  }, [asset.view_href, manifest, pageNumber]);

  useEffect(() => {
    pageNumberRef.current = pageNumber;
  }, [pageNumber]);

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  const closeReader = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    setReaderOpen(false);
    window.setTimeout(() => openerRef.current?.focus(), 0);
  }, []);

  const scrollToPage = useCallback((nextPage: number, smooth = true) => {
    const target = pageElements.current.get(nextPage);
    if (!target) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({
      behavior: smooth && !reduceMotion ? "smooth" : "auto",
      block: "start",
      inline: "center"
    });
  }, []);

  const selectPage = useCallback((nextPage: number) => {
    if (nextPage < 1 || nextPage > pageCount) return;
    pageNumberRef.current = nextPage;
    setPageNumber(nextPage);
    setPageDraft(String(nextPage));
    setPreviewError(false);
    setPolicyFeedback("");
    if (readerOpen) {
      programmaticPage.current = nextPage;
      scrollToPage(nextPage);
    }
  }, [pageCount, readerOpen, scrollToPage]);

  const changeZoom = useCallback((delta: number) => {
    setZoom((current) => clampZoom(current + delta));
  }, []);

  useEffect(() => {
    const blockProtectedDocumentShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && ["p", "s"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        setPolicyFeedback(event.key.toLowerCase() === "p"
          ? "A impressão deste conteúdo está desativada."
          : "O arquivo original não está disponível para salvar.");
        return;
      }
      if (!readerOpen) return;
      if (event.key === "Escape") closeReader();
      if (event.key === "ArrowLeft" || event.key === "PageUp") {
        event.preventDefault();
        selectPage(pageNumber - 1);
      }
      if (event.key === "ArrowRight" || event.key === "PageDown") {
        event.preventDefault();
        selectPage(pageNumber + 1);
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        changeZoom(ZOOM_STEP);
      }
      if (event.key === "-") {
        event.preventDefault();
        changeZoom(-ZOOM_STEP);
      }
    };
    window.addEventListener("keydown", blockProtectedDocumentShortcut, { capture: true });
    return () => window.removeEventListener("keydown", blockProtectedDocumentShortcut, { capture: true });
  }, [changeZoom, closeReader, pageNumber, readerOpen, selectPage]);

  useEffect(() => {
    if (!readerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [readerOpen]);

  useEffect(() => {
    const updateFullscreen = () => setFullscreenActive(document.fullscreenElement === readerRef.current);
    document.addEventListener("fullscreenchange", updateFullscreen);
    return () => document.removeEventListener("fullscreenchange", updateFullscreen);
  }, []);

  useEffect(() => {
    if (!readerOpen || !viewportRef.current) return;
    const viewport = viewportRef.current;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setViewportWidth(entry.contentRect.width);
    });
    observer.observe(viewport);
    setViewportWidth(viewport.clientWidth);
    return () => observer.disconnect();
  }, [readerOpen]);

  useEffect(() => {
    if (!readerOpen || !viewportRef.current || pageCount === 0) return;
    const intersections = new Map<number, number>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const page = Number((entry.target as HTMLElement).dataset.page);
        if (Number.isSafeInteger(page)) intersections.set(page, entry.intersectionRatio);
      }
      const requestedPage = programmaticPage.current;
      if (requestedPage !== null) {
        const requestedVisibility = intersections.get(requestedPage) ?? 0;
        const strongestVisibility = Math.max(0, ...intersections.values());
        if (requestedVisibility > 0.01 && requestedVisibility >= strongestVisibility) {
          programmaticPage.current = null;
        }
        return;
      }
      let visiblePage = 1;
      let visibility = 0;
      for (const [page, ratio] of intersections) {
        if (ratio > visibility) {
          visiblePage = page;
          visibility = ratio;
        }
      }
      if (visibility > 0.01 && visiblePage !== pageNumberRef.current) {
        pageNumberRef.current = visiblePage;
        setPageNumber(visiblePage);
        setPageDraft(String(visiblePage));
      }
    }, { root: viewportRef.current, threshold: [0, 0.02, 0.08, 0.15, 0.35, 0.6, 0.85] });
    pageElements.current.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [pageCount, readerOpen]);

  useEffect(() => {
    if (!readerOpen) return;
    const frame = window.requestAnimationFrame(() => scrollToPage(pageNumberRef.current, false));
    return () => window.cancelAnimationFrame(frame);
  }, [readerOpen, scrollToPage]);

  useEffect(() => {
    if (!readerOpen || !viewportRef.current) return;
    const viewport = viewportRef.current;
    const touchStart = (event: TouchEvent) => {
      if (event.touches.length !== 2) return;
      pinchStartDistance.current = touchDistance(event.touches);
      pinchStartZoom.current = zoomRef.current;
    };
    const touchMove = (event: TouchEvent) => {
      if (event.touches.length !== 2 || pinchStartDistance.current === null) return;
      event.preventDefault();
      const scale = touchDistance(event.touches) / pinchStartDistance.current;
      setZoom(clampZoom(pinchStartZoom.current * scale));
    };
    const touchEnd = () => {
      pinchStartDistance.current = null;
      pinchStartZoom.current = zoomRef.current;
    };
    viewport.addEventListener("touchstart", touchStart, { passive: true });
    viewport.addEventListener("touchmove", touchMove, { passive: false });
    viewport.addEventListener("touchend", touchEnd, { passive: true });
    viewport.addEventListener("touchcancel", touchEnd, { passive: true });
    return () => {
      viewport.removeEventListener("touchstart", touchStart);
      viewport.removeEventListener("touchmove", touchMove);
      viewport.removeEventListener("touchend", touchEnd);
      viewport.removeEventListener("touchcancel", touchEnd);
    };
  }, [readerOpen]);

  const fitWidth = () => setZoom(MIN_ZOOM);

  const commitPageDraft = () => {
    const requested = Number(pageDraft);
    if (Number.isSafeInteger(requested) && requested >= 1 && requested <= pageCount) {
      selectPage(requested);
    } else {
      setPageDraft(String(pageNumber));
    }
  };

  const toggleFullscreen = async () => {
    if (!readerRef.current || typeof readerRef.current.requestFullscreen !== "function") {
      setPolicyFeedback("O modo de tela cheia não está disponível neste navegador.");
      return;
    }
    try {
      if (document.fullscreenElement === readerRef.current) await document.exitFullscreen();
      else await readerRef.current.requestFullscreen();
    } catch {
      setPolicyFeedback("O modo de tela cheia não está disponível neste navegador.");
    }
  };

  const toggleDoubleTapZoom = () => setZoom((current) => current > MIN_ZOOM ? MIN_ZOOM : 2);

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "touch") return;
    const now = Date.now();
    if (now - lastTouchTap.current < 300) toggleDoubleTapZoom();
    lastTouchTap.current = now;
  };

  const markPageFailed = (page: number) => {
    setFailedPages((current) => new Set(current).add(page));
  };

  const retryPage = (page: number) => {
    setFailedPages((current) => {
      const next = new Set(current);
      next.delete(page);
      return next;
    });
  };

  const baseWidth = Math.min(Math.max(viewportWidth - (viewportWidth < 680 ? 24 : 48), 280), 1040);
  const renderedPageWidth = Math.round(baseWidth * zoom);

  return <div className="protected-pdf">
    {manifest === "loading"
      ? <p className="protected-pdf-state" role="status">Preparando visualização protegida…</p>
      : manifest === "error"
        ? <div className="protected-pdf-state" role="alert"><p>Não foi possível preparar este PDF.</p><button type="button" className="secondary-button" onClick={() => void loadManifest()}>Tentar novamente</button></div>
        : <>
            <div
              className="protected-pdf-page protected-pdf-preview"
              onContextMenu={(event) => event.preventDefault()}
              aria-label={`${asset.title}, página ${pageNumber} de ${pageCount}`}
            >
              {previewError
                ? <div className="protected-pdf-state" role="alert"><p>Não foi possível carregar esta página.</p><button type="button" className="secondary-button" onClick={() => setPreviewError(false)}>Tentar novamente</button></div>
                // Private, authenticated, no-store raster pages cannot use the optimizing image proxy.
                // eslint-disable-next-line @next/next/no-img-element
                : <img
                    src={pageHref(asset.view_href, pageNumber)}
                    alt={`${asset.title} — página ${pageNumber} de ${pageCount}`}
                    draggable={false}
                    loading="eager"
                    referrerPolicy="no-referrer"
                    onDragStart={(event) => event.preventDefault()}
                    onError={() => setPreviewError(true)}
                  />}
              <button
                ref={openerRef}
                type="button"
                className="protected-pdf-open"
                onClick={() => setReaderOpen(true)}
              >
                <ReaderIcon name="expand"/>
                Abrir leitor
              </button>
            </div>
            <nav className="protected-pdf-pagination" aria-label="Páginas do PDF">
              <button type="button" className="secondary-button" disabled={pageNumber === 1} onClick={() => selectPage(pageNumber - 1)}><ReaderIcon name="previous"/> Anterior</button>
              <span aria-live="polite">Página {pageNumber} de {pageCount}</span>
              <button type="button" className="secondary-button" disabled={pageNumber === pageCount} onClick={() => selectPage(pageNumber + 1)}>Próxima <ReaderIcon name="next"/></button>
            </nav>
          </>}
    {asset.download_href
      ? <a className="secondary-button" href={asset.download_href}>Baixar PDF de apoio</a>
      : <p className="asset-policy-note" role="note">Download desativado pelo administrador.</p>}
    <p className="asset-policy-feedback" aria-live="polite">{policyFeedback}</p>

    {readerOpen && typeof manifest === "object"
      ? <section
          ref={readerRef}
          className="pdf-reader"
          role="dialog"
          aria-modal="true"
          aria-label={`Leitor de ${asset.title}`}
        >
          <header className="pdf-reader-toolbar">
            <div className="pdf-reader-identity">
              <button type="button" className="pdf-reader-back" aria-label="Voltar ao curso" onClick={closeReader} autoFocus>
                <ReaderIcon name="back"/>
                <span>Voltar ao curso</span>
              </button>
              <strong title={asset.title}>{asset.title}</strong>
            </div>
            <nav className="pdf-reader-page-controls" aria-label="Navegação de páginas">
              <button type="button" className="pdf-reader-icon-button" disabled={pageNumber === 1} aria-label="Página anterior" onClick={() => selectPage(pageNumber - 1)}><ReaderIcon name="previous"/></button>
              <label className="pdf-reader-page-field">
                <span className="sr-only">Ir para página</span>
                <input
                  type="number"
                  min={1}
                  max={pageCount}
                  value={pageDraft}
                  onChange={(event) => setPageDraft(event.target.value)}
                  onBlur={commitPageDraft}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      commitPageDraft();
                      event.currentTarget.blur();
                    }
                  }}
                  aria-label="Ir para página"
                />
                <span>de {pageCount}</span>
              </label>
              <button type="button" className="pdf-reader-icon-button" disabled={pageNumber === pageCount} aria-label="Próxima página" onClick={() => selectPage(pageNumber + 1)}><ReaderIcon name="next"/></button>
            </nav>
            <nav className="pdf-reader-zoom-controls" aria-label="Controles de zoom">
              <button type="button" className="pdf-reader-icon-button" disabled={zoom <= MIN_ZOOM} aria-label="Diminuir zoom" onClick={() => changeZoom(-ZOOM_STEP)}><ReaderIcon name="minus"/></button>
              <span aria-live="polite">{Math.round(zoom * 100)}%</span>
              <button type="button" className="pdf-reader-icon-button" disabled={zoom >= MAX_ZOOM} aria-label="Aumentar zoom" onClick={() => changeZoom(ZOOM_STEP)}><ReaderIcon name="plus"/></button>
              <button type="button" className="pdf-reader-icon-button" disabled={zoom === MIN_ZOOM} aria-label="Ajustar à largura" onClick={fitWidth}><ReaderIcon name="fit"/></button>
              <button type="button" className="pdf-reader-icon-button pdf-reader-fullscreen" aria-label={fullscreenActive ? "Sair da tela cheia" : "Tela cheia"} onClick={() => void toggleFullscreen()}><ReaderIcon name={fullscreenActive ? "collapse" : "expand"}/></button>
            </nav>
          </header>
          <div
            ref={viewportRef}
            className="pdf-reader-viewport"
            onContextMenu={(event) => event.preventDefault()}
            onDoubleClick={toggleDoubleTapZoom}
            onPointerUp={handlePointerUp}
            tabIndex={0}
          >
            <div className="pdf-reader-pages">
              {Array.from({ length: manifest.pageCount }, (_, index) => index + 1).map((page) =>
                <figure
                  key={page}
                  ref={(element) => {
                    if (element) pageElements.current.set(page, element);
                    else pageElements.current.delete(page);
                  }}
                  className="pdf-reader-page"
                  data-page={page}
                  style={{ width: `${renderedPageWidth}px` }}
                  aria-label={`Página ${page} de ${manifest.pageCount}`}
                >
                  {failedPages.has(page)
                    ? <div className="protected-pdf-state" role="alert"><p>Não foi possível carregar a página {page}.</p><button type="button" className="secondary-button" onClick={() => retryPage(page)}>Tentar novamente</button></div>
                    // Private, authenticated, no-store raster pages cannot use the optimizing image proxy.
                    // eslint-disable-next-line @next/next/no-img-element
                    : <img
                        src={pageHref(asset.view_href, page)}
                        alt={`${asset.title} — página ${page} de ${manifest.pageCount}`}
                        draggable={false}
                        loading={Math.abs(page - pageNumber) <= 1 ? "eager" : "lazy"}
                        referrerPolicy="no-referrer"
                        onDragStart={(event) => event.preventDefault()}
                        onError={() => markPageFailed(page)}
                      />}
                </figure>)}
              <p className="pdf-reader-touch-hint">Use dois dedos para ampliar</p>
            </div>
          </div>
          <p className="pdf-reader-feedback" aria-live="polite">{policyFeedback}</p>
        </section>
      : null}
  </div>;
}

function ReaderIcon({ name }: {
  name: "back" | "previous" | "next" | "minus" | "plus" | "fit" | "expand" | "collapse";
}) {
  if (name === "minus" || name === "plus") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h14"/>{name === "plus" ? <path d="M12 5v14"/> : null}</svg>;
  }
  if (name === "fit") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 5H5v14h2M17 5h2v14h-2M9 12h6"/></svg>;
  }
  if (name === "expand" || name === "collapse") {
    const outward = name === "expand";
    return <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d={outward ? "M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" : "M4 9h5V4M20 9h-5V4M4 15h5v5M20 15h-5v5"}/>
    </svg>;
  }
  if (name === "back") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6M9 12h10"/></svg>;
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d={name === "previous" ? "m14 18-6-6 6-6" : "m10 6 6 6-6 6"}/></svg>;
}

function validPdfManifest(
  value: unknown,
  expectedAssetId: string | undefined
): value is { asset_id: string; page_count: number } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).sort().join(",") === "asset_id,page_count" &&
    typeof expectedAssetId === "string" && record.asset_id === expectedAssetId &&
    Number.isSafeInteger(record.page_count) && Number(record.page_count) >= 1 &&
    Number(record.page_count) <= 100;
}

function restoreReaderSession(storageKey: string, pageCount: number) {
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "null");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid");
    const record = parsed as Record<string, unknown>;
    const page = Number(record.page);
    const zoom = Number(record.zoom);
    if (record.version !== 1 || !Number.isSafeInteger(page) || page < 1 || page > pageCount ||
      !Number.isFinite(zoom) || zoom < MIN_ZOOM || zoom > MAX_ZOOM) throw new Error("invalid");
    return { page, zoom: clampZoom(zoom) };
  } catch {
    return { page: 1, zoom: MIN_ZOOM };
  }
}

function clampZoom(value: number) {
  return Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value)) * 100) / 100;
}

function touchDistance(touches: TouchList) {
  const first = touches.item(0);
  const second = touches.item(1);
  if (!first || !second) return 1;
  return Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY);
}

function pageHref(base: string, page: number) {
  return `${base}/${page}`;
}
