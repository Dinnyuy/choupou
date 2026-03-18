from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class _Track:
    label: str
    box: np.ndarray
    confidence: float
    hits: int = 1
    misses: int = 0
    counted: bool = False


def _compute_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = [int(v) for v in box_a.tolist()]
    bx1, by1, bx2, by2 = [int(v) for v in box_b.tolist()]

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0:
        return 0.0

    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    union = area_a + area_b - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


class StreamDetectionTracker:
    """Confirme les detections sur plusieurs cycles pour limiter les faux positifs webcam."""

    def __init__(
        self,
        required_hits: int = 2,
        max_misses: int = 2,
        iou_threshold: float = 0.35,
        fast_confirm_confidence: float = 0.90,
    ):
        self.required_hits = max(1, int(required_hits))
        self.max_misses = max(0, int(max_misses))
        self.iou_threshold = max(0.0, min(1.0, float(iou_threshold)))
        self.fast_confirm_confidence = max(0.0, min(1.0, float(fast_confirm_confidence)))
        self._tracks: List[_Track] = []

    def reset(self) -> None:
        self._tracks = []

    def update(self, detections: List[dict]) -> tuple[List[dict], Dict[str, int]]:
        matched_track_indexes: set[int] = set()
        new_tracks: List[_Track] = []

        ordered_detections = sorted(
            detections,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )

        for detection in ordered_detections:
            label = str(detection["waste_type"])
            box = np.array(detection["box"], dtype=np.int32)
            confidence = float(detection["confidence"])

            best_match_index = None
            best_match_iou = 0.0
            for track_index, track in enumerate(self._tracks):
                if track_index in matched_track_indexes or track.label != label:
                    continue
                iou = _compute_iou(track.box, box)
                if iou >= self.iou_threshold and iou > best_match_iou:
                    best_match_index = track_index
                    best_match_iou = iou

            if best_match_index is None:
                new_tracks.append(_Track(label=label, box=box, confidence=confidence))
                continue

            track = self._tracks[best_match_index]
            track.box = box
            track.confidence = max(track.confidence, confidence)
            track.hits += 1
            track.misses = 0
            matched_track_indexes.add(best_match_index)

        surviving_tracks: List[_Track] = []
        for track_index, track in enumerate(self._tracks):
            if track_index not in matched_track_indexes:
                track.misses += 1
            if track.misses <= self.max_misses:
                surviving_tracks.append(track)

        surviving_tracks.extend(new_tracks)
        self._tracks = surviving_tracks

        stable_detections: List[dict] = []
        new_counts: Dict[str, int] = {}
        for track in self._tracks:
            is_confirmed = (
                track.hits >= self.required_hits
                or track.confidence >= self.fast_confirm_confidence
            )
            if not is_confirmed:
                continue

            if track.misses == 0 and not track.counted:
                new_counts[track.label] = new_counts.get(track.label, 0) + 1
                track.counted = True

            if track.misses <= 1:
                stable_detections.append(
                    {
                        "waste_type": track.label,
                        "confidence": track.confidence,
                        "box": track.box.copy(),
                    }
                )

        return stable_detections, new_counts
