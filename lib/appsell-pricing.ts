import { createHash } from "node:crypto";

export const APPSELL_VARIABLE_FEE_ROUNDING = "floor_per_transaction" as const;
export const APPSELL_PRICING_CONTRACT_VERSION = "2026-07-12.v3" as const;

export const APPSELL_PRICING_PLANS = {
  launch: { key: "launch", monthlyFeeCents: 0, platformRateBps: 550, platformFixedFeeCents: 49 },
  growth: { key: "growth", monthlyFeeCents: 9_900, platformRateBps: 350, platformFixedFeeCents: 39 },
  scale: { key: "scale", monthlyFeeCents: 39_900, platformRateBps: 190, platformFixedFeeCents: 29 }
} as const;

export type AppSellPlanKey = keyof typeof APPSELL_PRICING_PLANS;
export type PaymentMethod = "card" | "pix" | "boleto";
export type CostEvidenceKind = "measured" | "contracted" | "assumption" | "missing";
export type ConnectChargeType = "direct" | "destination" | "separate_charges_transfers" | "undecided";
export type MerchantOfRecord = "platform" | "connected_account" | "undecided";
export type ConnectPricingControl = "stripe" | "platform" | "undecided";

export interface ApprovedSale {
  transactionId: string;
  ledgerRecordId: string;
  tenantId: string;
  subscriptionId: string;
  amountCents: number;
  paymentMethod: PaymentMethod;
  approvedAt: string;
}

export interface SalesLedgerCoverage {
  snapshotId: string;
  sourceSystem: string;
  tenantId: string;
  subscriptionId: string;
  periodStart: string;
  periodEndExclusive: string;
  timezone: string;
  cutoffAt: string;
  closedAt: string;
  extractedAt: string;
  approvedSalesCount: number;
  approvedGmvCents: number;
  monthlyFeeLedgerRecordId: string;
  monthlyFeeOccurrences: 1;
  monthlyFeeChargedCents: number;
}

export interface AppSellConnectContract {
  chargeType: ConnectChargeType;
  merchantOfRecord: MerchantOfRecord;
  pricingControl: ConnectPricingControl;
  contractReference: string;
}

export interface AppSellPricingScenario {
  scenarioId: string;
  tenantId: string;
  subscriptionId: string;
  planKey: AppSellPlanKey;
  monthlyFeeLedgerRecordId: string;
  monthlyFeeChargedCents: number;
  monthlyFeeOccurrences: 1;
  period: string;
  timezone: string;
  periodStart: string;
  periodEndExclusive: string;
  ledgerCutoffAt: string;
  closingPolicyId: string;
  currency: "BRL";
  connectContract: AppSellConnectContract;
  salesLedger: SalesLedgerCoverage;
  sales: readonly ApprovedSale[];
}

export interface PaymentMethodAllocationCents {
  card: number;
  pix: number;
  boleto: number;
}

export interface FinopsReconciliation {
  recordId: string;
  sourceSystem: string;
  salesLedgerSnapshotId: string;
  costLedgerSnapshotId: string;
  periodStart: string;
  periodEndExclusive: string;
  cutoffAt: string;
  reconciledAt: string;
  reconciledBy: string;
}

export interface CostCoverage {
  costLedgerSnapshotId: string;
  salesLedgerSnapshotId: string;
  tenantId: string;
  subscriptionId: string;
  periodStart: string;
  periodEndExclusive: string;
  timezone: string;
  cutoffAt: string;
  closedAt: string;
  coveredSalesCount: number;
  coveredGmvCents: number;
}

export interface CostEvidence {
  cents: number;
  evidence: CostEvidenceKind;
  evidenceId: string;
  sourceReference: string;
  scenarioFingerprint: string;
  paymentMethodAllocationCents: PaymentMethodAllocationCents;
  allocationMethod: string;
  asOf: string;
  coverage: CostCoverage;
  reconciliation: FinopsReconciliation | null;
}

