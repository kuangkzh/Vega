window.CAGit = {
  methods: {
    async loadGit() {
      const p = encodeURIComponent(this.selectedProjectId || '');
      const bb = await (await this.api('/api/git/branches?project=' + p)).json();
      this.gitCurrent = bb.current;
      this.gitBranchList = this.parseBranches(bb.branches || '');
      const lg = await (await this.api('/api/git/log?project=' + p)).json();
      this.gitErr = lg.err || '';
      this.gitCommits = lg.commits || [];
      this.gitLogList = this.buildLogList(this.gitCommits);
      this.renderGitGraph();
      await this.loadGitStatus();
    },
    renderGitGraph() {
      const el = this.$refs.gitGraph;
      if (!el) return;
      el.innerHTML = '';
      if (!this.gitCommits || !this.gitCommits.length) return;
      const GG = window.GitgraphJS;
      if (!GG || !GG.createGitgraph) { el.textContent = 'gitgraph 库加载失败'; return; }
      // 默认 orientation（不设置）即垂直、最新提交在上；reverse 反而是最旧在上，故不传。
      const graph = GG.createGitgraph(el, {
        template: GG.templateExtend(GG.TemplateName.Metro, {
          colors: ['#6ca3d6', '#71a677', '#c9a05c', '#dd3232', '#7a8a99', '#b08ac9', '#d98a5c'],
          branch: { lineWidth: 2, spacing: 16, label: { display: false } },
          commit: {
            spacing: 30,
            dot: { size: 5 },
            message: { displayHash: true, displayAuthor: false, font: 'normal 12px sans-serif' },
          },
        }),
      });
      graph.import(this.gitCommits);
    },
    onGraphClick(e) {
      const hash = this.resolveCommitHash(e.target);
      if (!hash) return;
      this.toggleCommit({ hash });
    },
    resolveCommitHash(target) {
      let el = target;
      while (el && el.nodeType === 1) {
        if (el.getAttribute) {
          const href = el.getAttribute('href') || el.getAttribute('xlink:href');
          if (href && href.charAt(0) === '#') {
            const h = href.slice(1);
            if (/^[0-9a-f]{40}$/i.test(h)) return h;
          }
          const id = el.getAttribute('id');
          if (id && /^[0-9a-f]{40}$/i.test(id)) return id;
        }
        // 点中 message 文字时，向上找到同 commit 的圆点（use href="#hash"）即可定位
        if (el.querySelector) {
          const use = el.querySelector('use');
          if (use) {
            const href = use.getAttribute('href') || use.getAttribute('xlink:href');
            const h = href ? href.slice(1) : '';
            if (/^[0-9a-f]{40}$/i.test(h)) return h;
          }
        }
        el = el.parentNode;
      }
      return '';
    },
    buildLogList(commits) {
      return commits.map(c => ({
        hash: c.hash,
        short: c.hash.slice(0, 7),
        subject: c.subject,
        decorations: this.decorationsOf(c.refs),
      }));
    },
    decorationsOf(refs) {
      const names = [];
      const tags = [];
      for (const r of (refs || [])) {
        if (r.startsWith('tag: ')) tags.push(r.slice(5));
        else names.push(r);
      }
      return names.concat(tags);
    },
    async loadGitStatus() {
      const p = encodeURIComponent(this.selectedProjectId || '');
      const s = await (await this.api('/api/git/status?project=' + p)).json();
      this.gitStatusList = this.parseStatus(s.out || '');
      this.buildStatusMap();
    },
    buildStatusMap() {
      const map = {};
      for (const s of this.gitStatusList) {
        if (!map[s.path]) map[s.path] = { letters: [], cls: s.cls };
        map[s.path].letters.push(s.letter);
      }
      for (const p of Object.keys(map)) {
        map[p].letters = this.sortStatusLetters([...new Set(map[p].letters)]);
      }
      this.gitStatusMap = map;
    },
    sortStatusLetters(letters) {
      const order = 'DRCAMU';
      return letters.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    },
    _letterOf(code) {
      if (code === '??') return 'U';
      if (code.includes('A')) return 'A';
      if (code.includes('D')) return 'D';
      if (code.includes('R')) return 'R';
      if (code.includes('C')) return 'C';
      if (code.includes('M')) return 'M';
      return code;
    },
    parseBranches(text) {
      return text.split('\n').map(l => l.replace(/\s+$/, '')).filter(Boolean).map(line => {
        const current = line.startsWith('*');
        const name = line.slice(2).trim();
        return { current, remote: name.startsWith('remotes/'), name };
      });
    },
    parseStatus(text) {
      const map = {
        '??': { label: '未跟踪', cls: 'text-bg-secondary' },
        'A ': { label: '新增', cls: 'text-bg-success' }, ' A': { label: '新增', cls: 'text-bg-success' },
        'D ': { label: '删除', cls: 'text-bg-danger' }, ' D': { label: '删除', cls: 'text-bg-danger' },
        'M ': { label: '已暂存修改', cls: 'text-bg-success' }, ' M': { label: '已修改', cls: 'text-bg-warning' },
        'MM': { label: '已修改', cls: 'text-bg-warning' },
        'R ': { label: '重命名', cls: 'text-bg-info' }, ' R': { label: '重命名', cls: 'text-bg-info' },
        'C ': { label: '复制', cls: 'text-bg-info' }, ' C': { label: '复制', cls: 'text-bg-info' },
      };
      return text.split('\n').filter(Boolean).map(line => {
        const code = line.slice(0, 2);
        let path = line.slice(3).trim();
        if (path.includes(' -> ')) path = path.split(' -> ').pop();
        const info = map[code] || { label: code, cls: 'text-bg-secondary' };
        return { code, path, label: info.label, cls: info.cls, letter: this._letterOf(code) };
      });
    },
    commitStatusInfo(s) {
      const map = {
        A: { label: '新增', cls: 'git-badge-added' },
        M: { label: '修改', cls: 'git-badge-modified' },
        D: { label: '删除', cls: 'git-badge-deleted' },
        R: { label: '重命名', cls: 'git-badge-renamed' },
        C: { label: '复制', cls: 'git-badge-renamed' },
        T: { label: '类型变更', cls: 'git-badge-modified' },
      };
      return map[s] || { label: s, cls: 'git-badge-modified' };
    },
    async toggleCommit(l) {
      if (!l || !l.hash) return;
      if (this.gitCommitExpand === l.hash) {
        this.gitCommitExpand = null;
        this.gitCommitSubject = '';
        return;
      }
      this.gitCommitExpand = l.hash;
      const c = (this.gitCommits || []).find(x => x.hash === l.hash);
      this.gitCommitSubject = c ? c.subject : '';
      this.gitCommitLoading = true;
      this.gitCommitError = '';
      this.gitCommitFiles = [];
      this.gitCommitMeta = null;
      try {
        const r = await this.api('/api/git/commit?hash=' + encodeURIComponent(l.hash) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
        const d = await r.json();
        this.gitCommitMeta = d.commit || null;
        this.gitCommitFiles = d.files || [];
        if (d.err) this.gitCommitError = d.err;
      } finally {
        this.gitCommitLoading = false;
      }
    },
    openCommitDiff(f) {
      const qs = new URLSearchParams({
        project: this.selectedProjectId || '',
        hash: this.gitCommitExpand || '',
        path: f.path,
      });
      window.open('/git/diff?' + qs.toString(), '_blank');
    },
    openWorkingDiff(path) {
      const qs = new URLSearchParams({
        project: this.selectedProjectId || '',
        path: path,
        working: '1',
      });
      window.open('/git/diff?' + qs.toString(), '_blank');
    },
  },
};
