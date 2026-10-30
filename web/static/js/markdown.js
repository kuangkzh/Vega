// marked 4.x 的 html:false 不生效，这里覆盖 html 渲染器：markdown 内的原始 HTML 一律按纯文本输出
if (typeof window.marked !== 'undefined') {
  window.marked.use({
    renderer: {
      html: (html) => String(html).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    },
  });
}

// $$...$$ 公式交给 KaTeX；katex 未加载时保持原样文本
if (typeof window.marked !== 'undefined' && window.katex) {
  const render = (tex, displayMode) => window.katex.renderToString(tex, { displayMode, throwOnError: false });

  // 独占一行的块级公式
  window.marked.use({
    extensions: [
      {
        name: 'mathBlock',
        level: 'block',
        start: (src) => { const i = src.indexOf('$$'); return i < 0 ? undefined : i; },
        tokenizer(src) {
          const m = /^\$\$([\s\S]+?)\$\$[ \t]*(?:\n|$)/.exec(src);
          if (m) return { type: 'mathBlock', raw: m[0], text: m[1].trim() };
        },
        renderer: (token) => '<div class="math-block">' + render(token.text, true) + '</div>',
      },
      // 夹在文字中的行内公式
      {
        name: 'mathInline',
        level: 'inline',
        start: (src) => { const i = src.indexOf('$$'); return i < 0 ? undefined : i; },
        tokenizer(src) {
          const m = /^\$\$([^\n]+?)\$\$/.exec(src);
          if (m) return { type: 'mathInline', raw: m[0], text: m[1].trim() };
        },
        renderer: (token) => render(token.text, false),
      },
    ],
  });
}

window.CAMarkdown = {
  methods: {
    renderMarkdown(text) {
      return window.marked.parse(text || '', { gfm: true, breaks: false });
    },
  },
};
