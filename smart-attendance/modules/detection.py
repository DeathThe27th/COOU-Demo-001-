"""YuNet face detector wrapper (lazy singleton, thread-safe)."""
import threading

import cv2

import config

_detector = None
_lock = threading.Lock()


def _get_detector():
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            str(config.YUNET_MODEL_PATH), "", (320, 320),
            score_threshold=config.DETECTION_SCORE_THRESHOLD,
        )
    return _detector


def detect_largest_face(image_bgr):
    """Detect faces in a BGR frame; return the largest detection row or None.

    The returned row is YuNet's full 15-value detection (bbox + 5 landmarks +
    score) which is exactly what SFace's alignCrop expects.
    """
    h, w = image_bgr.shape[:2]
    with _lock:  # OpenCV detector instances are not thread-safe
        det = _get_detector()
        det.setInputSize((w, h))
        _, faces = det.detect(image_bgr)
    if faces is None or len(faces) == 0:
        return None
    return max(faces, key=lambda f: float(f[2]) * float(f[3]))
