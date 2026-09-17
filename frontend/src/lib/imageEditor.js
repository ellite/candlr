// A small canvas-based crop/zoom editor for person photos. Takes any image
// source that can be turned into an object URL (a File from a picker, or a
// Blob already fetched by the caller - e.g. from the /images/preview
// endpoint, so a pasted URL never hits an untrusted cross-origin <img> and
// taints the canvas) and resolves with a single square JPEG Blob framed the
// way the user chose, ready to upload as-is.
export function initImageEditor(modalEl) {
  const canvas = modalEl.querySelector("[data-editor-canvas]");
  const ctx = canvas.getContext("2d");
  const zoomSlider = modalEl.querySelector("[data-editor-zoom]");
  const cancelBtn = modalEl.querySelector("[data-editor-cancel]");
  const confirmBtn = modalEl.querySelector("[data-editor-confirm]");
  const backdrop = modalEl.querySelector("[data-editor-backdrop]");

  const V = canvas.width; // square viewport, canvas.width === canvas.height
  const OUT = 640; // exported image resolution

  let img = null;
  let objectUrl = null;
  let baseScale = 1;
  let zoom = 1;
  let dx = 0;
  let dy = 0;
  let dragging = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let dxStart = 0;
  let dyStart = 0;
  let resolvePromise = null;

  function draw() {
    ctx.clearRect(0, 0, V, V);
    if (!img) return;
    const scale = baseScale * zoom;
    ctx.drawImage(img, dx, dy, img.naturalWidth * scale, img.naturalHeight * scale);
  }

  function clampOffset() {
    const scale = baseScale * zoom;
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    dx = Math.min(0, Math.max(V - dw, dx));
    dy = Math.min(0, Math.max(V - dh, dy));
  }

  function setZoom(newZoom) {
    // Keep whatever point is currently at the viewport center fixed, so
    // zooming feels like it's zooming into the crop rather than the
    // top-left corner.
    const oldScale = baseScale * zoom;
    const centerX = (V / 2 - dx) / oldScale;
    const centerY = (V / 2 - dy) / oldScale;
    zoom = newZoom;
    const newScale = baseScale * zoom;
    dx = V / 2 - centerX * newScale;
    dy = V / 2 - centerY * newScale;
    clampOffset();
    draw();
  }

  zoomSlider.addEventListener("input", () => setZoom(Number(zoomSlider.value) / 100));

  canvas.addEventListener("pointerdown", (e) => {
    if (!img) return;
    dragging = true;
    canvas.setPointerCapture(e.pointerId);
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    dxStart = dx;
    dyStart = dy;
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const bounds = canvas.getBoundingClientRect();
    dx = dxStart + (e.clientX - dragStartX) * V / bounds.width;
    dy = dyStart + (e.clientY - dragStartY) * V / bounds.height;
    clampOffset();
    draw();
  });
  function endDrag() {
    dragging = false;
  }
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  function cleanup() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    img = null;
    modalEl.classList.add("hidden");
  }

  cancelBtn.addEventListener("click", () => {
    cleanup();
    resolvePromise?.(null);
    resolvePromise = null;
  });
  backdrop.addEventListener("click", () => {
    cleanup();
    resolvePromise?.(null);
    resolvePromise = null;
  });

  confirmBtn.addEventListener("click", () => {
    const out = document.createElement("canvas");
    out.width = OUT;
    out.height = OUT;
    const octx = out.getContext("2d");
    const ratio = OUT / V;
    const scale = baseScale * zoom * ratio;
    octx.drawImage(img, dx * ratio, dy * ratio, img.naturalWidth * scale, img.naturalHeight * scale);
    out.toBlob(
      (blob) => {
        cleanup();
        resolvePromise?.(blob);
        resolvePromise = null;
      },
      "image/jpeg",
      0.92
    );
  });

  return function open(sourceBlob) {
    return new Promise((resolve) => {
      resolvePromise = resolve;
      objectUrl = URL.createObjectURL(sourceBlob);
      const image = new Image();
      image.onload = () => {
        img = image;
        baseScale = Math.max(V / img.naturalWidth, V / img.naturalHeight);
        zoom = 1;
        dx = (V - img.naturalWidth * baseScale) / 2;
        dy = (V - img.naturalHeight * baseScale) / 2;
        zoomSlider.value = "100";
        draw();
        modalEl.classList.remove("hidden");
      };
      image.onerror = () => {
        cleanup();
        resolve(null);
      };
      image.src = objectUrl;
    });
  };
}
