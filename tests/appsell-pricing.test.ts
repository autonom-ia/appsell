import { describe, expect, it } from "vitest";

import {
  APPSELL_PRICING_PLANS,
  evaluateAppSellContributionMargin,
  pricingScenarioFingerprint,
  quoteAppSellPlan,
  quoteAppSellStrictMarginBudget,
  type AppSellConnectContract,
  type AppSellCostModel,
  type AppSellPlanKey,
  type AppSellPricingScenario,
  type CostEvidenceKind,
  type PaymentMethod
} from "../lib/appsell-pricing";

describe("AppSell commercial pricing", () => {
  it("pins the tenant contract, closing window and ordered ledger records into the fingerprint", () => {
    const input = scenario([
      { amount: 100, method: "card" },
      { amount: 200, method: "pix" },
      { amount: 300, method: "boleto" }
    ]);
    input.sales = [
      { ...input.sales[0]!, transactionId: "sale-Z", ledgerRecordId: "ledger-Z" },
      { ...input.sales[1]!, transactionId: "sale-a", ledgerRecordId: "ledger-a" },
      { ...input.sales[2]!, transactionId: "sale_1", ledgerRecordId: "ledger-1" }
    ];
    expect(pricingScenarioFingerprint(input)).toBe(
      "sha256:f6376f67aab28f794ac48536dcd2898c6ffd05725296709e318acbc12cfcfc1f"
    );
  });

  it("canonicalizes Connect and sales-ledger objects independently of insertion order", () => {
    const input = repeatedScenario(3, 10_000, "growth");
    const reordered = {
      ...input,
      connectContract: Object.fromEntries(
        Object.entries(input.connectContract).reverse()
      ) as AppSellConnectContract,
      salesLedger: Object.fromEntries(
        Object.entries(input.salesLedger).reverse()
      ) as AppSellPricingScenario["salesLedger"]
    };
    expect(Object.keys(reordered.connectContract)).not.toEqual(Object.keys(input.connectContract));
    expect(Object.keys(reordered.salesLedger)).not.toEqual(Object.keys(input.salesLedger));
    expect(pricingScenarioFingerprint(reordered)).toBe(pricingScenarioFingerprint(input));
  });

  it("charges one coherent monthly fee and floors the percentage per transaction", () => {
    const input = scenario([{ amount: 101, method: "card" }, { amount: 102, method: "pix" }]);
    expect(quoteAppSellPlan(input)).toMatchObject({
      tenantId: "tenant-seller-001",
      subscriptionId: "subscription-growth-001",
      salesCount: 2,
      grossMerchandiseValueCents: 203,
      salesByPaymentMethod: { card: 1, pix: 1, boleto: 0 },
      gmvByPaymentMethodCents: { card: 101, pix: 102, boleto: 0 },
      monthlyFeeCents: 0,
      monthlyFeeOccurrences: 1,
      variableFeeCents: 10,
      fixedTransactionFeesCents: 98,
      platformRevenueCents: 108,
      rounding: "floor_per_transaction"
    });
  });

  it("quotes the three candidate contracts per tenant without aggregating an admin cohort", () => {
    const sales = Array.from({ length: 100 }, () => ({ amount: 10_000, method: "card" as const }));
    const quotes = (["launch", "growth", "scale"] as const)
      .map((planKey) => quoteAppSellPlan(scenario(sales, planKey)))
      .sort((left, right) => left.platformRevenueCents - right.platformRevenueCents);
    expect(Object.fromEntries(quotes.map((quote) => [quote.planKey, quote.platformRevenueCents])))
      .toEqual({ growth: 48_800, launch: 59_900, scale: 61_800 });
    expect(quotes.map((quote) => quote.planKey)).toEqual(["growth", "launch", "scale"]);
    expect(quotes.every((quote) => quote.tenantId === "tenant-seller-001")).toBe(true);
  });

  it("exposes the strict per-tenant cost ceiling required to stay above 50 percent", () => {
    const cases = [
      { scenario: repeatedScenario(10, 10_000, "launch"), revenue: 5_990, ceiling: 2_994, contribution: 2_996 },
      { scenario: repeatedScenario(100, 10_000, "growth"), revenue: 48_800, ceiling: 24_399, contribution: 24_401 },
      { scenario: repeatedScenario(1_000, 10_000, "scale"), revenue: 258_900, ceiling: 129_449, contribution: 129_451 }
    ];
    for (const item of cases) {
      expect(quoteAppSellStrictMarginBudget(item.scenario)).toMatchObject({
        target: "strictly_above_50_percent",
        platformRevenueCents: item.revenue,
        maxContributionCostsCents: item.ceiling,
        minimumContributionCents: item.contribution
      });
    }
  });

  it("returns only model_eligible with decided Connect and fully closed, reconciled evidence", () => {
    const input = repeatedScenario(100, 10_000, "growth");
    const result = evaluateAppSellContributionMargin(
      input, costs(input, "measured", true, {
        processorCost: 40_000, processorRecovered: 40_000,
        taxesOnPlatformRevenue: 4_000, infrastructure: 2_000,
        storageAndDelivery: 1_000, supportAndOperations: 2_000,
        refundsAndReversals: 1_000, chargebacksAndFraud: 1_000,
        connectPaymentVolumeFees: 500, otherVariableCosts: 500
      })
    );
    expect(result).toMatchObject({
      status: "model_eligible", platformRevenueCents: 48_800,
      contributionCostsCents: 12_000, contributionCents: 36_800,
      contributionMarginFraction: { numeratorCents: 36_800, denominatorCents: 48_800 },
      reasons: []
    });
    expect(result.scenarioFingerprint).toMatch(/^sha256:[a-f0-9]{64}$/);
  });

  it("never marks an undecided Connect/MoR contract as model eligible", () => {
    const input = repeatedScenario(10, 10_000, "launch", {
      chargeType: "undecided",
      merchantOfRecord: "undecided",
      pricingControl: "undecided",
      contractReference: "pending:connect-commercial-decision"
    });
    const result = evaluateAppSellContributionMargin(input, costs(input, "contracted", true));
    expect(result.status).toBe("unverified");
    expect(result.reasons).toContain("connect_contract_undecided");
  });

  it("does not certify modeled costs without reconciliation", () => {
    const input = repeatedScenario(1, 100_000);
    const result = evaluateAppSellContributionMargin(input, costs(input, "measured", false));
    expect(result.status).toBe("unverified");
    expect(result.reasons.some((reason) => reason.startsWith("unreconciled_costs:"))).toBe(true);
  });

  it("uses an exact fraction at the strict 50 percent boundary", () => {
    const input = repeatedScenario(100, 10_000, "growth");
    const exact = evaluateAppSellContributionMargin(
      input, costs(input, "contracted", true, { taxesOnPlatformRevenue: 24_400 })
    );
    expect(exact.status).toBe("rejected");
    expect(exact.reasons).toContain("margin_not_above_50_percent");
    expect(exact.contributionMarginFraction).toEqual({ numeratorCents: 24_400, denominatorCents: 48_800 });

    const above = evaluateAppSellContributionMargin(
      input, costs(input, "contracted", true, { taxesOnPlatformRevenue: 24_399 })
    );
    expect(above.status).toBe("model_eligible");
    expect(above.contributionMarginFraction).toEqual({ numeratorCents: 24_401, denominatorCents: 48_800 });
  });

  it("binds cost evidence to the exact plan, tenant, subscription and sale methods", () => {
    const original = repeatedScenario(2, 10_000);
    const evidence = costs(original, "measured", true);
    const changedMethod = scenario(original.sales.map((sale, index) => ({
      amount: sale.amountCents,
      method: index === 0 ? "pix" as const : sale.paymentMethod
    })));
    expect(() => evaluateAppSellContributionMargin(changedMethod, evidence))
      .toThrow("fingerprint does not match");

    const foreignSale = { ...original.sales[0]!, tenantId: "tenant-foreign-002" };
    const foreignScenario = { ...original, sales: [foreignSale, original.sales[1]!] };
    expect(() => quoteAppSellPlan(foreignScenario)).toThrow("selected tenant subscription");
  });

  it("requires the exact local calendar month, cutoff and complete sales-ledger coverage", () => {
    const input = repeatedScenario(2, 10_000);
    expect(() => quoteAppSellPlan({ ...input, periodStart: "2026-07-01T02:00:00Z" }))
      .toThrow("exact local calendar month");
    expect(() => quoteAppSellPlan({ ...input, ledgerCutoffAt: "2026-08-01T02:59:59Z" }))
      .toThrow("complete pricing period");
    expect(() => quoteAppSellPlan({
      ...input,
      sales: [{ ...input.sales[0]!, approvedAt: "2026-08-01T03:00:00Z" }, input.sales[1]!]
    })).toThrow("pricing window");
    expect(() => quoteAppSellPlan({
      ...input,
      salesLedger: { ...input.salesLedger, approvedSalesCount: 1 }
    })).toThrow("every approved sale");
  });

  it("accepts UTC as a valid IANA timezone when the ledger window matches it", () => {
    const input = repeatedScenario(2, 10_000);
    const utc = {
      ...input,
      timezone: "UTC",
      periodStart: "2026-07-01T00:00:00Z",
      periodEndExclusive: "2026-08-01T00:00:00Z",
      salesLedger: {
        ...input.salesLedger,
        timezone: "UTC",
        periodStart: "2026-07-01T00:00:00Z",
        periodEndExclusive: "2026-08-01T00:00:00Z"
      }
    };
    expect(quoteAppSellPlan(utc).salesCount).toBe(2);
  });

  it("requires every cost and reconciliation to cover all sales through closing", () => {
    const input = repeatedScenario(2, 10_000);
    const incomplete = costs(input, "measured", true);
    incomplete.infrastructure.coverage.coveredSalesCount = 1;
    expect(() => evaluateAppSellContributionMargin(input, incomplete))
      .toThrow("every approved sale and GMV");

    const wrongSnapshot = costs(input, "measured", true);
    wrongSnapshot.connectPayoutFees.reconciliation!.salesLedgerSnapshotId = "sales-ledger:other-close";
    expect(() => evaluateAppSellContributionMargin(input, wrongSnapshot))
      .toThrow("both closed ledger snapshots");

    const earlyReconciliation = costs(input, "measured", true);
    earlyReconciliation.processorCost.reconciliation!.reconciledAt = "2026-08-09T12:00:00Z";
    expect(() => evaluateAppSellContributionMargin(input, earlyReconciliation))
      .toThrow("cannot predate cost evidence or ledger closure");
  });

  it("rejects duplicate ledger records, incoherent monthly fees and runtime enums", () => {
    const input = repeatedScenario(1, 10_000, "growth");
    expect(() => quoteAppSellPlan({ ...input, monthlyFeeOccurrences: 2 as 1 }))
      .toThrow("must equal one");
    expect(() => quoteAppSellPlan({ ...input, monthlyFeeChargedCents: 0 }))
      .toThrow("selected tenant contract");
    expect(() => quoteAppSellPlan({ ...input, currency: "USD" as "BRL" }))
      .toThrow("currency must be BRL");
    expect(() => quoteAppSellPlan({ ...input, sales: [input.sales[0]!, input.sales[0]!] }))
      .toThrow("transactionId must be unique");
    expect(() => quoteAppSellPlan({
      ...input,
      monthlyFeeLedgerRecordId: input.sales[0]!.ledgerRecordId
    })).toThrow("must be distinct from every sale ledgerRecordId");

    const invalidEvidence = costs(input, "measured", true);
    invalidEvidence.infrastructure.evidence = "forged" as CostEvidenceKind;
    expect(() => evaluateAppSellContributionMargin(input, invalidEvidence))
      .toThrow("evidence is invalid");
  });

  it("requires each cost allocation to reconcile by payment method", () => {
    const input = repeatedScenario(1, 10_000);
    const invalid = costs(input, "measured", true, { infrastructure: 100 });
    invalid.infrastructure.paymentMethodAllocationCents.card = 99;
    expect(() => evaluateAppSellContributionMargin(input, invalid))
      .toThrow("allocation must equal its cost");
  });

  it("fails closed for aggregate monetary overflow", () => {
    expect(() => quoteAppSellPlan(scenario([
      { amount: Number.MAX_SAFE_INTEGER, method: "card" },
      { amount: Number.MAX_SAFE_INTEGER, method: "pix" }
    ], "scale"))).toThrow("supported monetary range");

    const input = repeatedScenario(1, 1);
    const hugeCosts = costs(input, "contracted", true, {
      infrastructure: Number.MAX_SAFE_INTEGER,
      supportAndOperations: Number.MAX_SAFE_INTEGER
    });
    expect(() => evaluateAppSellContributionMargin(input, hugeCosts))
      .toThrow("contributionCostsCents exceeds");
  });
});