export interface AppSellCostModel {
  processorCost: CostEvidence;
  processorRecovered: CostEvidence;
  taxesOnPlatformRevenue: CostEvidence;
  infrastructure: CostEvidence;
  storageAndDelivery: CostEvidence;
  supportAndOperations: CostEvidence;
  refundsAndReversals: CostEvidence;
  chargebacksAndFraud: CostEvidence;
  connectPaymentVolumeFees: CostEvidence;
  connectActiveAccountFees: CostEvidence;
  connectPayoutFees: CostEvidence;
  connectCrossBorderFxFees: CostEvidence;
  otherVariableCosts: CostEvidence;
}

export interface AppSellPlanQuote {
  planKey: AppSellPlanKey;
  tenantId: string;
  subscriptionId: string;
  salesCount: number;
  grossMerchandiseValueCents: number;
  salesByPaymentMethod: Record<PaymentMethod, number>;
  gmvByPaymentMethodCents: PaymentMethodAllocationCents;
  monthlyFeeCents: number;
  monthlyFeeOccurrences: 1;
  variableFeeCents: number;
  fixedTransactionFeesCents: number;
  platformRevenueCents: number;
  rounding: typeof APPSELL_VARIABLE_FEE_ROUNDING;
}

export interface AppSellStrictMarginBudget extends AppSellPlanQuote {
  target: "strictly_above_50_percent";
  maxContributionCostsCents: number;
  minimumContributionCents: number;
}

export interface AppSellMarginEvaluation extends AppSellPlanQuote {
  scenarioFingerprint: string;
  processingShortfallCents: number;
  contributionCostsCents: number;
  contributionCents: number;
  contributionMarginFraction: {
    numeratorCents: number;
    denominatorCents: number;
  };
  status: "model_eligible" | "unverified" | "rejected";
  reasons: string[];
}

const PAYMENT_METHODS = ["card", "pix", "boleto"] as const;
const EVIDENCE_KINDS = ["measured", "contracted", "assumption", "missing"] as const;
const CONNECT_CHARGE_TYPES = ["direct", "destination", "separate_charges_transfers", "undecided"] as const;
const MERCHANTS_OF_RECORD = ["platform", "connected_account", "undecided"] as const;
const CONNECT_PRICING_CONTROLS = ["stripe", "platform", "undecided"] as const;
const REQUIRED_COST_KEYS = [
  "processorCost", "processorRecovered", "taxesOnPlatformRevenue", "infrastructure",
  "storageAndDelivery", "supportAndOperations", "refundsAndReversals",
  "chargebacksAndFraud", "connectPaymentVolumeFees", "connectActiveAccountFees",
  "connectPayoutFees", "connectCrossBorderFxFees", "otherVariableCosts"
] as const satisfies readonly (keyof AppSellCostModel)[];

export function pricingScenarioFingerprint(scenario: AppSellPricingScenario) {
  assertScenario(scenario);
  const plan = APPSELL_PRICING_PLANS[scenario.planKey];
  const canonical = JSON.stringify({
    schema: "appsell-pricing-scenario:v3",
    pricingContractVersion: APPSELL_PRICING_CONTRACT_VERSION,
    tenantId: scenario.tenantId,
    subscriptionId: scenario.subscriptionId,
    plan: {
      key: plan.key,
      monthlyFeeCents: plan.monthlyFeeCents,
      monthlyFeeLedgerRecordId: scenario.monthlyFeeLedgerRecordId,
      monthlyFeeOccurrences: scenario.monthlyFeeOccurrences,
      platformRateBps: plan.platformRateBps,
      platformFixedFeeCents: plan.platformFixedFeeCents,
      rounding: APPSELL_VARIABLE_FEE_ROUNDING
    },
    scenarioId: scenario.scenarioId,
    period: scenario.period,
    timezone: scenario.timezone,
    periodStart: scenario.periodStart,
    periodEndExclusive: scenario.periodEndExclusive,
    ledgerCutoffAt: scenario.ledgerCutoffAt,
    closingPolicyId: scenario.closingPolicyId,
    currency: scenario.currency,
    connectContract: {
      chargeType: scenario.connectContract.chargeType,
      merchantOfRecord: scenario.connectContract.merchantOfRecord,
      pricingControl: scenario.connectContract.pricingControl,
      contractReference: scenario.connectContract.contractReference
    },
    salesLedger: {
      snapshotId: scenario.salesLedger.snapshotId,
      sourceSystem: scenario.salesLedger.sourceSystem,
      tenantId: scenario.salesLedger.tenantId,
      subscriptionId: scenario.salesLedger.subscriptionId,
      periodStart: scenario.salesLedger.periodStart,
      periodEndExclusive: scenario.salesLedger.periodEndExclusive,
      timezone: scenario.salesLedger.timezone,
      cutoffAt: scenario.salesLedger.cutoffAt,
      closedAt: scenario.salesLedger.closedAt,
      extractedAt: scenario.salesLedger.extractedAt,
      approvedSalesCount: scenario.salesLedger.approvedSalesCount,
      approvedGmvCents: scenario.salesLedger.approvedGmvCents,
      monthlyFeeLedgerRecordId: scenario.salesLedger.monthlyFeeLedgerRecordId,
      monthlyFeeOccurrences: scenario.salesLedger.monthlyFeeOccurrences,
      monthlyFeeChargedCents: scenario.salesLedger.monthlyFeeChargedCents
    },
    sales: [...scenario.sales]
      .map(({ transactionId, ledgerRecordId, tenantId, subscriptionId, amountCents, paymentMethod, approvedAt }) => ({
        transactionId,
        ledgerRecordId,
        tenantId,
        subscriptionId,
        amountCents,
        paymentMethod,
        approvedAt
      }))
      .sort((left, right) => left.transactionId < right.transactionId
        ? -1 : left.transactionId > right.transactionId ? 1 : 0)
  });
  return `sha256:${createHash("sha256").update(canonical).digest("hex")}`;
}

