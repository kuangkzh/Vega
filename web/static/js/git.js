window.CAGit = {
  methods: {
    async loadGit() {
      const p = encodeURIComponent(this.selectedProjectId || '');
      const bb = await (await this.api('/api/git/branches?project=' + p)).json();
      this.gitCurrent = bb.current;
      this.gitBranchList = this.parseBranches(bb.branches || '');
      const lg = await (await this.api('/api/git/log?project=' + p)).json();
      this.gitErr = lg.err || '';
      this.gitLogList = this.parseLog(lg.out || '');
      await this.loadGitStatus();
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
    parseLog(text) {
      const reHash = /([0-9a-fA-F]{7,40})/;
      const lines = [];
      for (const raw of text.split('\n')) {
        if (!raw) continue;
        const m = raw.match(reHash);
        if (!m) { lines.push({ graph: raw, hash: '', subject: '', decorations: [] }); continue; }
        const graph = raw.slice(0, m.index);
        const hash = m[1];
        let rest = raw.slice(m.index + m[0].length);
        let decorations = [];
        const decoMatch = rest.match(/\(([^)]*)\)\s*$/);
        if (decoMatch) {
          rest = rest.slice(0, rest.length - decoMatch[0].length);
          decorations = decoMatch[1].split(',').map(s => s.trim()).filter(Boolean);
        }
        lines.push({ graph, hash, subject: rest.trim(), decorations });
      }
      return lines;
    },
  },
};