const DECIDED_CONNECT: AppSellConnectContract = {
  chargeType: "destination",
  merchantOfRecord: "platform",
  pricingControl: "platform",
  contractReference: "stripe-contract:appsell-platform-pricing-v1"
};

function repeatedScenario(
  count: number,
  amount: number,
  planKey: AppSellPlanKey = "launch",
  connectContract: AppSellConnectContract = DECIDED_CONNECT
): AppSellPricingScenario {
  return scenario(Array.from({ length: count }, (_, index) => ({
    amount,
    method: index % 5 < 3 ? "card" : "pix"
  })), planKey, connectContract);
}

function scenario(
  sales: readonly { amount: number; method: PaymentMethod }[],
  planKey: AppSellPlanKey = "launch",
  connectContract: AppSellConnectContract = DECIDED_CONNECT
): AppSellPricingScenario {
  const tenantId = "tenant-seller-001";
  const subscriptionId = "subscription-growth-001";
  const approvedSales = sales.map((sale, index) => ({
    transactionId: `approved-sale-${String(index + 1).padStart(4, "0")}`,
    ledgerRecordId: `ledger-sale-${String(index + 1).padStart(4, "0")}`,
    tenantId,
    subscriptionId,
    amountCents: sale.amount,
    paymentMethod: sale.method,
    approvedAt: `2026-07-${String(index % 28 + 1).padStart(2, "0")}T12:00:00Z`
  }));
  return {
    scenarioId: "tenant-july-close-2026",
    tenantId,
    subscriptionId,
    planKey,
    monthlyFeeLedgerRecordId: `subscription-fee:${subscriptionId}:2026-07:${planKey}`,
    monthlyFeeChargedCents: APPSELL_PRICING_PLANS[planKey].monthlyFeeCents,
    monthlyFeeOccurrences: 1,
    period: "2026-07",
    timezone: "America/Sao_Paulo",
    periodStart: "2026-07-01T03:00:00Z",
    periodEndExclusive: "2026-08-01T03:00:00Z",
    ledgerCutoffAt: "2026-08-08T03:00:00Z",
    closingPolicyId: "finops-monthly-close-v1",
    currency: "BRL",
    connectContract: { ...connectContract },
    salesLedger: {
      snapshotId: "sales-ledger:tenant-seller-001:2026-07:close-v1",
      sourceSystem: "financial-sales-ledger",
      tenantId,
      subscriptionId,
      periodStart: "2026-07-01T03:00:00Z",
      periodEndExclusive: "2026-08-01T03:00:00Z",
      timezone: "America/Sao_Paulo",
      cutoffAt: "2026-08-08T03:00:00Z",
      closedAt: "2026-08-08T04:00:00Z",
      extractedAt: "2026-08-08T05:00:00Z",
      approvedSalesCount: approvedSales.length,
      approvedGmvCents: approvedSales.reduce((total, sale) => total + sale.amountCents, 0),
      monthlyFeeLedgerRecordId: `subscription-fee:${subscriptionId}:2026-07:${planKey}`,
      monthlyFeeOccurrences: 1,
      monthlyFeeChargedCents: APPSELL_PRICING_PLANS[planKey].monthlyFeeCents
    },
    sales: approvedSales
  };
}

