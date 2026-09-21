window.CAChat = {
  methods: {
    async loadSessions() {
      const r = await this.api('/api/sessions'); this.sessions = await r.json();
    },
    async selectSession(sid) {
      this.currentSid = sid; this.streamText = ''; this.streamTools = [];
      const s = this.sessions.find(x => x.id === sid); if (s) this.usage = s.token_usage;
      await this.loadMessages(); this.openStream();
    },
    async loadMessages() {
      if (!this.currentSid) return;
      const r = await this.api('/api/sessions/' + this.currentSid + '/messages'); this.messages = await r.json();
    },
    async newSession() {
      const r = await this.api('/api/sessions', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:'session'}) });
      const s = await r.json(); await this.loadSessions(); this.selectSession(s.id);
    },
    async forkSession(sid) {
      await this.api('/api/sessions/' + sid + '/fork', { method:'POST' }); await this.loadSessions();
    },
    async deleteSession(sid) {
      await this.api('/api/sessions/' + sid, { method:'DELETE' }); await this.loadSessions();
      if (this.currentSid === sid) this.currentSid = null;
    },
    async sendMessage() {
      if (!this.inputText.trim() || !this.currentSid) return;
      const text = this.inputText.trim(); this.inputText = '';
      await this.api('/api/sessions/' + this.currentSid + '/message', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content: text}) });
      this.streamText = ''; this.streamTools = [];
      if (!this.es) this.openStream();
    },
    async resumeSession() {
      if (!this.currentSid) return;
      await this.api('/api/sessions/' + this.currentSid + '/resume', { method:'POST' });
    },
    openStream() {
      if (this.es) { this.es.close(); this.es = null; }
      if (!this.currentSid) return;
      this.es = new EventSource('/api/sessions/' + this.currentSid + '/stream');
      this.es.onmessage = (ev) => {
        const d = JSON.parse(ev.data);
        if (d.type === 'text') this.streamText += d.content;
        else if (d.type === 'tool') this.streamTools.push(d);
        else if (d.type === 'usage') this.usage = d.usage;
        else if (d.type === 'status' && d.status === 'paused') {
          this.streamText = ''; this.streamTools = [];
          this.loadMessages(); this.loadSessions();
        }
      };
    },
  },
};