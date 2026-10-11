window.CAChat = {
  watch: {
    messages: { handler() { this.scrollChat(); }, deep: true },
    streamText() { this.scrollChat(); },
    streamReasoning() { this.scrollChat(); },
    pending: { handler() { this.scrollChat(); }, deep: true },
    tab(v) { if (v === 'chat') this.scrollChat(); },
  },
  computed: {
    currentSessionTitle() {
      if (!this.currentSid) return '会话列表';
      const s = this.sessions.find(x => x.id === this.currentSid);
      return s ? '会话：' + this.shortTitle(s.title) : '会话列表';
    },
  },
  methods: {
    scrollChat() {
      this.$nextTick(() => {
        const el = this.$refs.chatScroll;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    async loadSessions() {
      const r = await this.api('/api/sessions');
      const list = await r.json();
      list.sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
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
    formatArgs(s) {
      if (!s) return '';
      try { return JSON.stringify(JSON.parse(s), null, 2); } catch (e) { return s; }
    },
    formatTokens(n) {
      n = Number(n) || 0;
      if (n >= 1e6) return (n / 1e6).toFixed(1) + 'm';
      if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
      return String(n);
    },
    async selectSession(sid) {
      this.sessionsOpen = false;
      this.closeStream();
      this.currentSid = sid; this.streamText = ''; this.streamReasoning = ''; this.pending = [];
      const s = this.sessions.find(x => x.id === sid);
      if (s) { this.usage = s.token_usage; this.maxContext = s.max_context || 0; }
      await this.loadMessages();
      this.openStream();
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
        this.streamReasoning = '';
        this.pending = [];
        this.usage = null;
        this.closeStream();
      }
      await this.loadSessions();
    },
    async toggleFeishu(sid) {
      await this.api('/api/sessions/' + sid + '/feishu/toggle', { method:'POST' });
      await this.loadSessions();
    },
    onChatEnter(e) {
      if (e.shiftKey) return;   // Shift+Enter 走浏览器默认换行
      e.preventDefault();       // 其余 Enter（含 Ctrl+Enter）发送
      this.sendMessage();
    },
    autoResize() {
      const el = this.$refs.chatInput;
      if (!el) return;
      const cs = getComputedStyle(el);
      const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
      el.style.height = 'auto';
      el.style.height = (el.scrollHeight + border) + 'px';
    },
    async sendMessage() {
      if (!this.inputText.trim() || !this.currentSid) return;
      const text = this.inputText.trim(); this.inputText = '';
      this.$nextTick(this.autoResize);
      const mid = this.uid();
      await this.api('/api/sessions/' + this.currentSid + '/message', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content: text, id: mid}) });
      this.streamText = '';
      this.streamReasoning = '';
      if (!this.es) this.openStream();
    },
    uid() {
      if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
      return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    },
    async resumeSession() {
      if (!this.currentSid) return;
      await this.api('/api/sessions/' + this.currentSid + '/resume', { method:'POST' });
    },
    openStream() {
      this.closeStream();
      if (!this.currentSid) return;
      const sid = this.currentSid;
      this.es = new EventSource('/api/sessions/' + sid + '/stream');
      this.es.onmessage = (ev) => {
        if (sid !== this.currentSid) return;  // 忽略残留旧流事件，避免切换会话时串台
        const d = JSON.parse(ev.data);
        const cur = this.sessions.find(x => x.id === sid);
        if (d.type === 'text') this.streamText += d.content;
        else if (d.type === 'reasoning') this.streamReasoning += d.content;
        else if (d.type === 'queued') this.pending.push({ id: d.id, content: d.content });
        else if (d.type === 'message') {
          if (d.id) this.pending = this.pending.filter(p => p.id !== d.id);
          this.messages.push(d.message);
          if (cur) cur.message_count = this.messages.length;
          if (d.message && d.message.role === 'assistant') { this.streamText = ''; this.streamReasoning = ''; }
        }
        else if (d.type === 'usage') this.usage = d.usage;
        else if (d.type === 'restart') {
          this.loadSessions().then(() => this.selectSession(d.new_id));
        }
        else if (d.type === 'status') {
          if (cur) cur.status = d.status;
          if (d.status === 'paused') {
            this.streamText = ''; this.streamReasoning = ''; this.pending = [];
            this.loadMessages(); this.loadSessions();
          }
        }
      };
    },
    closeStream() {
      if (this.es) { this.es.close(); this.es = null; }
    },
  },
};