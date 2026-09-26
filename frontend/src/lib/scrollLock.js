// Keeps the page behind a bottom sheet from scrolling while it's open.
// Reference-counted since a sheet's close handler sometimes re-opens
// another one immediately (e.g. the event form returning to the person
// detail sheet), and plain overflow:hidden on body doesn't reliably block
// touch-scrolling on iOS Safari, hence pinning it with position:fixed.
let locks = 0;
let scrollY = 0;

export function lockScroll() {
  locks += 1;
  if (locks > 1) return;
  scrollY = window.scrollY;
  document.body.style.position = "fixed";
  document.body.style.top = `-${scrollY}px`;
  document.body.style.left = "0";
  document.body.style.right = "0";
}

export function unlockScroll() {
  locks = Math.max(0, locks - 1);
  if (locks > 0) return;
  document.body.style.position = "";
  document.body.style.top = "";
  document.body.style.left = "";
  document.body.style.right = "";
  window.scrollTo(0, scrollY);
}
