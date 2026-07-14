import { notFound } from "next/navigation";

import { DemoInspectionPanel } from "./demo-inspection-panel";

export const dynamic = "force-dynamic";

export default async function DemoInspectionPage({
  searchParams
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  if (process.env.NODE_ENV === "production") notFound();
  const query = await searchParams;
  const feedback = query.status === "started" || query.status === "revoked"
    ? query.status
    : null;
  return <DemoInspectionPanel feedback={feedback}/>;
}
