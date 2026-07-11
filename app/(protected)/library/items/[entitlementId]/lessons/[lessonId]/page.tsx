import { LearningPlayer } from "../../../../../../components/learning-player";

export default async function LearningLessonPage({ params }: {
  params: Promise<{ entitlementId: string; lessonId: string }>;
}) {
  const { entitlementId, lessonId } = await params;
  return <LearningPlayer entitlementId={entitlementId} lessonId={lessonId}/>;
}