export function quoteAppSellPlan(scenario: AppSellPricingScenario): AppSellPlanQuote {
  assertScenario(scenario);
  const plan = APPSELL_PRICING_PLANS[scenario.planKey];
  const grossMerchandiseValue = scenario.sales.reduce(
    (total, sale) => total + BigInt(sale.amountCents), 0n
  );
  const variableFee = scenario.sales.reduce(
    (total, sale) => total + BigInt(sale.amountCents) * BigInt(plan.platformRateBps) / 10_000n,
    0n
  );
  const fixedTransactionFees = BigInt(scenario.sales.length) * BigInt(plan.platformFixedFeeCents);
  const monthlyFee = BigInt(scenario.monthlyFeeChargedCents);
  const salesByPaymentMethod = { card: 0, pix: 0, boleto: 0 };
  const gmvByPaymentMethod = { card: 0n, pix: 0n, boleto: 0n };
  for (const sale of scenario.sales) {
    salesByPaymentMethod[sale.paymentMethod] += 1;
    gmvByPaymentMethod[sale.paymentMethod] += BigInt(sale.amountCents);
  }

  return {
    planKey: scenario.planKey,
    tenantId: scenario.tenantId,
    subscriptionId: scenario.subscriptionId,
    salesCount: scenario.sales.length,
    grossMerchandiseValueCents: toSafeCents("grossMerchandiseValueCents", grossMerchandiseValue),
    salesByPaymentMethod,
    gmvByPaymentMethodCents: {
      card: toSafeCents("gmvByPaymentMethodCents.card", gmvByPaymentMethod.card),
      pix: toSafeCents("gmvByPaymentMethodCents.pix", gmvByPaymentMethod.pix),
      boleto: toSafeCents("gmvByPaymentMethodCents.boleto", gmvByPaymentMethod.boleto)
    },
    monthlyFeeCents: scenario.monthlyFeeChargedCents,
    monthlyFeeOccurrences: scenario.monthlyFeeOccurrences,
    variableFeeCents: toSafeCents("variableFeeCents", variableFee),
    fixedTransactionFeesCents: toSafeCents("fixedTransactionFeesCents", fixedTransactionFees),
    platformRevenueCents: toSafeCents(
      "platformRevenueCents", monthlyFee + variableFee + fixedTransactionFees
    ),
    rounding: APPSELL_VARIABLE_FEE_ROUNDING
  };
}

export function quoteAppSellStrictMarginBudget(
  scenario: AppSellPricingScenario
): AppSellStrictMarginBudget {
  const quote = quoteAppSellPlan(scenario);
  const platformRevenue = BigInt(quote.platformRevenueCents);
  const maxContributionCosts = platformRevenue > 0n ? (platformRevenue - 1n) / 2n : 0n;
  const minimumContribution = platformRevenue - maxContributionCosts;
  return {
    ...quote,
    target: "strictly_above_50_percent",
    maxContributionCostsCents: toSafeCents("maxContributionCostsCents", maxContributionCosts),
    minimumContributionCents: toSafeCents("minimumContributionCents", minimumContribution)
  };
}

