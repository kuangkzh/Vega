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
      await this.loadSessions();
      if (this.sessions.length) this.selectSession(this.sessions[0].id);
    },
    async logout() { await this.api('/api/logout', { method:'POST' }); location.href = '/login'; },
  },
  mounted() { this.checkAuth(); },
};