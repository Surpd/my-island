export type IslandLocationId = 'schedule' | 'homework' | 'grades' | 'information';

export type IslandLocation = { id: IslandLocationId; label: string; x: number; y: number; asset?: string; camera?: { x: number; y: number; scale: number } };

// Replace this pack without touching navigation or domain logic.
export const islandLocations: IslandLocation[] = [
  { id: 'schedule', label: 'Расписание', x: 59, y: 33, camera: { x: 58, y: 32, scale: 1.18 } },
  { id: 'homework', label: 'Домашка', x: 26, y: 69, camera: { x: 25, y: 67, scale: 1.2 } },
  { id: 'grades', label: 'Оценки', x: 72, y: 57, camera: { x: 72, y: 55, scale: 1.16 } },
  { id: 'information', label: 'Новости', x: 79, y: 80, camera: { x: 78, y: 78, scale: 1.2 } },
];

export const islandPacks = {
  grade9: { master: '/island/grade-9-master.png', dayNight: { day: '/island/grade-9-master.png', night: '/island/grade-9-master.png' }, locations: islandLocations },
} as const;