export function evaluateAppSellContributionMargin(
  scenario: AppSellPricingScenario,
  costs: AppSellCostModel
): AppSellMarginEvaluation {
  const quote = quoteAppSellPlan(scenario);
  const scenarioFingerprint = pricingScenarioFingerprint(scenario);
  for (const key of REQUIRED_COST_KEYS) assertCost(key, costs[key], scenario, scenarioFingerprint);

  const processorCost = BigInt(costs.processorCost.cents);
  const processorRecovered = BigInt(costs.processorRecovered.cents);
  const processingShortfall = processorCost > processorRecovered
    ? processorCost - processorRecovered : 0n;
  const contributionCosts = processingShortfall + REQUIRED_COST_KEYS
    .filter((key) => key !== "processorCost" && key !== "processorRecovered")
    .reduce((total, key) => total + BigInt(costs[key].cents), 0n);
  const platformRevenue = BigInt(quote.platformRevenueCents);
  const contribution = platformRevenue - contributionCosts;
  const unverified = REQUIRED_COST_KEYS.filter(
    (key) => !["measured", "contracted"].includes(costs[key].evidence)
  );
  const unreconciled = REQUIRED_COST_KEYS.filter((key) => costs[key].reconciliation === null);
  const connectUndecided = Object.values(scenario.connectContract).some((value) => value === "undecided");
  const reasons: string[] = [];
  if (platformRevenue <= 0n) reasons.push("platform_revenue_not_positive");
  if (contribution <= 0n) reasons.push("contribution_not_positive");
  if (2n * contribution <= platformRevenue) reasons.push("margin_not_above_50_percent");
  if (connectUndecided) reasons.push("connect_contract_undecided");
  if (unverified.length > 0) reasons.push(`unverified_costs:${unverified.join(",")}`);
  if (unreconciled.length > 0) reasons.push(`unreconciled_costs:${unreconciled.join(",")}`);
  const rejected = platformRevenue <= 0n || contribution <= 0n || 2n * contribution <= platformRevenue;

  return {
    ...quote,
    scenarioFingerprint,
    processingShortfallCents: toSafeCents("processingShortfallCents", processingShortfall),
    contributionCostsCents: toSafeCents("contributionCostsCents", contributionCosts),
    contributionCents: toSafeSignedCents("contributionCents", contribution),
    contributionMarginFraction: {
      numeratorCents: toSafeSignedCents("contributionMarginFraction.numeratorCents", contribution),
      denominatorCents: quote.platformRevenueCents
    },
    status: rejected ? "rejected" : connectUndecided || unverified.length > 0 || unreconciled.length > 0
      ? "unverified" : "model_eligible",
    reasons
  };
}

