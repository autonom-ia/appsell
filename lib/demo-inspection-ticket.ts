const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const OPAQUE_TOKEN = /^[A-Za-z0-9_-]{43,128}$/;

export interface DemoInspectionTicket {
  id: string;
  token: string;
}

export function serializeDemoInspectionTicket(ticket: DemoInspectionTicket) {
  if (!UUID.test(ticket.id) || !OPAQUE_TOKEN.test(ticket.token)) {
    throw new Error("invalid_demo_inspection_ticket");
  }
  return `${ticket.id}.${ticket.token}`;
}

export function parseDemoInspectionTicket(value: string | null | undefined) {
  if (!value || value.length > 166) return null;
  const separator = value.indexOf(".");
  if (separator < 0 || separator !== value.lastIndexOf(".")) return null;
  const ticket = {
    id: value.slice(0, separator),
    token: value.slice(separator + 1)
  };
  return UUID.test(ticket.id) && OPAQUE_TOKEN.test(ticket.token) ? ticket : null;
}
