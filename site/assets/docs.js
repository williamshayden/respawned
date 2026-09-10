// The demo loads only when requested, and pauses while hidden or offscreen.
const demo = document.querySelector('.demo');
if (demo) {
  const video = demo.querySelector('video');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let visible = false;
  const update = () => {
    if (!demo.open || reducedMotion.matches) {
      video.pause();
      if (video.hasAttribute('src')) {
        video.removeAttribute('src');
        video.load();
      }
      return;
    }
    if (visible && !document.hidden) {
      if (!video.hasAttribute('src')) video.src = video.dataset.src;
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  };
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    update();
  }).observe(video);
  demo.addEventListener('toggle', update);
  document.addEventListener('visibilitychange', update);
  reducedMotion.addEventListener('change', update);
}
