"use client";

import { createPortal } from "react-dom";
import { KeyboardEvent, ReactNode, RefObject, useEffect, useRef } from "react";

interface AccessibleDialogProps {
  children: ReactNode;
  titleId: string;
  descriptionId?: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  onClose: () => void;
}

const focusableSelector = "a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";

export function AccessibleDialog({ children, titleId, descriptionId, initialFocusRef, returnFocusRef, onClose }: AccessibleDialogProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const backdrop = backdropRef.current;
    if (!backdrop) return;
    const returnFocus = returnFocusRef?.current;
    const siblings = [...document.body.children].filter((element) => element !== backdrop);
    const previous = siblings.map((element) => ({ element: element as HTMLElement, inert: (element as HTMLElement).inert, ariaHidden: element.getAttribute("aria-hidden") }));
    for (const { element } of previous) {
      element.inert = true;
      element.setAttribute("aria-hidden", "true");
    }
    (initialFocusRef?.current ?? dialogRef.current?.querySelector<HTMLElement>(focusableSelector) ?? dialogRef.current)?.focus();
    return () => {
      for (const { element, inert, ariaHidden } of previous) {
        element.inert = inert;
        if (ariaHidden === null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
      }
      queueMicrotask(() => returnFocus?.focus());
    };
  }, [initialFocusRef, returnFocusRef]);

  function trapFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])].filter((element) => element.offsetParent !== null);
    if (!controls.length) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (typeof document === "undefined") return null;
  return createPortal(
    <div ref={backdropRef} className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialogRef} className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1} onKeyDown={trapFocus}>{children}</div>
    </div>,
    document.body
  );
}
