import React, { useRef, useState, useEffect } from "react";

/**
 * Shown when an agent marks a delivery "Delivered" — captures proof
 * either as a hand-drawn signature (HTML canvas, no library needed) or
 * an uploaded/taken photo. Calls onConfirm(dataUrl) with a base64 image
 * once captured; onCancel() if the agent backs out (in which case the
 * status change itself is also cancelled — proof is required to
 * complete a delivery, not optional).
 */
export default function ProofOfDeliveryModal({ onConfirm, onCancel }) {
  const [mode, setMode] = useState("signature"); // "signature" | "photo"
  const canvasRef = useRef(null);
  const isDrawingRef = useRef(false);
  const [hasDrawn, setHasDrawn] = useState(false);
  const [photoDataUrl, setPhotoDataUrl] = useState(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#1a1204";
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
  }, [mode]);

  function getPos(e) {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function startDraw(e) {
    isDrawingRef.current = true;
    const { x, y } = getPos(e);
    const ctx = canvasRef.current.getContext("2d");
    ctx.beginPath();
    ctx.moveTo(x, y);
  }

  function draw(e) {
    if (!isDrawingRef.current) return;
    e.preventDefault();
    const { x, y } = getPos(e);
    const ctx = canvasRef.current.getContext("2d");
    ctx.lineTo(x, y);
    ctx.stroke();
    setHasDrawn(true);
  }

  function stopDraw() {
    isDrawingRef.current = false;
  }

  function clearCanvas() {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    setHasDrawn(false);
  }

  function handlePhotoSelect(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => setPhotoDataUrl(event.target.result);
    reader.readAsDataURL(file);
  }

  function handleConfirm() {
    if (mode === "signature") {
      if (!hasDrawn) return;
      onConfirm(canvasRef.current.toDataURL("image/png"));
    } else {
      if (!photoDataUrl) return;
      onConfirm(photoDataUrl);
    }
  }

  const canConfirm = mode === "signature" ? hasDrawn : !!photoDataUrl;

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "420px" }}>
        <h3 style={{ marginBottom: "12px" }}>Proof of Delivery</h3>
        <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "12px" }}>
          Capture a signature or photo to confirm this delivery.
        </p>

        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button
            className={mode === "signature" ? "btn btn-primary" : "btn"}
            onClick={() => setMode("signature")}
          >
            Signature
          </button>
          <button
            className={mode === "photo" ? "btn btn-primary" : "btn"}
            onClick={() => setMode("photo")}
          >
            Photo
          </button>
        </div>

        {mode === "signature" && (
          <div>
            <canvas
              ref={canvasRef}
              width={360}
              height={180}
              style={{ border: "1px solid var(--border-color)", borderRadius: "var(--radius-sm)", touchAction: "none", width: "100%", maxWidth: "360px" }}
              onMouseDown={startDraw}
              onMouseMove={draw}
              onMouseUp={stopDraw}
              onMouseLeave={stopDraw}
              onTouchStart={startDraw}
              onTouchMove={draw}
              onTouchEnd={stopDraw}
            />
            <button className="btn" onClick={clearCanvas} style={{ marginTop: "8px" }}>
              Clear
            </button>
          </div>
        )}

        {mode === "photo" && (
          <div>
            <input type="file" accept="image/*" capture="environment" onChange={handlePhotoSelect} className="input" />
            {photoDataUrl && (
              <img
                src={photoDataUrl}
                alt="Proof of delivery preview"
                style={{ marginTop: "10px", maxWidth: "100%", maxHeight: "180px", borderRadius: "var(--radius-sm)" }}
              />
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: "8px", marginTop: "16px" }}>
          <button className="btn btn-primary" onClick={handleConfirm} disabled={!canConfirm}>
            Confirm Delivery
          </button>
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
