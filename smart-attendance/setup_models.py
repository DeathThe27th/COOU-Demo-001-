"""One-time download of the YuNet (detection) and SFace (recognition) ONNX models.

Run once with internet access:  python setup_models.py
After this, the system runs fully offline.
"""
import hashlib
import sys
import urllib.request

import config

MODELS = [
    {
        "name": "YuNet face detection",
        "url": ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_detection_yunet/face_detection_yunet_2023mar.onnx"),
        "path": config.YUNET_MODEL_PATH,
    },
    {
        "name": "SFace face recognition",
        "url": ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_recognition_sface/face_recognition_sface_2021dec.onnx"),
        "path": config.SFACE_MODEL_PATH,
    },
]


def download(model):
    path = model["path"]
    if path.exists() and path.stat().st_size > 0:
        print(f"[ok] {model['name']} already present: {path.name}")
        return
    print(f"[..] downloading {model['name']} ...")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(model["url"], tmp)
    tmp.rename(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    print(f"[ok] saved {path.name} ({path.stat().st_size // 1024} KB, sha256 {digest}...)")


def verify_loadable():
    import cv2
    cv2.FaceDetectorYN.create(str(config.YUNET_MODEL_PATH), "", (320, 320))
    cv2.FaceRecognizerSF.create(str(config.SFACE_MODEL_PATH), "")
    print("[ok] both models load in OpenCV — setup complete, system is now offline-ready")


if __name__ == "__main__":
    try:
        for m in MODELS:
            download(m)
        verify_loadable()
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
