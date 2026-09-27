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
  },
  methods: {
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
      const r = await this.api('/api/browse?path=' + encodeURIComponent(path || '.'));
      const d = await r.json();
      if (d.entries) { this.browseEntries = d.entries; this.browsePath = String(d.path || '.').replace(/\\/g, '/'); }
    },
    async openFile(path) {
      const r = await this.api('/api/file?path=' + encodeURIComponent(path));
      const d = await r.json();
      if (d.content !== undefined) {
        this.currentFilePath = path;
        this.codeBrowseOpen = false;
        this.fileContent = d.content;
        this.$nextTick(() => this.initEditor());
      }
    },
    async saveFile() {
      if (this.cm) this.fileContent = this.cm.getValue();
      await this.api('/api/file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path: this.currentFilePath, content: this.fileContent}) });
    },
    fileMode(path) {
      const ext = ((path || '').split('.').pop() || '').toLowerCase();
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
      const ext = ((this.currentFilePath || '').split('.').pop() || '').toLowerCase();
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
        return;
      }
      if (this.cm) { this.cm.toTextArea(); this.cm = null; }
      this.cm = window.CodeMirror.fromTextArea(ta, {
        mode: mode,
        lineNumbers: true,
        lineWrapping: false,
        tabSize: 4,
        indentUnit: 4,
        readOnly: false,
        theme: 'default',
      });
      this.cm.on('change', () => { this.fileContent = this.cm.getValue(); this.resizeEditor(); });
      this.resizeEditor();
    },
    resizeEditor() {
      if (!this.cm) return;
      const lh = this.cm.defaultTextHeight() || 16;
      const lines = Math.max(this.cm.lineCount(), 1);
      this.cm.setSize('100%', Math.max(320, lines * lh + 16) + 'px');
    },
  },
};
