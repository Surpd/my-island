import juniorDay from './art/junior.webp'
import juniorNight from './art/junior-night.webp'
import middleDay from './art/tide-student.webp'
import middleNight from './art/middle-night.webp'
import seniorDay from './art/senior.webp'
import seniorNight from './art/senior-night.webp'
import graduateDay from './art/graduate.webp'
import graduateNight from './art/graduate-night.webp'
import facultyDay from './art/tide-teacher.webp'
import facultyNight from './art/faculty-night.webp'

// Percentages refer to the building on the artwork, in its uncropped square.
export const worlds = {
  junior: {
    title: 'Малая бухта', width: 1254, day: juniorDay, night: juniorNight,
    points: { schedule: [50, 19], work: [18, 35], results: [82, 35], news: [22, 62], profile: [76, 64] },
    sites: { schedule: 'Школа с часами', work: 'Дом мастерских', results: 'Читальная беседка', news: 'Малый маяк', profile: 'Дом у причала' },
  },
  middle: {
    title: 'Северная гавань', width: 1254, day: middleDay, night: middleNight,
    points: { schedule: [53, 22], work: [23, 33], results: [80, 32], news: [16, 59], profile: [76, 64] },
    sites: { schedule: 'Главный учебный корпус', work: 'Учебная мастерская', results: 'Павильон наблюдений', news: 'Сигнальный маяк', profile: 'Приёмная у причала' },
  },
  senior: {
    title: 'Кампус у пролива', width: 1254, day: seniorDay, night: seniorNight,
    points: { schedule: [56, 16], work: [24, 36], results: [80, 44], news: [15, 65], profile: [75, 69] },
    sites: { schedule: 'Корпус с часами', work: 'Проектные мастерские', results: 'Архив исследований', news: 'Маяк пролива', profile: 'Приёмная на пристани' },
  },
  graduate: {
    title: 'Академическая гавань', width: 1254, day: graduateDay, night: graduateNight,
    points: { schedule: [23, 19], work: [76, 18], results: [69, 36], news: [15, 71], profile: [76, 65] },
    sites: { schedule: 'Академия с часовой башней', work: 'Двор проектных студий', results: 'Библиотека выпускных работ', news: 'Портовый маяк', profile: 'Приёмная коллегии' },
  },
  faculty: {
    title: 'Преподавательская гавань', width: 1254, day: facultyDay, night: facultyNight,
    points: { schedule: [32, 19], homeroom: [75, 19], work: [25, 42], results: [77, 43], news: [15, 65], profile: [79, 65] },
    sites: { schedule: 'Корпус с часами', work: 'Кафедральные студии', results: 'Медиатека курсов', news: 'Сигнальный маяк', profile: 'Приёмная преподавателей', homeroom: 'Классный дом в оранжерее' },
  },
}
