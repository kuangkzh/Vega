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
    tab(v) {
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
    async fetchBrowse(path) {
      await this.loadGitStatus();
      const r = await this.api('/api/browse?path=' + encodeURIComponent(path || '.'));
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
      const r = await this.api('/api/file/diff?path=' + encodeURIComponent(path));
      const d = await r.json();
      const hasDiff = (d.lines && d.lines.some(Boolean)) || (d.deleted && d.deleted.length);
      this.fileDiff = hasDiff ? d : null;
    },
    async openFile(path) {
      const r = await this.api('/api/file?path=' + encodeURIComponent(path));
      const d = await r.json();
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
      await this.api('/api/file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path: this.currentFilePath, content: this.fileContent}) });
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
      for (let i = 0; i < diffLines.length && i < this.cm.lineCount(); i++) {
        const type = diffLines[i];
        if (type === 'added') {
          this.cm.addLineClass(i, 'background', 'cm-diff-added');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('+', 'cm-diff-added'));
        } else if (type === 'modified') {
          this.cm.addLineClass(i, 'background', 'cm-diff-modified');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('~', 'cm-diff-modified'));
        }
      }
      for (const del of (d.deleted || [])) {
        let anchor = del.anchorNewNo;
        if (anchor >= this.cm.lineCount()) anchor = this.cm.lineCount() - 1;
        if (anchor < 0) continue;
        this.cm.setGutterMarker(anchor, 'cm-diff-gutter', this._gutterMarker('-', 'cm-diff-deleted'));
      }
    },
    _gutterMarker(text, cls) {
      const el = document.createElement('span');
      el.textContent = text;
      el.className = cls;
      return el;
    },
    resizeEditor() {
      if (!this.cm) return;
      const lh = this.cm.defaultTextHeight() || 16;
      const lines = Math.max(this.cm.lineCount(), 1);
      this.cm.setSize('100%', Math.max(320, lines * lh + 16) + 'px');
    },
  },
};
