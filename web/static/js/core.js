window.CACore = {
  watch: {
    currentProject(p) {
      document.title = '【Vega】' + (p ? p.name : '');
    },
    selectedProjectId() {
      this.syncProjectUrl();
    },
  },
  methods: {
    async api(url, opts) {
      const resp = await fetch(url, opts);
      if (resp.status === 401) { location.href = '/login'; throw new Error('unauthorized'); }
      return resp;
    },
    async checkAuth() {
      const r = await fetch('/api/authed'); const d = await r.json();
      if (!d.authed) { location.href = '/login'; return; }
      this.loading = false;
      await this.loadProjects();
      this.openGlobalStream();
      const pid = new URLSearchParams(location.search).get('project');
      const target = pid ? this.projects.find(p => p.id === pid) : null;
      if (target) {
        target.expanded = true;
        this.selectedProjectId = target.id;
      } else if (this.projects.length) {
        this.projects[0].expanded = true;
      }
      if (!this.currentSid && this.projects.length) {
        const first = target
          ? (target.sessions && target.sessions.length ? target : null)
          : this.projects.find(p => p.sessions && p.sessions.length);
        if (first) this.selectSession(first.sessions[0].id);
      }
    },
    syncProjectUrl() {
      const url = new URL(location.href);
      if (this.selectedProjectId) url.searchParams.set('project', this.selectedProjectId);
      else url.searchParams.delete('project');
      history.replaceState(null, '', url);
    },
    async logout() { await this.api('/api/logout', { method:'POST' }); location.href = '/login'; },
  },
  mounted() {
      this.checkAuth();
      document.addEventListener('click', () => this.closeProjectMenus());
    },
};