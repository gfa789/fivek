"""Resolve Garmin's device-dependent sample columns into a bounded detail view."""
from __future__ import annotations

from statistics import median

from .analysis import activities, bounded, mapping, number
from .store import Store


def sample_series(payload, limit: int = 600) -> tuple[list[dict], int, dict]:
    limit = max(2, min(1000, limit))
    payload = mapping(payload)
    descriptors = payload.get("metricDescriptors")
    descriptors = descriptors if isinstance(descriptors, list) else []
    columns = {}
    for descriptor in descriptors:
        descriptor = mapping(descriptor)
        index, key = descriptor.get("metricsIndex"), descriptor.get("key")
        if type(index) is int and index >= 0 and isinstance(key, str):
            columns[key] = index
    points = payload.get("activityDetailMetrics")
    points = points if isinstance(points, list) else []
    series, initial_stamp = [], None
    for point in points:
        values = mapping(point).get("metrics")
        if not isinstance(values, list):
            continue
        def get(name):
            index = columns.get(name)
            return number(values[index]) if index is not None and index < len(values) else None
        stamp = get("directTimestamp")
        if initial_stamp is None and stamp is not None:
            anchor = get("sumElapsedDuration")
            if anchor is None:
                anchor = get("sumDuration")
            initial_stamp = stamp - (anchor or 0) * 1000
        elapsed = get("sumElapsedDuration")
        basis = "elapsed duration"
        if elapsed is None and stamp is not None and initial_stamp is not None:
            elapsed = (stamp - initial_stamp) / 1000
            basis = "timestamp offset"
        if elapsed is None:
            elapsed = get("sumDuration")
            basis = "active timer"
        if elapsed is None or elapsed < 0:
            continue
        speed = bounded(get("directSpeed"), .5, 12)
        distance = get("sumDistance")
        series.append({"minutes": round(elapsed / 60, 3),
                       "time_seconds": elapsed,
                       "time_basis": basis,
                       "heart_rate": bounded(get("directHeartRate"), 25, 250),
                       "pace_seconds_km": round(1000 / speed, 1) if speed else None,
                       "cadence": bounded(get("directDoubleCadence"), 1, 300),
                       "distance_km": round(distance / 1000, 3) if distance is not None else None})
    series.sort(key=lambda point: point["time_seconds"])
    total = len(series)
    channels = ("heart_rate", "pace_seconds_km")
    intervals = [series[i]["time_seconds"] - series[i - 1]["time_seconds"] for i in range(1, total)
                 if series[i]["time_seconds"] > series[i - 1]["time_seconds"]]
    nominal = median(intervals) if intervals else None
    threshold = max(10, nominal * 5) if nominal is not None else float("inf")
    gap_counts = {key: [0] for key in channels}
    large_gaps = 0
    for index, point in enumerate(series):
        gap = index > 0 and point["time_seconds"] - series[index - 1]["time_seconds"] > threshold
        large_gaps += int(gap)
        for key in channels:
            gap_counts[key].append(gap_counts[key][-1] + int(gap or point[key] is None))
    metadata = {"time_bases": sorted({point["time_basis"] for point in series}),
                "source_rows": len(points), "excluded_timeline_rows": len(points) - total,
                "channel_readings": {key: sum(point[key] is not None for point in series) for key in channels},
                "large_time_gaps": large_gaps,
                "typical_sample_interval_seconds": round(nominal, 2) if nominal is not None else None,
                "sampling": "Actual sample endpoints and per-bucket heart-rate/pace extrema; omitted missing readings still break the plotted line."}
    indices = set(range(total))
    if total > limit:
        indices = {0, total - 1}
        buckets = max(0, (limit - 2) // 6)
        for bucket in range(buckets):
            first = 1 + bucket * (total - 2) // buckets
            end = 1 + (bucket + 1) * (total - 2) // buckets
            indices.update((first, end - 1))
            for key in channels:
                valid = [i for i in range(first, end) if series[i][key] is not None]
                if valid:
                    indices.update((min(valid, key=lambda i: series[i][key]), max(valid, key=lambda i: series[i][key])))
        if not buckets:
            for key in channels:
                valid = [i for i in range(total) if series[i][key] is not None]
                for candidate in ([max(valid, key=lambda i: series[i][key]), min(valid, key=lambda i: series[i][key])] if valid else []):
                    if len(indices) < limit:
                        indices.add(candidate)
        for index in range(limit):
            if len(indices) >= limit:
                break
            indices.add(round(index * (total - 1) / (limit - 1)))
    output, previous = [], None
    for index in sorted(indices):
        point = {**series[index], "gap_before": []}
        if previous is not None:
            point["gap_before"] = [key for key in channels if gap_counts[key][index + 1] > gap_counts[key][previous + 1]]
        output.append(point)
        previous = index
    return output, total, metadata


def activity_detail(store: Store, activity_id: str) -> dict | None:
    item = next((a for a in activities(store) if a["id"] == activity_id), None)
    if item is None:
        return None
    payload = mapping(store.get("splits", activity_id))
    raw_laps = payload.get("lapDTOs")
    raw_laps = raw_laps if isinstance(raw_laps, list) else []
    laps = []
    for index, raw in enumerate(raw_laps):
        raw = mapping(raw)
        distance = number(raw.get("distance"))
        elapsed = number(raw.get("elapsedDuration")) or number(raw.get("duration"))
        laps.append({"index": index + 1, "distance_km": round(distance / 1000, 3) if distance is not None else None,
                     "seconds": elapsed,
                     "pace_seconds_km": round(elapsed / (distance / 1000), 1) if item["kind"] == "run" and elapsed and distance else None,
                     "average_hr": bounded(raw.get("averageHR"), 25, 250),
                     "max_hr": bounded(raw.get("maxHR"), 25, 250)})
    samples, count, metadata = sample_series(store.get("samples", activity_id))
    return {"activity": item, "laps": laps, "samples": samples, "source_sample_count": count,
            "display_sample_count": len(samples),
            "sample_quality": metadata,
            "note": "Activity elapsed time includes pauses. Sample times prefer elapsed duration, then timestamp offsets, then active timer duration; active timer fallback can exclude pauses. Garmin may already sample chart data; this view displays at most 600 actual samples and preserves channel extrema. Line breaks mark missing readings or unusually long gaps between source samples."}
