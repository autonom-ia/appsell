/**
 * Salvar sozinho, sem perder o que foi digitado no meio (#649).
 *
 * `value` é o que está no campo; `saved`, o que o servidor confirmou;
 * `inFlight`, o que está indo agora. O que se digita durante um salvamento
 * fica em `value` e vira o próximo salvamento.
 */

export type AutosaveStatus = "idle" | "pending" | "saving" | "saved" | "failed";
export type AutosaveFailure = "offline" | "invalid" | "conflict" | "forbidden" | "error";

export interface AutosaveState {
  status: AutosaveStatus;
  value: string;
  saved: string;
  inFlight: string | null;
  reason: AutosaveFailure | null;
}

export type AutosaveAction =
  | { type: "edit"; value: string }
  | { type: "start" }
  | { type: "success"; value: string }
  | { type: "failure"; reason: AutosaveFailure }
  | { type: "external"; value: string };

export function initialAutosave(value: string): AutosaveState {
  return { status: "idle", value, saved: value, inFlight: null, reason: null };
}

export function autosaveReducer(state: AutosaveState, action: AutosaveAction): AutosaveState {
  switch (action.type) {
    case "edit": {
      if (state.status === "saving") return { ...state, value: action.value };
      const status = action.value === state.saved ? "idle" : "pending";
      return { ...state, value: action.value, status, reason: null };
    }
    case "start":
      return { ...state, status: "saving", inFlight: state.value, reason: null };
    case "success": {
      const dirty = state.value !== action.value;
      return { ...state, saved: action.value, inFlight: null, status: dirty ? "pending" : "saved", reason: null };
    }
    case "failure":
      return { ...state, status: "failed", inFlight: null, reason: action.reason };
    case "external": {
      const editing = state.value !== state.saved || state.status === "saving";
      if (editing) return { ...state, saved: action.value };
      return { ...state, value: action.value, saved: action.value, status: "idle" };
    }
  }
}

/** Precisa mandar ao servidor o que está no campo agora. */
export function needsSave(state: AutosaveState): boolean {
  return state.status === "pending" && state.value !== state.saved;
}
