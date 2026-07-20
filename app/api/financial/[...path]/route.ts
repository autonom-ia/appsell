import { createBffProxy } from "@/lib/bff-proxy";

const financialApiUrl = process.env.FINANCIAL_API_URL ?? "https://financial.api-autonomia.com";

export const dynamic = "force-dynamic";

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS } = createBffProxy({
  targetBaseUrl: financialApiUrl,
  pathPrefix: "/financial"
});
