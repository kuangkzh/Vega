// marked 4.x 的 html:false 不生效，这里覆盖 html 渲染器：markdown 内的原始 HTML 一律按纯文本输出
if (typeof window.marked !== 'undefined') {
  window.marked.use({
    renderer: {
      html: (html) => String(html).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    },
  });
}

window.CACode = {
  watch: {
    workspaceView(v) {
      if (v === 'code') {
        this.$nextTick(() => this.initEditor());
      } else if (this.cm) {
        this.cm.toTextArea();
        this.cm = null;
      }
    },
  },
  computed: {
    currentBrowsePath() {
      return '文件浏览 · ' + (this.browsePath === '.' ? '根目录' : this.browsePath);
    },
    isMarkdownFile() {
      return ['md', 'markdown'].includes(this.extOf(this.currentFilePath));
    },
  },
  methods: {
    extOf(path) {
      return ((path || '').split('.').pop() || '').toLowerCase();
    },
    browse(path) {
      if (path === '..') {
        this.browsePath = this.browsePath.includes('/') ? (this.browsePath.slice(0, this.browsePath.lastIndexOf('/')) || '.') : '.';
      } else {
        this.browsePath = path;
      }
      this.fetchBrowse(this.browsePath);
    },
    browseJoin(name) { this.browse(this.browsePath === '.' ? name : this.browsePath + '/' + name); },
    browseJoinPath(name) { return this.browsePath === '.' ? name : this.browsePath + '/' + name; },
    // 面包屑：把项目相对路径切成可点击段（hasFile=true 时最后一段视为文件名，不可点）
    pathSegments(path, hasFile) {
      const parts = (path || '').split('/').filter(p => p && p !== '.');
      let acc = '';
      return parts.map((name, i) => {
        acc = acc ? acc + '/' + name : name;
        return { name, path: acc, isFile: !!hasFile && i === parts.length - 1 };
      });
    },
    // 以 base 所在目录（或项目根）为基准，解析出项目相对路径
    resolvePath(href, base) {
      let raw;
      try { raw = decodeURI(href.split('#')[0].split('?')[0]); } catch { raw = href.split('#')[0].split('?')[0]; }
      if (!raw) return null;
      if (raw.startsWith('/')) return raw.replace(/^\/+/, '');
      const parts = (base || '').includes('/') ? base.split('/').slice(0, -1) : [];
      for (const seg of raw.split('/')) {
        if (seg === '..') parts.pop();
        else if (seg && seg !== '.') parts.push(seg);
      }
      return parts.join('/');
    },
    // 路径分发：目录进浏览，文件进编辑器
    goPath(path, isDir) {
      if (!path && path !== '.') return;
      if (isDir) { this.codeBrowseOpen = true; this.browse(path); }
      else this.openFile(path);
    },
    onMdClick(e) {
      const a = e.target.closest && e.target.closest('a');
      if (!a) return;
      const href = a.getAttribute('href') || '';
      if (!href || href.startsWith('#')) return;
      if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
        if (/^https?:/i.test(href)) { e.preventDefault(); window.open(href, '_blank', 'noopener'); }
        return;
      }
      e.preventDefault();
      this.goPath(this.resolvePath(href, this.currentFilePath), href.endsWith('/'));
    },
    async fetchBrowse(path) {
      await this.loadGitStatus();
      const r = await this.api('/api/browse?path=' + encodeURIComponent(path || '.') + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      if (d.entries) {
        this.browsePath = String(d.path || '.').replace(/\\/g, '/');
        this.browseEntries = d.entries.map(e => {
          const st = this.entryStatus(e);
          return { ...e, statusLetters: st.text, statusCls: st.cls };
        });
      }
    },
    entryStatus(e) {
      const prefix = this.browsePath === '.' ? e.name : this.browsePath + '/' + e.name;
      const letters = new Set();
      for (const p of Object.keys(this.gitStatusMap)) {
        if (p === prefix || p.startsWith(prefix + '/')) {
          for (const l of this.gitStatusMap[p].letters) letters.add(l);
        }
      }
      if (!letters.size) return { text: '', cls: '' };
      const arr = this.sortStatusLetters([...letters]);
      const clsMap = { A: 'git-badge-added', U: 'git-badge-added', M: 'git-badge-modified', D: 'git-badge-deleted', R: 'git-badge-renamed', C: 'git-badge-renamed' };
      return { text: arr.join(' '), cls: clsMap[arr[0]] || '' };
    },
    async loadFileDiff(path) {
      const r = await this.api('/api/file/diff?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      const hasDiff = (d.lines && d.lines.some(Boolean)) || (d.deleted && d.deleted.length);
      this.fileDiff = hasDiff ? d : null;
    },
    async openFile(path) {
      const r = await this.api('/api/file?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      if (d.is_dir) { this.goPath(path, true); return; }
      if (d.error) { alert(d.error); return; }
      if (d.content !== undefined) {
        this.currentFilePath = path;
        this.codeBrowseOpen = false;
        this.mdPreview = false;
        this.fileContent = d.content;
        this.fileDiff = null;
        await this.loadFileDiff(path);
        this.$nextTick(() => this.initEditor());
      }
    },
    toggleMdPreview() {
      if (this.mdPreview) {
        this.mdPreview = false;
        this.$nextTick(() => this.initEditor());
      } else {
        if (this.cm) { this.fileContent = this.cm.getValue(); this.cm.toTextArea(); this.cm = null; }
        this.mdPreview = true;
      }
    },
    renderMarkdown(text) {
      return window.marked.parse(text || '', { gfm: true, breaks: false });
    },
    async saveFile() {
      if (this.cm) this.fileContent = this.cm.getValue();
      await this.api('/api/file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project_id: this.selectedProjectId, path: this.currentFilePath, content: this.fileContent}) });
      await this.loadFileDiff(this.currentFilePath);
      this.applyDiffMarks();
    },
    fileMode(path) {
      const ext = this.extOf(path);
      const map = {
        py: 'python', pyw: 'python',
        js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript',
        html: 'htmlmixed', htm: 'htmlmixed', vue: 'htmlmixed',
        css: 'css', scss: 'css', less: 'css',
        xml: 'xml', svg: 'xml',
        json: { name: 'javascript', json: true },
        md: 'markdown', markdown: 'markdown',
        yml: 'yaml', yaml: 'yaml',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        c: 'clike', h: 'clike', cpp: 'clike', cc: 'clike', java: 'clike', cs: 'clike', php: 'clike', go: 'clike', rs: 'clike',
        sql: 'sql', txt: null,
      };
      return map[ext] === undefined ? null : map[ext];
    },
    langLabel() {
      const ext = this.extOf(this.currentFilePath);
      const label = {
        py: 'Python', js: 'JavaScript', mjs: 'JavaScript', jsx: 'JSX', ts: 'TypeScript',
        html: 'HTML', htm: 'HTML', css: 'CSS', scss: 'SCSS', xml: 'XML', json: 'JSON',
        md: 'Markdown', yml: 'YAML', yaml: 'YAML', sh: 'Shell', sql: 'SQL',
        c: 'C', h: 'C', cpp: 'C++', java: 'Java', cs: 'C#', php: 'PHP', go: 'Go', rs: 'Rust',
      };
      return label[ext] || '';
    },
    initEditor() {
      const ta = this.$refs.codeEditor;
      if (!ta || typeof window.CodeMirror === 'undefined') return;
      const mode = this.fileMode(this.currentFilePath);
      if (this.cm && this.cm.getTextArea() === ta) {
        this.cm.setOption('mode', mode);
        if (this.cm.getValue() !== this.fileContent) this.cm.setValue(this.fileContent);
        this.resizeEditor();
        this.applyDiffMarks();
        return;
      }
      if (this.cm) { this.cm.toTextArea(); this.cm = null; }
      this.cm = window.CodeMirror.fromTextArea(ta, {
        mode: mode,
        lineNumbers: true,
        gutters: ['CodeMirror-linenumbers', 'cm-diff-gutter'],
        lineWrapping: false,
        tabSize: 4,
        indentUnit: 4,
        readOnly: false,
        theme: 'default',
      });
      this.cm.on('change', () => { this.fileContent = this.cm.getValue(); this.resizeEditor(); });
      this.resizeEditor();
      this.applyDiffMarks();
    },
    clearDiffMarks() {
      if (!this.cm) return;
      for (let i = 0; i < this.cm.lineCount(); i++) {
        this.cm.removeLineClass(i, 'background', 'cm-diff-added');
        this.cm.removeLineClass(i, 'background', 'cm-diff-modified');
        this.cm.setGutterMarker(i, 'cm-diff-gutter', null);
      }
    },
    applyDiffMarks() {
      this.clearDiffMarks();
      if (!this.cm || !this.fileDiff) return;
      const d = this.fileDiff;
      const diffLines = d.lines || [];
      const lineCount = this.cm.lineCount();
      const byLine = {};
      for (const h of (d.hunks || [])) {
        if (h.type === 'delete') {
          const a = h.anchor < lineCount ? h.anchor : lineCount - 1;
          if (a >= 0) byLine[a] = h;
        } else {
          for (let n = h.newStart; n < h.newEnd && n < lineCount; n++) byLine[n] = h;
        }
      }
      this._diffHunksByLine = byLine;
      for (let i = 0; i < diffLines.length && i < lineCount; i++) {
        const type = diffLines[i];
        if (type === 'added') {
          this.cm.addLineClass(i, 'background', 'cm-diff-added');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('+', 'cm-diff-added', i));
        } else if (type === 'modified') {
          this.cm.addLineClass(i, 'background', 'cm-diff-modified');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('~', 'cm-diff-modified', i));
        }
      }
      for (const del of (d.deleted || [])) {
        let anchor = del.anchorNewNo;
        if (anchor >= lineCount) anchor = lineCount - 1;
        if (anchor < 0) continue;
        this.cm.setGutterMarker(anchor, 'cm-diff-gutter', this._gutterMarker('-', 'cm-diff-deleted', anchor));
      }
    },
    _gutterMarker(text, cls, lineNo) {
      const el = document.createElement('span');
      el.textContent = text;
      el.className = cls;
      if (lineNo !== undefined) {
        el.style.cursor = 'pointer';
        el.title = '点击查看改动前对比';
        el.addEventListener('click', () => this.showDiffDetail(lineNo));
      }
      return el;
    },
    showDiffDetail(lineNo) {
      const hunk = this._diffHunksByLine ? this._diffHunksByLine[lineNo] : null;
      if (!hunk || !this.fileDiff) return;
      const headLines = this.fileDiff.headLines || [];
      const curLines = (this.cm ? this.cm.getValue() : this.fileContent).split('\n');
      const head = headLines.slice(hunk.headStart, hunk.headEnd);
      const neu = curLines.slice(hunk.newStart, hunk.newEnd);
      let top = 0, left = 0, width = 520;
      if (this.cm) {
        const c = this.cm.charCoords({ line: lineNo, ch: 0 }, 'window');
        top = c.bottom;
        left = c.left;
        width = this.cm.getWrapperElement().getBoundingClientRect().width;
      }
      this.diffDetail = {
        type: hunk.type,
        oldLines: head.map((t, i) => ({ no: hunk.headStart + i + 1, text: t })),
        newLines: neu.map((t, i) => ({ no: hunk.newStart + i + 1, text: t })),
        top, left, width, anchorBottom: top,
      };
      this.$nextTick(() => this.fitDiffPopover());
    },
    fitDiffPopover() {
      const d = this.diffDetail;
      const el = this.$refs.diffPopover;
      if (!d || !el) return;
      const rect = el.getBoundingClientRect();
      const margin = 8;
      if (d.anchorBottom + rect.height > window.innerHeight - margin) {
        // 下方放不下，翻到锚点行上方
        d.top = d.anchorBottom - rect.height - (this.cm ? this.cm.defaultTextHeight() : 16);
        if (d.top < margin) d.top = margin;
      }
      if (d.left + rect.width > window.innerWidth - margin) {
        d.left = Math.max(margin, window.innerWidth - rect.width - margin);
      }
    },
    closeDiffDetail() { this.diffDetail = null; },
    resizeEditor() {
      if (!this.cm) return;
      const lh = this.cm.defaultTextHeight() || 16;
      const lines = Math.max(this.cm.lineCount(), 1);
      this.cm.setSize('100%', Math.max(320, lines * lh + 16) + 'px');
    },
  },
};
