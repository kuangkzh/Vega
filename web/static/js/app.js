const app = Vue.createApp({
  delimiters: ['[[', ']]'],
  mixins: [
    window.CACore,
    window.CAMarkdown,
    window.CAChat,
    window.CACode,
    window.CAGit,
    window.CATasks,
    window.CASettings,
    window.CAState,
  ],
});

function lineMetrics(el) {
  const cs = window.getComputedStyle(el);
  let lh = parseFloat(cs.lineHeight);
  if (!Number.isFinite(lh) || lh <= 0) {
    const fs = parseFloat(cs.fontSize);
    lh = (Number.isFinite(fs) && fs > 0 ? fs : 16) * 1.2;
  }
  const padTB = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const borderTB = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
  return { lh, padTB, borderTB };
}

function autosizeView(el, binding) {
  const opt = binding.value || {};
  const min = opt.min || 3;
  const max = opt.max || 20;
  const m = lineMetrics(el);
  el.style.height = 'auto';
  const text = Math.max(el.scrollHeight - m.padTB, 0);
  const h = Math.min(Math.max(text, m.lh * min), m.lh * max) + m.padTB + m.borderTB;
  el.style.height = Math.round(h) + 'px';
  el.style.overflowY = text > m.lh * max ? 'auto' : 'hidden';
}

app.directive('autosize', {
  mounted(el, binding) { autosizeView(el, binding); },
  updated(el, binding) { autosizeView(el, binding); },
});

app.mount('#app');