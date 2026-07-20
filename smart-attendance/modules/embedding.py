"""SFace embedding extraction + cosine similarity scoring."""
import base64
import threading

import cv2
import numpy as np

import config

_recognizer = None
_lock = threading.Lock()


def _get_recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = cv2.FaceRecognizerSF.create(str(config.SFACE_MODEL_PATH), "")
    return _recognizer


def extract_embedding(image_bgr, face_row):
    """Align/crop the detected face and return its 128-d embedding as a list."""
    with _lock:
        rec = _get_recognizer()
        aligned = rec.alignCrop(image_bgr, face_row)
        feature = rec.feature(aligned)
    return feature.flatten().astype(float).tolist()


def cosine_similarity(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def best_similarity(live_embedding, stored_embeddings):
    """Max cosine similarity between the live embedding and each enrolled one."""
    if not stored_embeddings:
        return 0.0
    return max(cosine_similarity(live_embedding, s) for s in stored_embeddings)


def decode_base64_image(data_url):
    """Decode a browser-captured base64 data URL (or bare base64) to a BGR image."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    raw = base64.b64decode(data_url)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)