function assertScenario(scenario: AppSellPricingScenario) {
  assertIdentifier("scenarioId", scenario.scenarioId);
  assertIdentifier("tenantId", scenario.tenantId);
  assertIdentifier("subscriptionId", scenario.subscriptionId);
  assertPlanKey(scenario.planKey);
  assertIdentifier("monthlyFeeLedgerRecordId", scenario.monthlyFeeLedgerRecordId);
  assertNonNegativeSafeInteger("monthlyFeeChargedCents", scenario.monthlyFeeChargedCents);
  if (scenario.monthlyFeeOccurrences !== 1) throw new Error("monthlyFeeOccurrences must equal one.");
  if (scenario.monthlyFeeChargedCents !== APPSELL_PRICING_PLANS[scenario.planKey].monthlyFeeCents) {
    throw new Error("monthlyFeeChargedCents must match the selected tenant contract.");
  }
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(scenario.period)) throw new Error("period must use YYYY-MM.");
  assertIanaTimezone(scenario.timezone);
  assertIsoDateTime("periodStart", scenario.periodStart);
  assertIsoDateTime("periodEndExclusive", scenario.periodEndExclusive);
  assertIsoDateTime("ledgerCutoffAt", scenario.ledgerCutoffAt);
  assertCalendarMonthWindow(scenario);
  if (Date.parse(scenario.ledgerCutoffAt) < Date.parse(scenario.periodEndExclusive)) {
    throw new Error("ledgerCutoffAt must cover the complete pricing period.");
  }
  assertIdentifier("closingPolicyId", scenario.closingPolicyId);
  if (scenario.currency !== "BRL") throw new Error("currency must be BRL.");
  assertConnectContract(scenario.connectContract);
  if (!Array.isArray(scenario.sales) || scenario.sales.length === 0) {
    throw new Error("sales must contain at least one approved sale.");
  }
  const transactionIds = new Set<string>();
  const ledgerRecordIds = new Set<string>();
  const start = Date.parse(scenario.periodStart);
  const end = Date.parse(scenario.periodEndExclusive);
  let approvedGmv = 0n;
  for (const [index, sale] of scenario.sales.entries()) {
    assertIdentifier(`sales[${index}].transactionId`, sale.transactionId);
    if (transactionIds.has(sale.transactionId)) throw new Error("transactionId must be unique.");
    transactionIds.add(sale.transactionId);
    assertIdentifier(`sales[${index}].ledgerRecordId`, sale.ledgerRecordId);
    if (ledgerRecordIds.has(sale.ledgerRecordId)) throw new Error("ledgerRecordId must be unique.");
    ledgerRecordIds.add(sale.ledgerRecordId);
    if (sale.tenantId !== scenario.tenantId || sale.subscriptionId !== scenario.subscriptionId) {
      throw new Error(`sales[${index}] does not belong to the selected tenant subscription.`);
    }
    assertPositiveSafeInteger(`sales[${index}].amountCents`, sale.amountCents);
    if (!PAYMENT_METHODS.includes(sale.paymentMethod)) {
      throw new Error(`sales[${index}].paymentMethod is invalid.`);
    }
    assertIsoDateTime(`sales[${index}].approvedAt`, sale.approvedAt);
    const approvedAt = Date.parse(sale.approvedAt);
    if (approvedAt < start || approvedAt >= end) {
      throw new Error(`sales[${index}].approvedAt must belong to the pricing window.`);
    }
    approvedGmv += BigInt(sale.amountCents);
  }
  if (ledgerRecordIds.has(scenario.monthlyFeeLedgerRecordId)) {
    throw new Error("monthlyFeeLedgerRecordId must be distinct from every sale ledgerRecordId.");
  }
  assertSalesCoverage(scenario, toSafeCents("approvedGmvCents", approvedGmv));
}

function assertSalesCoverage(scenario: AppSellPricingScenario, approvedGmvCents: number) {
  const coverage = scenario.salesLedger;
  assertIdentifier("salesLedger.snapshotId", coverage.snapshotId);
  assertIdentifier("salesLedger.sourceSystem", coverage.sourceSystem);
  assertCoverageContext("salesLedger", coverage, scenario);
  assertIsoDateTime("salesLedger.closedAt", coverage.closedAt);
  assertIsoDateTime("salesLedger.extractedAt", coverage.extractedAt);
  if (Date.parse(coverage.closedAt) < Date.parse(scenario.ledgerCutoffAt)) {
    throw new Error("salesLedger.closedAt must reach the ledger cutoff.");
  }
  if (Date.parse(coverage.extractedAt) < Date.parse(coverage.closedAt)) {
    throw new Error("salesLedger.extractedAt cannot predate ledger closure.");
  }
  if (coverage.approvedSalesCount !== scenario.sales.length || coverage.approvedGmvCents !== approvedGmvCents) {
    throw new Error("salesLedger coverage must match every approved sale in the scenario.");
  }
  if (coverage.monthlyFeeLedgerRecordId !== scenario.monthlyFeeLedgerRecordId ||
      coverage.monthlyFeeOccurrences !== 1 ||
      coverage.monthlyFeeChargedCents !== scenario.monthlyFeeChargedCents) {
    throw new Error("salesLedger coverage must include the single contracted monthly fee.");
  }
}

