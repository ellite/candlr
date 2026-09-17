// Pointer-based drag-to-reorder for a simple vertical <ul> list. Works with
// mouse and touch alike (Pointer Events + pointer capture), dragging via a
// "ghost" clone that follows the pointer with position:fixed while the real
// <li> stays in the document flow (dimmed) and is reordered live as the
// pointer crosses sibling midpoints - so there is no need to compensate the
// real element's own layout position for the drag offset the way a
// transform-following approach would.
//
// Pointer capture is set on listEl (the <ul>), not on the handle inside the
// dragged <li>: moving that <li> via insertBefore counts as removing an
// already-connected node and reinserting it, and a capturing element that
// gets disconnected - even for an instant - silently loses its capture,
// which was cutting the drag short after the first reorder.
export function initDragReorder(listEl, { onReorder, handleSelector = "[data-drag-handle]" } = {}) {
  let ghost = null;
  let dragEl = null;
  let pointerId = null;
  let offsetX = 0;
  let offsetY = 0;

  function onPointerMove(e) {
    if (e.pointerId !== pointerId || !ghost) return;
    ghost.style.left = `${e.clientX - offsetX}px`;
    ghost.style.top = `${e.clientY - offsetY}px`;

    const el = document.elementFromPoint(e.clientX, e.clientY);
    const targetLi = el?.closest("li");
    if (!targetLi || targetLi === dragEl || !listEl.contains(targetLi)) return;

    const rect = targetLi.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    listEl.insertBefore(dragEl, before ? targetLi : targetLi.nextSibling);
  }

  function onPointerUp(e) {
    if (e.pointerId !== pointerId) return;
    listEl.removeEventListener("pointermove", onPointerMove);
    listEl.removeEventListener("pointerup", onPointerUp);
    listEl.removeEventListener("pointercancel", onPointerUp);

    ghost?.remove();
    dragEl?.classList.remove("opacity-40");

    const finalDragEl = dragEl;
    ghost = null;
    dragEl = null;
    pointerId = null;

    if (finalDragEl && onReorder) {
      onReorder([...listEl.children].map((li) => li.dataset.eventTypeId));
    }
  }

  listEl.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(handleSelector);
    const li = handle?.closest("li");
    if (!handle || !li) return;
    e.preventDefault();

    dragEl = li;
    pointerId = e.pointerId;
    listEl.setPointerCapture(pointerId);

    const rect = li.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;

    ghost = li.cloneNode(true);
    ghost.style.position = "fixed";
    ghost.style.left = `${rect.left}px`;
    ghost.style.top = `${rect.top}px`;
    ghost.style.width = `${rect.width}px`;
    ghost.style.margin = "0";
    ghost.style.pointerEvents = "none";
    ghost.style.zIndex = "1000";
    ghost.classList.add("shadow-lg", "ring-1", "ring-zinc-900/10", "dark:ring-white/10");
    document.body.appendChild(ghost);

    dragEl.classList.add("opacity-40");

    listEl.addEventListener("pointermove", onPointerMove);
    listEl.addEventListener("pointerup", onPointerUp);
    listEl.addEventListener("pointercancel", onPointerUp);
  });
}
