// Synthetic data only. No school records or external resource identifiers.
export const source = {
  id: 99001,
  spreadsheet_id: 'synthetic-journal',
  spreadsheet_title: 'Синтетический журнал',
  grade: 7,
  subject: 'Математика',
  sheet_title: 'Математика',
  status: 'ready',
  academic_year: '2026/27',
  term_number: 1,
  period_status: 'current',
  assessment_count: 18,
  result_count: 540,
};
export const assessments = Array.from({ length: 18 }, (_, i) => ({
  id: i + 1,
  title: `Работа ${i + 1}: исследование закономерностей и решение задач с подробным объяснением`,
  assessed_on: '2026-11-10',
  weight: 1,
  max_score: i % 3 === 0 ? 20 : null,
  category: i % 3 === 0 ? 'У' : i % 3 === 1 ? 'О' : 'П',
  group_marker: 'all',
}));
export const students = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  display_name: `Синтетический-${i + 1} Длиннаяфамилия-Двойная Имя Отчество`,
  group_marker: 'all',
  match_status: i % 4 ? 'linked' : 'unmatched',
  summaries: [
    {
      label: 'Средняя',
      summary_type: 'average_grade',
      numeric_value: 4.25,
      text_value: null,
    },
    {
      label: 'Допуск',
      summary_type: 'admission',
      numeric_value: null,
      text_value: i % 2 ? 'true' : 'false',
    },
    {
      label: 'Итоговая',
      summary_type: 'term_grade',
      numeric_value: (i % 5) + 1,
      text_value: null,
    },
    {
      label: 'Итоговая за год',
      summary_type: 'final_grade',
      numeric_value: ((i + 1) % 5) + 1,
      text_value: null,
    },
    {
      label: 'Учебные',
      summary_type: 'learning_grade',
      numeric_value: i === 0 ? 0 : 4.5,
      text_value: null,
    },
    {
      label: 'Обязательные',
      summary_type: 'required_grade',
      numeric_value: 4,
      text_value: null,
    },
    {
      label: 'Расчётная',
      summary_type: 'calculated_grade',
      numeric_value: 3.67,
      text_value: null,
    },
    {
      label: 'Округлённая',
      summary_type: 'rounded_grade',
      numeric_value: 4,
      text_value: null,
    },
    {
      label: 'Корректировка преподавателя',
      summary_type: 'teacher_adjustment',
      numeric_value: i === 0 ? 0 : null,
      text_value: null,
    },
    {
      label: 'Долг по аттестации',
      summary_type: 'assessment_debt',
      numeric_value: i === 0 ? 1 : 0,
      text_value: null,
    },
    {
      label: 'Сумма баллов',
      summary_type: 'technical',
      numeric_value: 123,
      text_value: null,
    },
  ],
  results: Object.fromEntries(
    assessments.map((a, j) => [
      a.id,
      j === 7
        ? null
        : {
            numeric_score: [6, 8, 9].includes(j)
              ? null
              : j === 5
                ? 0
                : ((i + j) % 5) + 1,
            status: j === 6 ? 'н' : j === 8 ? true : j === 9 ? false : 'graded',
          },
    ]),
  ),
}));