function assertCost(
  key: string,
  cost: CostEvidence,
  scenario: AppSellPricingScenario,
  expectedFingerprint: string
) {
  assertNonNegativeSafeInteger(`${key}.cents`, cost.cents);
  if (!EVIDENCE_KINDS.includes(cost.evidence)) throw new Error(`${key}.evidence is invalid.`);
  assertIdentifier(`${key}.evidenceId`, cost.evidenceId);
  assertText(`${key}.sourceReference`, cost.sourceReference, 500);
  assertText(`${key}.allocationMethod`, cost.allocationMethod, 500);
  if (cost.scenarioFingerprint !== expectedFingerprint) {
    throw new Error(`${key} evidence fingerprint does not match the pricing scenario.`);
  }
  const allocation = cost.paymentMethodAllocationCents;
  for (const method of PAYMENT_METHODS) {
    assertNonNegativeSafeInteger(`${key}.paymentMethodAllocationCents.${method}`, allocation[method]);
  }
  const allocated = BigInt(allocation.card) + BigInt(allocation.pix) + BigInt(allocation.boleto);
  if (allocated !== BigInt(cost.cents)) {
    throw new Error(`${key} payment-method allocation must equal its cost.`);
  }
  assertIsoDateTime(`${key}.asOf`, cost.asOf);
  assertCostCoverage(key, cost.coverage, scenario);
  if (Date.parse(cost.asOf) < Date.parse(cost.coverage.closedAt)) {
    throw new Error(`${key}.asOf must reach the closed cost ledger.`);
  }
  if (cost.reconciliation !== null) assertReconciliation(key, cost, scenario);
}

function assertCostCoverage(key: string, coverage: CostCoverage, scenario: AppSellPricingScenario) {
  assertIdentifier(`${key}.coverage.costLedgerSnapshotId`, coverage.costLedgerSnapshotId);
  if (coverage.salesLedgerSnapshotId !== scenario.salesLedger.snapshotId) {
    throw new Error(`${key} coverage must use the scenario sales ledger snapshot.`);
  }
  assertCoverageContext(`${key}.coverage`, coverage, scenario);
  assertIsoDateTime(`${key}.coverage.closedAt`, coverage.closedAt);
  if (Date.parse(coverage.closedAt) < Date.parse(scenario.ledgerCutoffAt)) {
    throw new Error(`${key} cost ledger must close through the scenario cutoff.`);
  }
  if (coverage.coveredSalesCount !== scenario.salesLedger.approvedSalesCount ||
      coverage.coveredGmvCents !== scenario.salesLedger.approvedGmvCents) {
    throw new Error(`${key} coverage must include every approved sale and GMV.`);
  }
}

function assertCoverageContext(
  name: string,
  coverage: Pick<CostCoverage, "tenantId" | "subscriptionId" | "periodStart" | "periodEndExclusive" | "timezone" | "cutoffAt">,
  scenario: AppSellPricingScenario
) {
  if (coverage.tenantId !== scenario.tenantId || coverage.subscriptionId !== scenario.subscriptionId) {
    throw new Error(`${name} does not belong to the selected tenant subscription.`);
  }
  if (coverage.periodStart !== scenario.periodStart ||
      coverage.periodEndExclusive !== scenario.periodEndExclusive ||
      coverage.timezone !== scenario.timezone || coverage.cutoffAt !== scenario.ledgerCutoffAt) {
    throw new Error(`${name} must cover the exact pricing window and cutoff.`);
  }
}

function assertReconciliation(key: string, cost: CostEvidence, scenario: AppSellPricingScenario) {
  const reconciliation = cost.reconciliation!;
  assertIdentifier(`${key}.reconciliation.recordId`, reconciliation.recordId);
  assertIdentifier(`${key}.reconciliation.sourceSystem`, reconciliation.sourceSystem);
  assertIdentifier(`${key}.reconciliation.reconciledBy`, reconciliation.reconciledBy);
  if (reconciliation.salesLedgerSnapshotId !== scenario.salesLedger.snapshotId ||
      reconciliation.costLedgerSnapshotId !== cost.coverage.costLedgerSnapshotId) {
    throw new Error(`${key} reconciliation must bind both closed ledger snapshots.`);
  }
  if (reconciliation.periodStart !== scenario.periodStart ||
      reconciliation.periodEndExclusive !== scenario.periodEndExclusive ||
      reconciliation.cutoffAt !== scenario.ledgerCutoffAt) {
    throw new Error(`${key} reconciliation must cover the exact pricing window and cutoff.`);
  }
  assertIsoDateTime(`${key}.reconciliation.reconciledAt`, reconciliation.reconciledAt);
  if (Date.parse(reconciliation.reconciledAt) < Date.parse(cost.asOf) ||
      Date.parse(reconciliation.reconciledAt) < Date.parse(scenario.salesLedger.closedAt)) {
    throw new Error(`${key} reconciliation cannot predate cost evidence or ledger closure.`);
  }
}

