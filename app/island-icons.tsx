/* oxlint-disable next/no-img-element */
import type { CSSProperties } from 'react';

export type IslandIconName =
  | 'schedule'
  | 'groups'
  | 'information'
  | 'homework'
  | 'grades'
  | 'homeroom';

const iconAssets: Record<IslandIconName, string> = {
  schedule: '/island/landmark-schedule.png',
  homework: '/island/landmark-homework.png',
  grades: '/island/landmark-grades.png',
  information: '/island/landmark-information.png',
  groups: '/island/landmark-groups.png',
  homeroom: '/island/landmark-homeroom.png',
};

export function IslandIcon({
  name,
  size = 24,
  className,
}: {
  name: IslandIconName;
  size?: number;
  className?: string;
}) {
  return (
    <img
      className={`landmark-icon ${className || ''}`}
      src={iconAssets[name]}
      alt=""
      width={size}
      height={size}
      draggable={false}
      style={{ '--landmark-size': `${size}px` } as CSSProperties}
    />
  );
}

export function IslandMark({
  name,
  size = 34,
  className,
}: {
  name: IslandIconName;
  size?: number;
  className?: string;
}) {
  return (
    <span className={`island-mark ${className || ''}`}>
      <IslandIcon name={name} size={size} />
    </span>
  );
}
