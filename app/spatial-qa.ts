/** Development-only fixture. No auth data or production behavior is replaced. */
export function installSpatialQa() {
  const query = new URLSearchParams(location.search);
  if (!query.has('qaFullscreen')) return () => {};
  const deviceTop = Number(query.get('qaDeviceTop') || 47);
  const chromeTop = Number(query.get('qaChromeTop') || 56);
  const root = document.documentElement;
  root.style.setProperty('--tg-safe-top', `${deviceTop}px`);
  root.style.setProperty('--tg-content-safe-top', `${chromeTop}px`);
  root.style.setProperty('--telegram-height', `${innerHeight}px`);
  const chrome = document.createElement('div');
  chrome.dataset.qaChrome = 'simulated';
  chrome.style.cssText = `position:fixed;inset:0 0 auto;height:${deviceTop + chromeTop}px;z-index:999;pointer-events:none;color:white;font:14px system-ui;`;
  const clock = document.createElement('div');
  clock.textContent = '9:41';
  clock.style.cssText = 'padding:12px 26px;font-weight:700';
  const buttons = document.createElement('div');
  buttons.textContent = 'Закрыть';
  buttons.style.cssText = `position:absolute;top:${deviceTop + 6}px;left:12px;padding:10px 14px;background:#183442cc;border-radius:22px`;
  const menu = document.createElement('div');
  menu.textContent = '⌄   ···';
  menu.style.cssText = `position:absolute;top:${deviceTop + 6}px;right:12px;padding:8px 14px;background:#183442cc;border-radius:22px;font-size:18px`;
  chrome.appendChild(clock);
  chrome.appendChild(buttons);
  chrome.appendChild(menu);
  document.body.appendChild(chrome);
  const scrub = (event: KeyboardEvent) => {
    const times: Record<string, number> = {
      '1': 0,
      '2': 175,
      '3': 310,
      '4': 350,
      '5': 390,
      '6': 490,
      '7': 699,
    };
    const animations =
      document
        .querySelector('.scene-optics')
        ?.getAnimations({ subtree: true }) || [];
    if (event.key === 'r') animations.forEach((animation) => animation.play());
    if (times[event.key] !== undefined)
      animations.forEach((animation) => {
        animation.pause();
        animation.currentTime = times[event.key];
      });
  };
  document.addEventListener('keydown', scrub);
  return () => {
    chrome.remove();
    document.removeEventListener('keydown', scrub);
  };
}
