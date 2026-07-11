import { LearningPlayer } from "../../../../components/learning-player";

export default async function ResumeLearningPage({ params }: {
  params: Promise<{ entitlementId: string }>;
}) {
  const { entitlementId } = await params;
  return <LearningPlayer entitlementId={entitlementId}/>;
}
