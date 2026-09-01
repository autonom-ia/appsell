import { NextRequest, NextResponse } from "next/server";

import { signDomainAssetUploadPart } from "@/lib/internal-api-client.server";
import { configuredPublicOrigin } from "@/lib/public-origin";
import {
  StorageUploadConfigurationError,
  browserUploadPart,
  configuredStorageUploadOrigin
} from "@/lib/storage-upload-policy.server";
import { mappedError } from "@/app/api/sellers/[sellerId]/products/[productId]/_route-shared";
import { assetActions, assetInvalidInput, assetUploadIds, prepareAssetMutation, type AssetUploadRouteContext } from "../../../../_route-shared";

/** Quantas partes um envio pode ter. Espelha `MULTIPART_MAX_PARTS` do domínio. */
const MAX_PART_NUMBER = 1000;
const SHA256 = /^[0-9a-f]{64}$/;

interface PartRouteContext extends AssetUploadRouteContext {
  params: Promise<Awaited<AssetUploadRouteContext["params"]> & { partNumber: string }>;
}

/**
 * A URL de uma parte, assinada pelo domínio com o tamanho e o hash presos nela.
 *
 * Uma por parte, e cada uma curta: é isso que deixa um envio longo sobreviver
 * ao prazo da assinatura, em vez de perder tudo quando ele vence.
 */
export async function POST(request: NextRequest, context: PartRouteContext) {
  const values = await context.params;
  const ids = await assetUploadIds({ params: Promise.resolve(values) });
  if (!ids) return NextResponse.json({ error: "asset_upload_not_found" }, { status: 404, headers: { "cache-control": "no-store" } });
  // Recusado aqui, e não repassado: um número fora da faixa não vira pedido ao
  // domínio só para voltar como erro dele.
  const partNumber = Number(values.partNumber);
  if (!Number.isInteger(partNumber) || partNumber < 1 || partNumber > MAX_PART_NUMBER) {
    return assetInvalidInput("part_number");
  }
  const prepared = await prepareAssetMutation(request, assetActions.signPart, ["_csrf", "sha256"]);
  if (!prepared.ok) return prepared.response;
  const sha256 = prepared.fields.sha256;
  if (!SHA256.test(sha256 ?? "")) return assetInvalidInput("sha256");
  try {
    const appOrigin = configuredPublicOrigin();
    const storageOrigin = configuredStorageUploadOrigin();
    const payload = await signDomainAssetUploadPart(
      prepared.authenticated.sessionId,
      prepared.authenticated.session,
      { ...ids, partNumber, sha256: sha256 as string }
    );
    const safe = browserUploadPart(payload, appOrigin, storageOrigin);
    // A prova de endereço é a mesma da intenção única: o navegador só recebe
    // uma URL que aponta para o armazenamento configurado, assinada, sem
    // credenciais e com validade curta.
    if (!safe || safe.part_number !== partNumber) {
      return NextResponse.json({ error: "invalid_asset_response" }, { status: 502, headers: { "cache-control": "no-store" } });
    }
    return NextResponse.json(safe, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof StorageUploadConfigurationError) {
      return NextResponse.json({ error: "storage_upload_configuration" }, { status: 503, headers: { "cache-control": "no-store" } });
    }
    return mappedError(error);
  }
}
