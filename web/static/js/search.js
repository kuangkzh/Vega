const KIND_LABELS = {
  user: '用户', reasoning: '思考过程', tool_call: '工具调用', tool_result: '工具结果', reply: '回复正文',
};
const KIND_BADGES = {
  user: 'text-bg-primary', reasoning: 'text-bg-secondary', tool_call: 'text-bg-warning',
  tool_result: 'text-bg-info', reply: 'text-bg-success',
};
const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

window.CASearch = {
  methods: {
    // 切到搜索页并聚焦输入框（导航点击 / Ctrl+Shift+F 共用）
    openSearch() {
      this.tab = 'search';
      this.$nextTick(() => { const el = this.$refs.searchInput; if (el) el.focus(); });
    },
    async runSearch() {
      const q = this.searchQuery.trim();
      const scopes = Object.keys(this.searchScopes).filter(k => this.searchScopes[k]);
      if (!q || !scopes.length) return;
      const params = new URLSearchParams({ q, scopes: scopes.join(','), limit: 100 });
      if (this.searchProjectId) params.set('project_id', this.searchProjectId);
      this.searchLoading = true;
      try {
        const r = await this.api('/api/search?' + params.toString());
        const d = await r.json();
        this.searchResults = d.results || [];
        this.searchTruncated = !!d.truncated;
        this.searchRan = true;
      } finally {
        this.searchLoading = false;
      }
    },
    async openSearchResult(r) {
      const p = this.projects.find(x => x.id === r.project_id);
      if (p) p.expanded = true;
      // 先切到工作区再加载消息，保证「滚到底部」发生在后面的定位之前
      this.tab = 'workspace';
      await this.selectSession(r.session_id);
      this.highlightMid = r.id;
      this.$nextTick(() => {
        const box = this.$refs.chatScroll;
        const el = box && box.querySelector('[data-mid="' + r.id + '"]');
        if (el) el.scrollIntoView({ block: 'center' });
      });
      setTimeout(() => { if (this.highlightMid === r.id) this.highlightMid = null; }, 2500);
    },
    searchKindLabel(kind) { return KIND_LABELS[kind] || kind; },
    searchKindBadge(kind) { return KIND_BADGES[kind] || 'text-bg-secondary'; },
    // 片段整体 HTML 转义后再把关键词包成 <mark>，避免消息里的标签被当成 HTML
    highlightSnippet(text) {
      const esc = s => String(s).replace(/[&<>"']/g, c => HTML_ESCAPES[c]);
      const kw = this.searchQuery.trim();
      if (!kw) return esc(text);
      const re = new RegExp(kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      let out = '', last = 0, m;
      while ((m = re.exec(text)) !== null) {
        out += esc(text.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
        last = m.index + m[0].length;
      }
      return out + esc(text.slice(last));
    },
  },
};