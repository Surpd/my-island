export type DeliveryRecord = {
  delivery_id?: string;
  lesson_id: string;
  status: string;
  known_student_ids?: string[];
  conflict_student_ids?: string[];
  source_lesson_ids?: string[];
  display_label?: string;
  unresolved_student_ids?: string[];
};

export function draftStudentDelivery(records: DeliveryRecord[], studentId: string, electiveRecords: DeliveryRecord[] = []) {
  const electiveSourceIds = new Set(electiveRecords.flatMap(record => record.source_lesson_ids || []));
  const selected = [...records.filter(record => !electiveSourceIds.has(record.lesson_id)), ...electiveRecords].filter(record =>
    record.status !== 'unresolved' && (record.known_student_ids || []).map(String).includes(String(studentId)),
  );
  return {
    selected,
    conflictLessonIds: selected
      .filter(record => (record.conflict_student_ids || []).map(String).includes(String(studentId)))
      .map(record => record.lesson_id),
    incompleteCount: records.filter(record => record.status === 'unresolved').length
      + electiveRecords.filter(record => (record.unresolved_student_ids || []).map(String).includes(String(studentId))).length,
  };
}
