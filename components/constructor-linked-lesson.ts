export function exactLinkedLesson<T extends {id: string}>(
  route: {draftId: string; active: string}, draftId: string | undefined, lessons: readonly T[],
): T | undefined {
  if (!route.active || !draftId || draftId !== route.draftId) return;
  const matches = lessons.filter(lesson => lesson.id === route.active);
  return matches.length === 1 ? matches[0] : undefined;
}