function assertConnectContract(contract: AppSellConnectContract) {
  if (!CONNECT_CHARGE_TYPES.includes(contract.chargeType)) throw new Error("connectContract.chargeType is invalid.");
  if (!MERCHANTS_OF_RECORD.includes(contract.merchantOfRecord)) throw new Error("connectContract.merchantOfRecord is invalid.");
  if (!CONNECT_PRICING_CONTROLS.includes(contract.pricingControl)) throw new Error("connectContract.pricingControl is invalid.");
  assertText("connectContract.contractReference", contract.contractReference, 500);
}

function assertPlanKey(planKey: AppSellPlanKey) {
  if (!Object.hasOwn(APPSELL_PRICING_PLANS, planKey)) throw new Error("Unknown AppSell plan.");
}

function assertCalendarMonthWindow(scenario: AppSellPricingScenario) {
  const [yearText, monthText] = scenario.period.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const nextMonth = month === 12 ? 1 : month + 1;
  const nextYear = month === 12 ? year + 1 : year;
  const start = zonedParts(scenario.periodStart, scenario.timezone);
  const end = zonedParts(scenario.periodEndExclusive, scenario.timezone);
  if (!sameParts(start, { year, month, day: 1, hour: 0, minute: 0, second: 0 }) ||
      !sameParts(end, { year: nextYear, month: nextMonth, day: 1, hour: 0, minute: 0, second: 0 })) {
    throw new Error("pricing window must be the exact local calendar month in the declared timezone.");
  }
}

function zonedParts(value: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  }).formatToParts(new Date(value));
  const get = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((part) => part.type === type)?.value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

function sameParts(left: ReturnType<typeof zonedParts>, right: ReturnType<typeof zonedParts>) {
  return left.year === right.year && left.month === right.month && left.day === right.day &&
    left.hour === right.hour && left.minute === right.minute && left.second === right.second;
}

function assertIanaTimezone(value: string) {
  if (typeof value !== "string" || (value !== "UTC" && !value.includes("/"))) {
    throw new Error("timezone must be an IANA timezone.");
  }
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format(0);
  } catch {
    throw new Error("timezone must be an IANA timezone.");
  }
}

function assertIsoDateTime(name: string, value: string) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)) {
    throw new Error(`${name} must be an ISO UTC date-time.`);
  }
  const parsed = Date.parse(value);
  const canonical = value.includes(".") ? value : value.replace("Z", ".000Z");
  if (Number.isNaN(parsed) || new Date(parsed).toISOString() !== canonical) {
    throw new Error(`${name} must be an ISO UTC date-time.`);
  }
}

function assertText(name: string, value: string, max: number) {
  if (typeof value !== "string" || !value.trim() || value.length > max) {
    throw new Error(`${name} must be non-empty and at most ${max} characters.`);
  }
}

function assertIdentifier(name: string, value: string) {
  if (typeof value !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._:@/-]{2,199}$/.test(value)) {
    throw new Error(`${name} must be a stable identifier.`);
  }
}

function assertPositiveSafeInteger(name: string, value: number) {
  assertNonNegativeSafeInteger(name, value);
  if (value === 0) throw new Error(`${name} must be greater than zero.`);
}

function assertNonNegativeSafeInteger(name: string, value: number) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${name} must be a non-negative safe integer.`);
}

function toSafeCents(name: string, value: bigint) {
  if (value < 0n || value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error(`${name} exceeds the supported monetary range.`);
  return Number(value);
}

function toSafeSignedCents(name: string, value: bigint) {
  const limit = BigInt(Number.MAX_SAFE_INTEGER);
  if (value < -limit || value > limit) throw new Error(`${name} exceeds the supported numeric range.`);
  return Number(value);
}
