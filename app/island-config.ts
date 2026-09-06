export type IslandLocationId =
  | 'schedule'
  | 'homework'
  | 'grades'
  | 'information';

export type IslandLocation = {
  id: IslandLocationId;
  label: string;
  x: number;
  y: number;
  asset?: string;
  camera?: { x: number; y: number; scale: number };
};

// Replace this pack without touching navigation or domain logic.
export const islandLocations: IslandLocation[] = [
  {
    id: 'schedule',
    label: 'Расписание',
    x: 25,
    y: 36,
    camera: { x: 25, y: 36, scale: 1.52 },
  },
  {
    id: 'homework',
    label: 'Домашка',
    x: 31,
    y: 49,
    camera: { x: 31, y: 49, scale: 1.48 },
  },
  {
    id: 'grades',
    label: 'Оценки',
    x: 78,
    y: 39,
    camera: { x: 84, y: 39, scale: 1.5 },
  },
  {
    id: 'information',
    label: 'Информация',
    x: 75,
    y: 63,
    camera: { x: 88, y: 63, scale: 1.52 },
  },
];

export type TeacherLocationId =
  | 'schedule'
  | 'groups'
  | 'information'
  | 'homeroom';
export type TeacherLocation = {
  id: TeacherLocationId;
  label: string;
  x: number;
  y: number;
  camera: { x: number; y: number; scale: number };
};

export const teacherCampusLocations: TeacherLocation[] = [
  {
    id: 'schedule',
    label: 'Расписание',
    x: 64,
    y: 30,
    camera: { x: 59, y: 30, scale: 1.5 },
  },
  {
    id: 'groups',
    label: 'Мои группы',
    x: 25,
    y: 35,
    camera: { x: 17, y: 35, scale: 1.44 },
  },
  {
    id: 'information',
    label: 'Информация',
    x: 72,
    y: 52,
    camera: { x: 82, y: 52, scale: 1.48 },
  },
  {
    id: 'homeroom',
    label: 'Мой класс',
    x: 25,
    y: 19,
    camera: { x: 13, y: 19, scale: 1.48 },
  },
];

export const islandPacks = {
  grade9: {
    master: '/island/student-campus-grade9.jpg',
    dayNight: {
      day: '/island/student-campus-grade9.jpg',
      night: '/island/student-campus-grade9.jpg',
    },
    locations: islandLocations,
  },
} as const;
