window.CAChat = {
  methods: {
    async loadSessions() {
      const r = await this.api('/api/sessions');
      const list = await r.json();
      list.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
      this.sessions = list;
    },
    formatTime(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      const p = n => String(n).padStart(2, '0');
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    },
    shortTitle(t) {
      const s = (t || '').replace(/\s+/g, ' ').trim() || '新会话';
      return s.length > 24 ? s.slice(0, 24) + '…' : s;
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
      const r = await this.api('/api/sessions', { method:'POST' });
      const s = await r.json(); await this.loadSessions(); this.selectSession(s.id);
    },
    async forkMessage(sid, index) {
      const r = await this.api('/api/sessions/' + sid + '/fork', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ message_index: index }) });
      const s = await r.json(); await this.loadSessions(); this.selectSession(s.id);
    },
    async deleteSession(sid) {
      if (!confirm('确定删除该会话？此操作不可恢复。')) return;
      await this.api('/api/sessions/' + sid, { method:'DELETE' });
      if (this.currentSid === sid) {
        this.currentSid = null;
        this.messages = [];
        this.streamText = '';
        this.streamTools = [];
        this.usage = null;
        if (this.es) { this.es.close(); this.es = null; }
      }
      await this.loadSessions();
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