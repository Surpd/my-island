export type DeliveryRecord = {
  lesson_id: string;
  status: string;
  known_student_ids?: string[];
  conflict_student_ids?: string[];
};

export function draftStudentDelivery(records: DeliveryRecord[], studentId: string) {
  const selected = records.filter(record =>
    record.status !== 'unresolved' && (record.known_student_ids || []).map(String).includes(String(studentId)),
  );
  return {
    selected,
    conflictLessonIds: selected
      .filter(record => (record.conflict_student_ids || []).map(String).includes(String(studentId)))
      .map(record => record.lesson_id),
    incompleteCount: records.filter(record => record.status === 'unresolved').length,
  };
}
