"""Pure schedule workload calculations over half-open minute intervals."""
from collections import Counter


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def interval_minutes(intervals):
    return sum(end - start for start, end in merge_intervals(intervals))


def overlapping_intervals(intervals):
    changes = Counter()
    for start, end in intervals:
        changes[start] += 1
        changes[end] -= 1
    previous = None
    active = 0
    overlap = []
    for at, delta in sorted(changes.items()):
        if active > 1 and previous < at:
            overlap.append((previous, at))
        previous = at
        active += delta
    return merge_intervals(overlap)


def interval_summary(intervals, pauses=()):
    if not intervals:
        return {"first_start": None, "last_end": None, "occupied_minutes": None,
                "window_minutes": None, "gap_minutes": None, "excluded_pause_minutes": None}
    merged = merge_intervals(intervals)
    start, end = merged[0][0], merged[-1][1]
    occupied = sum(b - a for a, b in merged)
    breaks = [(max(start, a), min(end, b)) for a, b in pauses if a < end and b > start]
    occupied_or_break = interval_minutes(merged + breaks)
    return {"first_start": format_minute(start), "last_end": format_minute(end), "occupied_minutes": occupied,
            "window_minutes": end - start, "gap_minutes": end - start - occupied_or_break,
            "excluded_pause_minutes": occupied_or_break - occupied}


def format_minute(value):
    return f"{value // 60:02}:{value % 60:02}"