function costs(
  input: AppSellPricingScenario,
  evidence: CostEvidenceKind,
  reconciled: boolean,
  overrides: Partial<Record<keyof AppSellCostModel, number>> = {}
): AppSellCostModel {
  const fingerprint = pricingScenarioFingerprint(input);
  const line = (key: keyof AppSellCostModel) => {
    const cents = overrides[key] ?? 0;
    const costLedgerSnapshotId = `cost-ledger:${input.tenantId}:${input.period}:${key}`;
    return {
      cents,
      evidence,
      evidenceId: `finops:${input.scenarioId}:${key}`,
      sourceReference: `ledger://${input.tenantId}/${input.period}/${key}`,
      scenarioFingerprint: fingerprint,
      paymentMethodAllocationCents: { card: cents, pix: 0, boleto: 0 },
      allocationMethod: "closed monthly allocation for the selected tenant subscription",
      asOf: "2026-08-10T12:00:00Z",
      coverage: {
        costLedgerSnapshotId,
        salesLedgerSnapshotId: input.salesLedger.snapshotId,
        tenantId: input.tenantId,
        subscriptionId: input.subscriptionId,
        periodStart: input.periodStart,
        periodEndExclusive: input.periodEndExclusive,
        timezone: input.timezone,
        cutoffAt: input.ledgerCutoffAt,
        closedAt: "2026-08-09T03:00:00Z",
        coveredSalesCount: input.salesLedger.approvedSalesCount,
        coveredGmvCents: input.salesLedger.approvedGmvCents
      },
      reconciliation: reconciled ? {
        recordId: `reconciliation:${input.scenarioId}:${key}`,
        sourceSystem: "finops-ledger",
        salesLedgerSnapshotId: input.salesLedger.snapshotId,
        costLedgerSnapshotId,
        periodStart: input.periodStart,
        periodEndExclusive: input.periodEndExclusive,
        cutoffAt: input.ledgerCutoffAt,
        reconciledAt: "2026-08-11T13:00:00Z",
        reconciledBy: "finops:reviewer-01"
      } : null
    };
  };
  return {
    processorCost: line("processorCost"),
    processorRecovered: line("processorRecovered"),
    taxesOnPlatformRevenue: line("taxesOnPlatformRevenue"),
    infrastructure: line("infrastructure"),
    storageAndDelivery: line("storageAndDelivery"),
    supportAndOperations: line("supportAndOperations"),
    refundsAndReversals: line("refundsAndReversals"),
    chargebacksAndFraud: line("chargebacksAndFraud"),
    connectPaymentVolumeFees: line("connectPaymentVolumeFees"),
    connectActiveAccountFees: line("connectActiveAccountFees"),
    connectPayoutFees: line("connectPayoutFees"),
    connectCrossBorderFxFees: line("connectCrossBorderFxFees"),
    otherVariableCosts: line("otherVariableCosts")
  };
}
