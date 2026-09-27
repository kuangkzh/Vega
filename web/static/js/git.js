window.CAGit = {
  methods: {
    async loadGit() {
      const bb = await (await this.api('/api/git/branches')).json();
      this.gitCurrent = bb.current; this.gitBranches = bb.branches;
      this.gitBranchList = this.parseBranches(bb.branches || '');
      const lg = await (await this.api('/api/git/log')).json();
      this.gitLog = lg.out || ''; this.gitErr = lg.err || '';
      this.gitLogList = this.parseLog(lg.out || '');
      const s = await (await this.api('/api/git/status')).json();
      this.gitStatus = s.out || '';
      this.gitStatusList = this.parseStatus(s.out || '');
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
        const path = line.slice(3).trim();
        const info = map[code] || { label: code, cls: 'text-bg-secondary' };
        return { code, path, label: info.label, cls: info.cls };
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
