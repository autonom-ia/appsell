"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useProtectedPdfStudy } from "./protected-pdf-study";

interface ProtectedPdfAsset {
  view_href: string;
  download_href: string | null;
  title: string;
}

interface PdfLinkRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

type PdfLink = {
  id: string;
  page: number;
  rect: PdfLinkRect;
} & (
  | { kind: "page"; target_page: number }
  | { kind: "video"; media_href: string }
  | { kind: "external"; href: string }
);

type ManifestState = { pageCount: number; links: PdfLink[] } | "loading" | "error";

interface CachedPdfPage {
  url: string;
  bytes: number;
  touchedAt: number;
}

class PdfPageLoadRejected extends Error {}

const TENANT_PDF_VIEW_HREF = /^\/api\/library\/items\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/pages$/;
const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;
const READER_PAGE_RADIUS = 1;
const PDF_PAGE_HEIGHT_RATIO = 297 / 210;
const PDF_MEMORY_CACHE_BYTES = 64 * 1024 * 1024;
const MAX_PDF_PAGE_BYTES = 8 * 1024 * 1024;
const PDF_PAGE_LOAD_ATTEMPTS = 4;
const PDF_PAGE_RETRY_DELAYS_MS = [1_000, 2_000, 4_000];

export function ProtectedPdfReader({ asset }: { asset: ProtectedPdfAsset }) {
  const [manifest, setManifest] = useState<ManifestState>("loading");
  const [pageNumber, setPageNumber] = useState(1);
  const [pageDraft, setPageDraft] = useState("1");
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [readerOpen, setReaderOpen] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [failedPages, setFailedPages] = useState<Set<number>>(() => new Set());
  const [cachedPages, setCachedPages] = useState<Map<number, string>>(() => new Map());
  const [policyFeedback, setPolicyFeedback] = useState("");
  const [viewportWidth, setViewportWidth] = useState(0);
  const [pageHeightRatio, setPageHeightRatio] = useState(PDF_PAGE_HEIGHT_RATIO);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const [panning, setPanning] = useState(false);
  const [activeVideoLink, setActiveVideoLink] = useState<Extract<PdfLink, { kind: "video" }> | null>(null);
  const readerRef = useRef<HTMLElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const pageElements = useRef(new Map<number, HTMLElement>());
  const pinchStartDistance = useRef<number | null>(null);
  const pinchStartZoom = useRef(MIN_ZOOM);
  const pinchStartCenter = useRef<{ x: number; y: number } | null>(null);
  const pinchStartScroll = useRef<{ left: number; top: number } | null>(null);
  const zoomRef = useRef(MIN_ZOOM);
  const lastTouchTap = useRef(0);
  const pageNumberRef = useRef(1);
  const programmaticPage = useRef<number | null>(null);
  const pageRatioCaptured = useRef(false);
  const pageCache = useRef(new Map<number, CachedPdfPage>());
  const pageCacheBytes = useRef(0);
  const pageLoads = useRef(new Map<number, Promise<string>>());
  const pageLoadControllers = useRef(new Set<AbortController>());
  const pageCacheGeneration = useRef(0);
  const panPointer = useRef<{
    id: number;
    x: number;
    y: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);

  const pageCount = typeof manifest === "object" ? manifest.pageCount : 0;
  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index + 1),
    [pageCount]
  );
  const storageKey = useMemo(
    () => `sell:protected-pdf:v1:${asset.view_href}`,
    [asset.view_href]
  );

  const clearProtectedPageCache = useCallback((updateState = true) => {
    pageCacheGeneration.current += 1;
    pageLoadControllers.current.forEach((controller) => controller.abort());
    pageLoadControllers.current.clear();
    pageLoads.current.clear();
    pageCache.current.forEach((entry) => URL.revokeObjectURL(entry.url));
    pageCache.current.clear();
    pageCacheBytes.current = 0;
    if (updateState) setCachedPages(new Map());
  }, []);

  const loadProtectedPage = useCallback((page: number): Promise<string> => {
    const cached = pageCache.current.get(page);
    if (cached) {
      cached.touchedAt = Date.now();
      return Promise.resolve(cached.url);
    }
    const activeLoad = pageLoads.current.get(page);
    if (activeLoad) return activeLoad;

    const generation = pageCacheGeneration.current;
    const controller = new AbortController();
    pageLoadControllers.current.add(controller);
    const request = (async () => {
      for (let attempt = 0; attempt < PDF_PAGE_LOAD_ATTEMPTS; attempt += 1) {
        try {
          const response = await fetch(pageHref(asset.view_href, page), {
            cache: "no-store",
            credentials: "same-origin",
            referrerPolicy: "no-referrer",
            signal: controller.signal
          });
          if (response.ok) {
            if (!response.headers.get("content-type")?.toLowerCase().startsWith("image/png")) {
              throw new PdfPageLoadRejected("invalid_pdf_page_type");
            }
            const blob = await response.blob();
            if (blob.size < 1 || blob.size > MAX_PDF_PAGE_BYTES) {
              throw new PdfPageLoadRejected("invalid_pdf_page_size");
            }
            if (generation !== pageCacheGeneration.current) throw abortError();
            const url = URL.createObjectURL(blob);
            const entry: CachedPdfPage = { url, bytes: blob.size, touchedAt: Date.now() };
            pageCache.current.set(page, entry);
            pageCacheBytes.current += entry.bytes;
            evictPdfPages(pageCache.current, pageCacheBytes, page);
            setCachedPages(new Map(
              Array.from(pageCache.current, ([pageNumber, cachedPage]) => [pageNumber, cachedPage.url])
            ));
            return url;
          }
          if (!retryablePdfPageResponse(response) || attempt === PDF_PAGE_LOAD_ATTEMPTS - 1) {
            throw new PdfPageLoadRejected(`pdf_page_http_${response.status}`);
          }
          await waitForPdfPageRetry(response, attempt, controller.signal);
        } catch (error) {
          if (isAbortError(error) || error instanceof PdfPageLoadRejected ||
            attempt === PDF_PAGE_LOAD_ATTEMPTS - 1) throw error;
          await waitForPdfPageRetry(null, attempt, controller.signal);
        }
      }
      throw new Error("pdf_page_unavailable");
    })().finally(() => {
      pageLoadControllers.current.delete(controller);
      if (pageLoads.current.get(page) === request) pageLoads.current.delete(page);
    });
    pageLoads.current.set(page, request);
    return request;
  }, [asset.view_href]);

  const loadManifest = useCallback(async () => {
    clearProtectedPageCache();
    setManifest("loading");
    setPolicyFeedback("");
    pageRatioCaptured.current = false;
    setPageHeightRatio(PDF_PAGE_HEIGHT_RATIO);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(asset.view_href, {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      const payload: unknown = response.ok ? await response.json() : null;
      const expectedAssetId = asset.view_href.match(TENANT_PDF_VIEW_HREF)?.[2];
      if (!validPdfManifest(payload, expectedAssetId, asset.view_href)) {
        throw new Error("invalid_pdf_manifest");
      }
      const restored = restoreReaderSession(storageKey, payload.page_count);
      setManifest({ pageCount: payload.page_count, links: payload.links });
      pageNumberRef.current = restored.page;
      setPageNumber(restored.page);
      setPageDraft(String(restored.page));
      setZoom(restored.zoom);
      setPreviewError(false);
      setFailedPages(new Set());
      setActiveVideoLink(null);
    } catch {
      setManifest("error");
    } finally {
      window.clearTimeout(timeout);
    }
  }, [asset.view_href, clearProtectedPageCache, storageKey]);

  useEffect(() => {
    const start = window.setTimeout(() => void loadManifest(), 0);
    return () => window.clearTimeout(start);
  }, [loadManifest]);

  useEffect(() => {
    return () => clearProtectedPageCache(false);
  }, [clearProtectedPageCache]);

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
    pageNumberRef.current = pageNumber;
  }, [pageNumber]);

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  const closeReader = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    setActiveVideoLink(null);
    clearProtectedPageCache();
    setReaderOpen(false);
    window.setTimeout(() => openerRef.current?.focus(), 0);
  }, [clearProtectedPageCache]);

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
    if (readerOpen) {
      void loadProtectedPage(nextPage).catch((error: unknown) => {
        if (!isAbortError(error)) setFailedPages((current) => new Set(current).add(nextPage));
      });
    }
    pageNumberRef.current = nextPage;
    setPageNumber(nextPage);
    setPageDraft(String(nextPage));
    setPreviewError(false);
    setPolicyFeedback("");
    if (readerOpen) {
      programmaticPage.current = nextPage;
      scrollToPage(nextPage);
    }
  }, [loadProtectedPage, pageCount, readerOpen, scrollToPage]);

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
      if (event.key === "Escape") {
        if (activeVideoLink) setActiveVideoLink(null);
        else closeReader();
        return;
      }
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
  }, [activeVideoLink, changeZoom, closeReader, pageNumber, readerOpen, selectPage]);

  useEffect(() => {
    if (!readerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [readerOpen]);

  useEffect(() => {
    if (!readerOpen || pageCount === 0) return;
    let cancelled = false;
    const prioritizedPages = Array.from({ length: pageCount }, (_, index) => index + 1)
      .sort((left, right) => {
        const distance = Math.abs(left - pageNumber) - Math.abs(right - pageNumber);
        return distance === 0 ? left - right : distance;
      });
    const loadPages = async () => {
      for (const page of prioritizedPages) {
        if (cancelled) return;
        try {
          await loadProtectedPage(page);
          if (cancelled) return;
          setFailedPages((current) => {
            if (!current.has(page)) return current;
            const next = new Set(current);
            next.delete(page);
            return next;
          });
        } catch (error) {
          if (cancelled || isAbortError(error)) return;
          if (Math.abs(page - pageNumber) <= READER_PAGE_RADIUS) {
            setFailedPages((current) => new Set(current).add(page));
          }
        }
      }
    };
    void loadPages();
    return () => {
      cancelled = true;
    };
  }, [loadProtectedPage, pageCount, pageNumber, readerOpen]);

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
    const viewport = viewportRef.current;
    let settleTimer = 0;
    const settleVisiblePage = () => {
      if (programmaticPage.current !== null) {
        programmaticPage.current = null;
        return;
      }
      const viewportBounds = viewport.getBoundingClientRect();
      const viewportCenter = viewportBounds.top + viewportBounds.height / 2;
      let visiblePage = pageNumberRef.current;
      let nearestDistance = Number.POSITIVE_INFINITY;
      pageElements.current.forEach((element, page) => {
        const bounds = element.getBoundingClientRect();
        if (bounds.bottom <= viewportBounds.top || bounds.top >= viewportBounds.bottom) return;
        const distance = Math.abs(bounds.top + bounds.height / 2 - viewportCenter);
        if (distance < nearestDistance) {
          visiblePage = page;
          nearestDistance = distance;
        }
      });
      if (visiblePage !== pageNumberRef.current) {
        pageNumberRef.current = visiblePage;
        setPageNumber(visiblePage);
        setPageDraft(String(visiblePage));
      }
    };
    const scheduleVisiblePage = () => {
      window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(settleVisiblePage, 180);
    };
    viewport.addEventListener("scroll", scheduleVisiblePage, { passive: true });
    return () => {
      window.clearTimeout(settleTimer);
      viewport.removeEventListener("scroll", scheduleVisiblePage);
    };
  }, [pageCount, readerOpen]);

  useEffect(() => {
    if (!readerOpen || viewportWidth <= 0) return;
    const frame = window.requestAnimationFrame(() => {
      programmaticPage.current = pageNumberRef.current;
      scrollToPage(pageNumberRef.current, false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [readerOpen, scrollToPage, viewportWidth]);

  useEffect(() => {
    if (!readerOpen || !viewportRef.current) return;
    const viewport = viewportRef.current;
    const touchStart = (event: TouchEvent) => {
      if (event.touches.length !== 2) return;
      pinchStartDistance.current = touchDistance(event.touches);
      pinchStartZoom.current = zoomRef.current;
      pinchStartCenter.current = touchCenter(event.touches);
      pinchStartScroll.current = { left: viewport.scrollLeft, top: viewport.scrollTop };
    };
    const touchMove = (event: TouchEvent) => {
      if (event.touches.length !== 2 || pinchStartDistance.current === null) return;
      event.preventDefault();
      const scale = touchDistance(event.touches) / pinchStartDistance.current;
      setZoom(clampZoom(pinchStartZoom.current * scale));
      const center = touchCenter(event.touches);
      if (pinchStartCenter.current && pinchStartScroll.current) {
        viewport.scrollLeft = pinchStartScroll.current.left -
          (center.x - pinchStartCenter.current.x);
        viewport.scrollTop = pinchStartScroll.current.top -
          (center.y - pinchStartCenter.current.y);
      }
    };
    const touchEnd = () => {
      pinchStartDistance.current = null;
      pinchStartZoom.current = zoomRef.current;
      pinchStartCenter.current = null;
      pinchStartScroll.current = null;
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

  const retryPage = (page: number) => {
    setFailedPages((current) => {
      const next = new Set(current);
      next.delete(page);
      return next;
    });
    void loadProtectedPage(page).catch((error: unknown) => {
      if (!isAbortError(error)) {
        setFailedPages((current) => new Set(current).add(page));
      }
    });
  };

  const capturePageHeightRatio = (image: HTMLImageElement) => {
    if (pageRatioCaptured.current || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;
    const ratio = image.naturalHeight / image.naturalWidth;
    if (!Number.isFinite(ratio) || ratio < 1.1 || ratio > 2) return;
    pageRatioCaptured.current = true;
    setPageHeightRatio(ratio);
  };

  const baseWidth = Math.min(Math.max(viewportWidth - (viewportWidth < 680 ? 24 : 48), 280), 1040);
  const renderedPageWidth = Math.round(baseWidth * zoom);
  const study = useProtectedPdfStudy({
    viewHref: asset.view_href,
    pageCount,
    activePage: pageNumber,
    pageHeightRatio,
    open: readerOpen,
    onPage: selectPage
  });

  const startHandPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (study.tool !== "hand" || event.pointerType === "touch" || event.button !== 0) return;
    const target = event.target;
    if (target instanceof Element && target.closest("a,button,input,video,[role='button']")) return;
    panPointer.current = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: event.currentTarget.scrollLeft,
      scrollTop: event.currentTarget.scrollTop
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanning(true);
    event.preventDefault();
  };

  const moveHandPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panPointer.current;
    if (!pan || pan.id !== event.pointerId) return;
    event.currentTarget.scrollLeft = pan.scrollLeft - (event.clientX - pan.x);
    event.currentTarget.scrollTop = pan.scrollTop - (event.clientY - pan.y);
    event.preventDefault();
  };

  const finishHandPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (panPointer.current?.id === event.pointerId) {
      panPointer.current = null;
      setPanning(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
    handlePointerUp(event);
  };

  const cancelHandPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (panPointer.current?.id !== event.pointerId) return;
    panPointer.current = null;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

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
              {readerOpen
                ? null
                : previewError
                ? <div className="protected-pdf-state" role="alert"><p>Não foi possível carregar esta página.</p><button type="button" className="secondary-button" onClick={() => setPreviewError(false)}>Tentar novamente</button></div>
                // Private, authenticated, no-store raster pages cannot use the optimizing image proxy.
                // eslint-disable-next-line @next/next/no-img-element
                : <img
                    src={pageHref(asset.view_href, pageNumber)}
                    alt={`${asset.title} — página ${pageNumber} de ${pageCount}`}
                    draggable={false}
                    loading="eager"
                    referrerPolicy="no-referrer"
                    onLoad={(event) => capturePageHeightRatio(event.currentTarget)}
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
          {study.toolbar}
          <div
            ref={viewportRef}
            className={`pdf-reader-viewport pdf-study-mode-${study.tool}${panning ? " is-panning" : ""}`}
            onContextMenu={(event) => event.preventDefault()}
            onDoubleClick={toggleDoubleTapZoom}
            onPointerDown={startHandPan}
            onPointerMove={moveHandPan}
            onPointerUp={finishHandPan}
            onPointerCancel={cancelHandPan}
            tabIndex={0}
          >
            <div className="pdf-reader-pages">
              {pageNumbers.map((page) =>
                <figure
                  key={page}
                  ref={(element) => {
                    if (element) pageElements.current.set(page, element);
                    else pageElements.current.delete(page);
                  }}
                  className="pdf-reader-page"
                  data-page={page}
                  style={{
                    width: `${renderedPageWidth}px`,
                    minHeight: `${Math.round(renderedPageWidth * pageHeightRatio)}px`
                  }}
                  aria-label={`Página ${page} de ${manifest.pageCount}`}
                >
                  {failedPages.has(page)
                    ? <div className="protected-pdf-state" role="alert"><p>Não foi possível carregar a página {page}.</p><button type="button" className="secondary-button" onClick={() => retryPage(page)}>Tentar novamente</button></div>
                    : Math.abs(page - pageNumber) > READER_PAGE_RADIUS
                      ? <span className="pdf-reader-page-placeholder" aria-hidden="true"/>
                      : cachedPages.has(page)
                        ? <>
                          {/* Private, authenticated, no-store raster pages cannot use the optimizing image proxy. */}
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={cachedPages.get(page)}
                            alt={`${asset.title} — página ${page} de ${manifest.pageCount}`}
                            draggable={false}
                            loading="eager"
                            referrerPolicy="no-referrer"
                            onLoad={(event) => capturePageHeightRatio(event.currentTarget)}
                            onDragStart={(event) => event.preventDefault()}
                          />
                          <PdfLinkLayer
                            links={manifest.links.filter((link) => link.page === page)}
                            onPage={selectPage}
                            onVideo={setActiveVideoLink}
                          />
                          {study.layer(page)}
                        </>
                        : <span className="pdf-reader-page-placeholder" role="status" aria-label={`Carregando página ${page}`}/>}
                </figure>)}
              <p className="pdf-reader-touch-hint">Use dois dedos para ampliar</p>
            </div>
          </div>
          <p className="pdf-reader-feedback" aria-live="polite">{policyFeedback}</p>
          {study.panel}
          {activeVideoLink
            ? <PdfVideoDialog key={activeVideoLink.media_href} link={activeVideoLink} onClose={() => setActiveVideoLink(null)}/>
            : null}
        </section>
      : null}
  </div>;
}

function PdfLinkLayer({ links, onPage, onVideo }: {
  links: PdfLink[];
  onPage: (page: number) => void;
  onVideo: (link: Extract<PdfLink, { kind: "video" }>) => void;
}) {
  if (links.length === 0) return null;
  return <div className="pdf-reader-link-layer" aria-label="Links desta página">
    {links.map((link) => {
      const style = {
        left: `${link.rect.left}%`,
        top: `${link.rect.top}%`,
        width: `${link.rect.width}%`,
        height: `${link.rect.height}%`
      };
      if (link.kind === "page") {
        return <button
          key={link.id}
          type="button"
          className="pdf-reader-link pdf-reader-page-link"
          style={style}
          aria-label={`Ir para a página ${link.target_page}`}
          title={`Ir para a página ${link.target_page}`}
          onPointerUp={(event) => event.stopPropagation()}
          onClick={() => onPage(link.target_page)}
        />;
      }
      if (link.kind === "video") {
        return <button
          key={link.id}
          type="button"
          className="pdf-reader-link pdf-reader-video-link"
          style={style}
          aria-label={`Reproduzir vídeo complementar da página ${link.page}`}
          title="Reproduzir vídeo dentro do Sell"
          onPointerUp={(event) => event.stopPropagation()}
          onClick={() => onVideo(link)}
        ><span aria-hidden="true">▶</span></button>;
      }
      return <a
        key={link.id}
        className="pdf-reader-link pdf-reader-external-link"
        style={style}
        href={link.href}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Abrir referência externa em uma nova aba"
        title="Abrir referência externa"
        onPointerUp={(event) => event.stopPropagation()}
      />;
    })}
  </div>;
}

/** Quantas vezes vale perguntar se o vídeo já ficou pronto antes de desistir. */
const VIDEO_PREPARATION_ATTEMPTS = 60;

/**
 * O vídeo do PDF, que o servidor prepara antes de entregar.
 *
 * Pôr o endereço direto no `<video>` não serve mais: enquanto o MP4 está sendo
 * preparado a rota responde `202`, e o elemento trata qualquer resposta que não
 * seja mídia como erro — o aluno via "não foi possível" no exato momento em que
 * bastava esperar.
 *
 * Então o diálogo pergunta primeiro, e só entrega o endereço ao `<video>`
 * quando há vídeo do outro lado.
 */
function PdfVideoDialog({ link, onClose }: {
  link: Extract<PdfLink, { kind: "video" }>;
  onClose: () => void;
}) {
  const [state, setState] = useState<"checking" | "preparing" | "ready" | "failed">("checking");
  const [attempt, setAttempt] = useState(0);

  // Sem efeito de reinício: o diálogo é remontado por `key={media_href}`, então
  // trocar de vídeo já começa do zero. Reiniciar estado dentro de um efeito
  // renderiza uma vez com o estado do vídeo anterior antes de corrigir.
  useEffect(() => {
    if (state === "ready" || state === "failed") return;
    let cancelled = false;
    let timer: number | null = null;
    (async () => {
      try {
        const response = await fetch(link.media_href, { method: "HEAD", cache: "no-store" });
        if (cancelled) return;
        if (response.status === 200 || response.status === 206) {
          setState("ready");
          return;
        }
        if (response.status !== 202 || attempt >= VIDEO_PREPARATION_ATTEMPTS) {
          setState("failed");
          return;
        }
        const retry = Number(response.headers.get("retry-after"));
        const seconds = Number.isSafeInteger(retry) && retry >= 1 && retry <= 60 ? retry : 3;
        setState("preparing");
        timer = window.setTimeout(() => {
          if (!cancelled) setAttempt((current) => current + 1);
        }, seconds * 1_000);
      } catch {
        if (!cancelled) setState("failed");
      }
    })();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
    // `attempt` é o que reagenda a pergunta; `state` não entra para não
    // reiniciar o ciclo a cada transição.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  return <section
    className="pdf-video-dialog"
    role="dialog"
    aria-modal="true"
    aria-label={`Vídeo complementar da página ${link.page}`}
    onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}
  >
    <div className="pdf-video-card">
      <header>
        <div>
          <p className="eyebrow">Vídeo complementar</p>
          <strong>Página {link.page}</strong>
        </div>
        <button type="button" className="pdf-video-close" onClick={onClose} autoFocus>
          Fechar
        </button>
      </header>
      {state === "failed"
        ? <div className="pdf-video-state" role="alert">
            <p>Não foi possível reproduzir este vídeo.</p>
            <button
              type="button"
              className="secondary-button"
              onClick={() => { setAttempt(0); setState("checking"); }}
            >
              Tentar novamente
            </button>
          </div>
        : state !== "ready"
        ? <div className="pdf-video-state" role="status" aria-live="polite">
            <p>Preparando o vídeo para tocar no seu navegador…</p>
            <p className="pdf-video-hint">Isto acontece uma vez por vídeo.</p>
          </div>
        : <video
            key={link.media_href}
            className="pdf-linked-video"
            controls
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            playsInline
            preload="metadata"
            src={link.media_href}
            aria-label={`Vídeo complementar da página ${link.page}`}
            onContextMenu={(event) => event.preventDefault()}
            onError={() => setState("failed")}
          >Seu navegador não consegue reproduzir este vídeo.</video>}
      <p className="pdf-video-policy">Reprodução autenticada dentro do Sell. Download não é disponibilizado.</p>
    </div>
  </section>;
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
  expectedAssetId: string | undefined,
  expectedBaseHref: string
): value is { asset_id: string; page_count: number; links: PdfLink[] } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).sort().join(",") === "asset_id,links,page_count" &&
    typeof expectedAssetId === "string" && record.asset_id === expectedAssetId &&
    Number.isSafeInteger(record.page_count) && Number(record.page_count) >= 1 &&
    Number(record.page_count) <= 100 && Array.isArray(record.links) &&
    record.links.length <= 512 && record.links.every((link) =>
      validPdfLink(link, Number(record.page_count), expectedBaseHref)
    );
}

function validPdfLink(value: unknown, pageCount: number, expectedBaseHref: string): value is PdfLink {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const link = value as Record<string, unknown>;
  if (typeof link.id !== "string" || !/^[0-9a-f]{16}$/.test(link.id) ||
    !Number.isSafeInteger(link.page) || Number(link.page) < 1 || Number(link.page) > pageCount ||
    !validPdfLinkRect(link.rect)) return false;
  const common = ["id", "kind", "page", "rect"];
  if (link.kind === "page") {
    return Object.keys(link).sort().join(",") === [...common, "target_page"].sort().join(",") &&
      Number.isSafeInteger(link.target_page) && Number(link.target_page) >= 1 &&
      Number(link.target_page) <= pageCount;
  }
  if (link.kind === "video") {
    return Object.keys(link).sort().join(",") === [...common, "media_href"].sort().join(",") &&
      link.media_href === `${expectedBaseHref}/links/${link.id}/media`;
  }
  if (link.kind !== "external" ||
    Object.keys(link).sort().join(",") !== [...common, "href"].sort().join(",") ||
    typeof link.href !== "string") return false;
  try {
    const href = new URL(link.href);
    return href.protocol === "https:" && !href.username && !href.password;
  } catch {
    return false;
  }
}

function validPdfLinkRect(value: unknown): value is PdfLinkRect {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const rect = value as Record<string, unknown>;
  if (Object.keys(rect).sort().join(",") !== "height,left,top,width") return false;
  const entries = [rect.left, rect.top, rect.width, rect.height];
  return entries.every((entry) => typeof entry === "number" && Number.isFinite(entry) &&
    entry >= 0 && entry <= 100) && Number(rect.width) > 0 && Number(rect.height) > 0 &&
    Number(rect.left) + Number(rect.width) <= 100.01 &&
    Number(rect.top) + Number(rect.height) <= 100.01;
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

function touchCenter(touches: TouchList) {
  const first = touches.item(0);
  const second = touches.item(1);
  if (!first || !second) return { x: 0, y: 0 };
  return { x: (first.clientX + second.clientX) / 2, y: (first.clientY + second.clientY) / 2 };
}

function pageHref(base: string, page: number) {
  return `${base}/${page}`;
}

function retryablePdfPageResponse(response: Response) {
  return response.status === 429 || response.status >= 500;
}

function waitForPdfPageRetry(response: Response | null, attempt: number, signal: AbortSignal) {
  const retryAfter = response?.headers.get("retry-after");
  let delay = PDF_PAGE_RETRY_DELAYS_MS[Math.min(attempt, PDF_PAGE_RETRY_DELAYS_MS.length - 1)];
  if (retryAfter) {
    const seconds = Number(retryAfter);
    const parsed = Number.isFinite(seconds)
      ? seconds * 1_000
      : Date.parse(retryAfter) - Date.now();
    if (Number.isFinite(parsed)) delay = Math.min(5_000, Math.max(250, parsed));
  }
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, delay);
    const abort = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(abortError());
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

function evictPdfPages(
  cache: Map<number, CachedPdfPage>,
  totalBytes: { current: number },
  protectedPage: number
) {
  while (totalBytes.current > PDF_MEMORY_CACHE_BYTES) {
    let oldest: [number, CachedPdfPage] | undefined;
    cache.forEach((entry, page) => {
      if (page === protectedPage || (oldest && entry.touchedAt >= oldest[1].touchedAt)) return;
      oldest = [page, entry];
    });
    if (!oldest) return;
    cache.delete(oldest[0]);
    totalBytes.current -= oldest[1].bytes;
    URL.revokeObjectURL(oldest[1].url);
  }
}

function abortError() {
  return new DOMException("PDF page load aborted", "AbortError");
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
