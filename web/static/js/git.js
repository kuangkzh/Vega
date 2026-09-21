window.CAGit = {
  methods: {
    async loadGit() {
      const b = await (await this.api('/api/git/branches')).json();
      this.gitCurrent = b.current; this.gitBranches = b.branches;
      const lg = await (await this.api('/api/git/log')).json();
      this.gitLog = lg.out || ''; this.gitErr = lg.err || '';
      const s = await (await this.api('/api/git/status')).json();
      this.gitStatus = s.out || '';
    },
  },
};