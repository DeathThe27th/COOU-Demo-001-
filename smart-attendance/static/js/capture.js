/* Camera capture + frame POST for enrollment and kiosk verification. */
(function () {
  const card = document.querySelector(".camera-card");
  if (!card) return;
  const mode = card.dataset.mode;

  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");
  const statusBox = document.getElementById("status");

  let stream = null;

  function setStatus(msg, kind) {
    statusBox.textContent = msg;
    statusBox.className = "status-box" + (kind ? " status-" + kind : "");
  }

  // Pending state lives on the button that triggered the work, so the affordance
  // the user just pressed is the thing that shows it is busy. The status box
  // carries the detail; the button carries the fact that something is happening.
  function setBusy(btn, busy) {
    btn.disabled = busy;
    if (busy) {
      btn.setAttribute("aria-busy", "true");
    } else {
      btn.removeAttribute("aria-busy");
    }
  }

  async function startCamera() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      return true;
    } catch (err) {
      setStatus(
        "Camera access denied or unavailable. Allow camera permission in your " +
        "browser and reload this page. (" + err.name + ")", "error");
      return false;
    }
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
  }

  function grabFrame() {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    return canvas.toDataURL("image/jpeg", 0.85);
  }

  // Gateway/transport failures where the request provably never reached Flask.
  // A dropped keep-alive connection (idle while the student types their matric
  // and the camera warms up) surfaces as one of these on the first POST.
  const RETRYABLE_STATUS = [408, 502, 503, 504];

  async function postJSON(url, payload) {
    let lastErr = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) return res.json();
        // 4xx/5xx from the app itself is a real error — surface it, don't retry.
        if (!RETRYABLE_STATUS.includes(res.status)) {
          throw new Error("Server error " + res.status);
        }
        lastErr = new Error("Server error " + res.status);
      } catch (err) {
        // fetch() rejects only on network-level failure; the app-error throw
        // above is re-raised immediately rather than retried.
        if (err.message && err.message.startsWith("Server error ") &&
            !RETRYABLE_STATUS.some((s) => err.message.endsWith(" " + s))) {
          throw err;
        }
        lastErr = err;
      }
    }
    throw lastErr;
  }

  /* ---------------- enrollment mode ---------------- */
  if (mode === "enroll") {
    const btn = document.getElementById("btn-capture");
    const counter = document.getElementById("shot-count");
    const minShots = parseInt(card.dataset.minShots, 10);

    startCamera().then((ok) => {
      if (ok) {
        btn.disabled = false;
        setStatus("Camera ready — capture " + minShots + "+ shots.", "info");
      }
    });

    btn.addEventListener("click", async () => {
      setBusy(btn, true);
      setStatus("Processing shot…", "info");
      try {
        const data = await postJSON(card.dataset.captureUrl, { image: grabFrame() });
        if (data.status === "ok") {
          counter.textContent = data.count + " / " + minShots + " shots";
          setStatus(data.message +
            (data.min_reached ? " Minimum reached — a few more angles won't hurt."
                              : " Change angle slightly and capture again."),
            data.min_reached ? "success" : "info");
        } else {
          setStatus(data.message, data.status === "no_face" ? "warn" : "error");
        }
      } catch (err) {
        setStatus("Network/server error: " + err.message + " — try again.", "error");
      }
      setBusy(btn, false);
    });
  }

  /* ---------------- kiosk verification mode ---------------- */
  if (mode === "verify") {
    const stepMatric = document.getElementById("step-matric");
    const stepCamera = document.getElementById("step-camera");
    const matricInput = document.getElementById("matric-input");
    const sessionSelect = document.getElementById("session-select");
    const btnCheck = document.getElementById("btn-check");
    const btnVerify = document.getElementById("btn-verify");
    const btnBack = document.getElementById("btn-back");

    function payload() {
      return {
        matric_no: matricInput.value.trim(),
        session_id: parseInt(sessionSelect.value, 10),
      };
    }

    function resetToMatric(clear) {
      stopCamera();
      stepCamera.hidden = true;
      stepMatric.hidden = false;
      if (clear) matricInput.value = "";
      matricInput.focus();
    }

    // Step 1: duplicate short-circuit BEFORE any camera access
    btnCheck.addEventListener("click", async () => {
      if (!matricInput.value.trim()) {
        setStatus("Enter your matric number.", "warn");
        return;
      }
      setBusy(btnCheck, true);
      setStatus("Checking registration…", "info");
      try {
        const data = await postJSON(card.dataset.checkUrl, payload());
        if (data.status === "ok") {
          setStatus("Starting camera…", "info");
          stepMatric.hidden = true;
          stepCamera.hidden = false;
          // The camera panel is a black rectangle until the permission prompt is
          // answered and the first frame paints. Disable Verify until there is
          // actually something to verify, so nobody taps it into a null frame.
          setBusy(btnVerify, true);
          const ok = await startCamera();
          setBusy(btnVerify, false);
          if (!ok) {
            resetToMatric(false);
          } else {
            setStatus(data.message, "info");
          }
        } else if (data.status === "already_marked") {
          setStatus(data.message + " Camera skipped.", "success");
        } else if (data.status === "flagged") {
          setStatus(data.message, "warn");
        } else {
          setStatus(data.message, "error");
        }
      } catch (err) {
        setStatus("Network/server error: " + err.message, "error");
      }
      setBusy(btnCheck, false);
    });

    matricInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") btnCheck.click();
    });

    // Step 2: capture a frame and verify 1:1
    btnVerify.addEventListener("click", async () => {
      setBusy(btnVerify, true);
      setStatus("Verifying face…", "info");
      try {
        const data = await postJSON(card.dataset.verifyUrl,
                                    { ...payload(), image: grabFrame() });
        if (data.status === "success" || data.status === "already_marked") {
          setStatus(data.message, "success");
          setTimeout(() => resetToMatric(true), 3500); // ready for next student
        } else if (data.status === "flagged") {
          setStatus(data.message, "warn");
          setTimeout(() => resetToMatric(true), 5000);
        } else if (data.status === "retry") {
          setStatus(data.message, "warn");
        } else {
          setStatus(data.message, "error");
        }
      } catch (err) {
        setStatus("Network/server error: " + err.message + " — try again.", "error");
      }
      setBusy(btnVerify, false);
    });

    btnBack.addEventListener("click", () => resetToMatric(false));
  }
})();
