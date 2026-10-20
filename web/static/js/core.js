window.CACore = {
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
      if (this.projects.length) this.projects[0].expanded = true;
      if (!this.currentSid && this.projects.length) {
        const first = this.projects.find(p => p.sessions && p.sessions.length);
        if (first) this.selectSession(first.sessions[0].id);
      }
    },
    async logout() { await this.api('/api/logout', { method:'POST' }); location.href = '/login'; },
  },
  mounted() {
      this.checkAuth();
      document.addEventListener('click', () => this.closeProjectMenus());
    },
};