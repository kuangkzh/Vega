// 图片地址解析器：渲染期间由 renderMarkdown 注入，用于把相对路径映射到文件读取接口
let imageResolver = null;

// 历史消息的 markdown 渲染结果缓存：以消息对象为 key，内容未变时直接复用，避免每次重渲染都重新解析
const mdCache = new WeakMap();

const escapeAttr = (s) => String(s === null || s === undefined ? '' : s)
  .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// marked 4.x 的 html:false 不生效，这里覆盖 html 渲染器：markdown 内的原始 HTML 一律按纯文本输出
if (typeof window.marked !== 'undefined') {
  window.marked.use({
    renderer: {
      html: (html) => String(html).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
      image(href, title, text) {
        const src = imageResolver ? imageResolver(href) : href;
        return '<img src="' + escapeAttr(src) + '" alt="' + escapeAttr(text) + '"'
          + (title ? ' title="' + escapeAttr(title) + '"' : '') + '>';
      },
    },
  });
}

// marked 4.x 的 gfm 删除线规则允许单个 ~ 作分隔符（/^(~~?).../），会把 600~700、1.0~1.1B
// 这类区间写法误判成删除线；这里收紧为只认 ~~ ... ~~
if (typeof window.marked !== 'undefined') {
  window.marked.use({
    tokenizer: {
      del(src) {
        const m = /^~~(?=[^\s~])([\s\S]*?[^\s~])~~(?=[^~]|$)/.exec(src);
        if (m) return { type: 'del', raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) };
        // 返回 undefined（而非 false）：单 ~ 不再当分隔符，也不回落到内置实现，交给后续规则按纯文本处理
      },
    },
  });
}

// 公式交给 KaTeX：$$...$$ 块级/行内，$...$ 行内；katex 未加载时保持原样文本
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
      // 单 $ 行内公式：不跨行、内容不能为空；结束 $ 后不能紧跟数字（避免误吃金额），$$ 由上面的规则处理
      {
        name: 'mathInlineSingle',
        level: 'inline',
        start: (src) => { const i = src.indexOf('$'); return i < 0 ? undefined : i; },
        tokenizer(src) {
          const m = /^\$(?!\$)([^$\n]+?)\$(?!\d)/.exec(src);
          const text = m ? m[1].trim() : '';
          if (text) return { type: 'mathInlineSingle', raw: m[0], text };
        },
        renderer: (token) => render(token.text, false),
      },
    ],
  });
}

window.CAMarkdown = {
  methods: {
    // resolveImage：可选，把 markdown 中的相对图片地址转为可访问 URL
    renderMarkdown(text, resolveImage) {
      imageResolver = typeof resolveImage === 'function' ? resolveImage : null;
      try {
        return window.marked.parse(text || '', { gfm: true, breaks: false });
      } finally {
        imageResolver = null;
      }
    },
    // 历史消息专用：按消息对象缓存渲染结果（内容不变则跳过 marked.parse）
    mdHtml(m) {
      const hit = mdCache.get(m);
      if (hit && hit.text === m.content) return hit.html;
      const html = this.renderMarkdown(m.content);
      mdCache.set(m, { text: m.content, html });
      return html;
    },
  },
};
