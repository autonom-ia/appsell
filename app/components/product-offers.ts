// Leitura e mensagens do painel de ofertas do produto (#553, fase 2b).
//
// A api é a única fonte da flag `multi_offer_enabled`. Este módulo só lê o que
// ela mandou: qualquer resposta fora do formato desliga o painel, e o editor
// fica idêntico ao de hoje.

export type OfferStatus = "draft" | "active" | "archived";

export interface ProductOfferSummary {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly status: OfferStatus;
  readonly version: number;
  readonly isDefault: boolean;
  readonly unitAmountMinor: number | null;
  readonly currency: string;
  readonly billingPeriod: string;
}

export interface MultiOfferState {
  readonly enabled: boolean;
  readonly offers: readonly ProductOfferSummary[];
}

const DISABLED: MultiOfferState = Object.freeze({ enabled: false, offers: Object.freeze([]) });
const STATUSES: ReadonlySet<string> = new Set(["draft", "active", "archived"]);

/** Mesmo limite da api (P4094): a padrão mais nove extras. */
export const MAX_OFFERS_PER_PRODUCT = 10;

export const OFFER_CURRENCIES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "BRL", label: "Real (R$)" }
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readOffer(value: unknown): ProductOfferSummary | null {
  if (!isRecord(value)) return null;
  const { id, title, summary, status, version, is_default: isDefault } = value;
  if (typeof id !== "string" || typeof title !== "string" || typeof status !== "string") return null;
  if (!STATUSES.has(status) || typeof version !== "number" || !Number.isSafeInteger(version)) return null;
  if (typeof isDefault !== "boolean") return null;
  const amount = value.unit_amount_minor;
  return Object.freeze({
    id,
    title,
    summary: typeof summary === "string" ? summary : "",
    status: status as OfferStatus,
    version,
    isDefault,
    unitAmountMinor: typeof amount === "number" && Number.isSafeInteger(amount) ? amount : null,
    currency: typeof value.currency === "string" ? value.currency : "BRL",
    billingPeriod: typeof value.billing_period === "string" ? value.billing_period : "one_time"
  });
}

/**
 * Lê `product.multi_offer_enabled` e `product.offers` do detalhe do produto.
 * Só `true` literal liga o painel; uma oferta malformada desliga tudo, para o
 * editor nunca mostrar uma lista pela metade.
 */
export function readMultiOffer(payload: unknown): MultiOfferState {
  if (!isRecord(payload)) return DISABLED;
  const product = isRecord(payload.product) ? payload.product : payload;
  if (product.multi_offer_enabled !== true || !Array.isArray(product.offers)) return DISABLED;
  const offers = product.offers.map(readOffer);
  if (offers.some((offer) => offer === null)) return DISABLED;
  return Object.freeze({ enabled: true, offers: Object.freeze(offers as ProductOfferSummary[]) });
}

/** A padrão primeiro, depois as outras na ordem em que a api mandou. */
export function orderedOffers(offers: readonly ProductOfferSummary[]): ProductOfferSummary[] {
  return [...offers.filter((offer) => offer.isDefault), ...offers.filter((offer) => !offer.isDefault)];
}

export function canCreateOffer(offers: readonly ProductOfferSummary[]): boolean {
  return offers.length < MAX_OFFERS_PER_PRODUCT;
}

/**
 * Pode virar padrão: não é a padrão, não está arquivada e, se a padrão de hoje
 * está à venda, também está à venda. É a mesma regra da api; aqui ela só
 * esconde o botão, quem recusa de fato é o banco (P4093).
 */
export function canBecomeDefault(
  offer: ProductOfferSummary,
  offers: readonly ProductOfferSummary[]
): boolean {
  if (offer.isDefault || offer.status === "archived") return false;
  const current = offers.find((item) => item.isDefault);
  return !(current?.status === "active" && offer.status !== "active");
}

/** Mensagem para cada recusa da api, pelo código exato que ela devolve. */
export function offerErrorMessage(status: number, code: string | undefined): string {
  if (code === "multi_offer_disabled") return "Esta loja ainda não pode ter mais de uma oferta.";
  if (code === "multi_offer_misconfigured") return "Ofertas extras indisponíveis agora. Tente mais tarde.";
  if (code === "offer_limit_reached") return `Cada produto pode ter até ${MAX_OFFERS_PER_PRODUCT} ofertas.`;
  if (code === "default_offer_locked") {
    return "A oferta padrão precisa continuar à venda e com o mesmo preço. Escolha uma oferta à venda com o mesmo preço, moeda e cobrança.";
  }
  if (status === 409) return "A oferta mudou em outra aba. Recarregue e tente de novo.";
  if (status === 422) return "Confira os campos da oferta.";
  if (status === 401 || status === 403) return "Sua sessão não permite esta ação. Entre de novo.";
  return "Não foi possível salvar agora. Tente de novo.";
}
