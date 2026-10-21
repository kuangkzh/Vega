window.CAGitDiff = {
  data() {
    return {
      diffLoading: true, diffErr: '', diffBinary: false,
      diffPath: '',
      commits: [], head: '', left: '', right: '',
      leftContent: '', rightContent: '',
    };
  },
  methods: {
    async loadCommits() {
      const r = await this.api('/api/git/commits?project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      this.head = d.head || '';
      this.commits = (d.out || '').split('\n').filter(Boolean).map(line => {
        const parts = line.split('\t');
        return { hash: parts[0], short: parts[1], subject: parts[2] || '' };
      });
    },
    async fetchParent(hash) {
      if (!hash) return '';
      try {
        const r = await this.api('/api/git/parent?hash=' + encodeURIComponent(hash) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
        const d = await r.json();
        return d.parent || '';
      } catch (e) {
        return '';
      }
    },
    async loadDiff() {
      this.diffErr = '';
      this.diffLoading = true;
      const project = encodeURIComponent(this.selectedProjectId || '');
      try {
        const r = await this.api('/api/git/diff?left=' + encodeURIComponent(this.left || '') +
          '&right=' + encodeURIComponent(this.right || '') +
          '&path=' + encodeURIComponent(this.diffPath) + '&project=' + project);
        const d = await r.json();
        this.diffErr = d.error || '';
        this.diffBinary = !!d.binary;
        this.leftContent = d.left || '';
        this.rightContent = d.right || '';
      } finally {
        this.diffLoading = false;
        this.$nextTick(() => this.initMergeView());
      }
    },
    onSideChange() { this.loadDiff(); },
    optionLabel(c) {
      return (c.hash === this.head ? '(HEAD) ' : '') + c.short + ' - ' + c.subject;
    },
    extOf(path) { return ((path || '').split('.').pop() || '').toLowerCase(); },
    fileMode(path) {
      const ext = this.extOf(path);
      const map = {
        py: 'python', pyw: 'python',
        js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'javascript',
        html: 'htmlmixed', htm: 'htmlmixed', vue: 'htmlmixed',
        css: 'css', scss: 'css', less: 'css',
        xml: 'xml', svg: 'xml',
        json: { name: 'javascript', json: true },
        md: 'markdown', markdown: 'markdown',
        yml: 'yaml', yaml: 'yaml',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        c: 'clike', h: 'clike', cpp: 'clike', cc: 'clike', java: 'clike', cs: 'clike', php: 'clike', go: 'clike', rs: 'clike',
        sql: 'sql',
      };
      return map[ext] === undefined ? null : map[ext];
    },
    initMergeView() {
      const el = this.$refs.diffView;
      if (!el || typeof window.CodeMirror === 'undefined' || typeof window.CodeMirror.MergeView === 'undefined') return;
      el.innerHTML = '';
      if (this.diffBinary || this.diffErr) return;
      this.mv = CodeMirror.MergeView(el, {
        value: this.rightContent,
        origLeft: this.leftContent,
        mode: this.fileMode(this.diffPath) || null,
        lineNumbers: true,
        lineWrapping: true,
        readOnly: true,
        revertButtons: false,
      });
      this.$nextTick(() => this.resizeMergeView());
      if (!this._resizeBound) {
        this._resizeBound = true;
        window.addEventListener('resize', () => this.resizeMergeView());
      }
    },
    resizeMergeView() {
      const mv = this.mv;
      if (!mv) return;
      const editors = [mv.leftOriginal(), mv.editor(), mv.rightOriginal()].filter(Boolean);
      editors.forEach(cm => { cm.getWrapperElement().style.height = 'auto'; cm.refresh(); });
      const hOf = cm => cm.getScrollInfo().height;
      let h = Math.max(0, ...editors.map(hOf));
      for (;;) {
        editors.forEach(cm => cm.setSize(null, Math.max(1, h)));
        const nh = Math.max(0, ...editors.map(hOf));
        if (nh >= h) break;
        h = nh;
      }
      mv.wrap.style.height = Math.max(1, h) + 'px';
    },
  },
};