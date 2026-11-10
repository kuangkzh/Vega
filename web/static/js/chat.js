// 工具参数美化结果缓存（按 tool_call 对象），避免每次重渲染都 JSON 解析+美化一遍
const fmtCache = new WeakMap();
// 流式增量缓冲：先攒在非响应式变量里，定时合并到 streamText，避免每个 chunk 触发一次全量渲染
const streamBuf = { text: '', reasoning: '' };
let streamTimer = null;

window.CAChat = {
  watch: {
    streamText() { this.scrollChat(); },
    streamReasoning() { this.scrollChat(); },
    tab(v) { if (v === 'workspace' && this.workspaceView === 'chat') this.scrollChat(true); },
    workspaceView(v) { if (v === 'chat') this.scrollChat(true); },
  },
  computed: {
    currentSession() {
      if (!this.currentSid) return null;
      const p = this.currentProject;
      return (p && p.sessions.find(x => x.id === this.currentSid)) || null;
    },
    currentSessionTitle() {
      const s = this.currentSession;
      return s ? '会话：' + this.shortTitle(s.title) : '会话列表';
    },
  },
  methods: {
    scrollChat(force = false) {
      this.$nextTick(() => {
        const el = this.$refs.chatScroll;
        if (el && (force || this.stickToBottom)) el.scrollTop = el.scrollHeight;
      });
    },
    onChatScroll() {
      const el = this.$refs.chatScroll;
      if (!el) return;
      this.stickToBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 24;
    },
    // 流式增量入缓冲，80ms 合并 flush 一次（chunk 频率由供应商决定，不直接决定渲染次数）
    pushStream(kind, chunk) {
      if (kind === 'reasoning') streamBuf.reasoning += chunk;
      else streamBuf.text += chunk;
      if (!streamTimer) streamTimer = setTimeout(() => { streamTimer = null; this.flushStream(); }, 80);
    },
    flushStream() {
      if (streamBuf.text) { this.streamText += streamBuf.text; streamBuf.text = ''; }
      if (streamBuf.reasoning) { this.streamReasoning += streamBuf.reasoning; streamBuf.reasoning = ''; }
    },
    // 一轮流式输出结束/切换会话：丢弃未 flush 的缓冲并清空展示区
    endStreamCycle() {
      if (streamTimer) { clearTimeout(streamTimer); streamTimer = null; }
      streamBuf.text = ''; streamBuf.reasoning = '';
      this.streamText = ''; this.streamReasoning = '';
    },
    async loadProjects() {
      const r = await this.api('/api/projects');
      const list = await r.json();
      for (const p of list) {
        const old = this.projects.find(x => x.id === p.id);
        p.expanded = old ? !!old.expanded : false;
        p.menuOpen = old ? !!old.menuOpen : false;
        p.sessions = (p.sessions || []).sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
      }
      this.projects = list;
      this.sortProjects();
    },
    projectLatestTime(p) {
      let t = 0;
      for (const s of (p.sessions || [])) t = Math.max(t, s.updated_at || s.created_at || 0);
      return t;
    },
    sortProjects() {
      this.projects.sort((a, b) => this.projectLatestTime(b) - this.projectLatestTime(a));
    },
    toggleProject(pid) {
      const p = this.projects.find(x => x.id === pid);
      if (p) p.expanded = !p.expanded;
    },
    toggleProjectMenu(pid) {
      const p = this.projects.find(x => x.id === pid);
      if (!p) return;
      const willOpen = !p.menuOpen;
      for (const x of this.projects) x.menuOpen = false;
      p.menuOpen = willOpen;
    },
    selectProject(pid) {
      const p = this.projects.find(x => x.id === pid);
      if (!p) return;
      const wasSelected = this.selectedProjectId === pid;
      this.selectedProjectId = pid;
      this.workspaceView = 'chat';
      p.expanded = wasSelected ? !p.expanded : true;
    },
    closeProjectMenus() {
      for (const p of this.projects) p.menuOpen = false;
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
    statusBadge(status) {
      const map = { running: 'text-bg-success', paused: 'text-bg-secondary', complete: 'text-bg-primary', error: 'text-bg-danger' };
      return map[status] || 'text-bg-secondary';
    },
    formatArgs(tc) {
      if (!tc) return '';
      if (fmtCache.has(tc)) return fmtCache.get(tc);
      const s = (tc.function && tc.function.arguments) || '';
      let out = s;
      if (s) { try { out = JSON.stringify(JSON.parse(s), null, 2); } catch (e) { out = s; } }
      fmtCache.set(tc, out);
      return out;
    },
    formatTokens(n) {
      n = Number(n) || 0;
      if (n >= 1e6) return (n / 1e6).toFixed(1) + 'm';
      if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
      return String(n);
    },
    async selectSession(sid) {
      const s = this.findSession(sid);
      if (!s) return;
      this.closeStream();
      this.selectedProjectId = s.project_id;
      this.workspaceView = 'chat';
      this.workspaceMenuOpen = false;
      this.currentSid = sid; this.endStreamCycle(); this.pending = [];
      this.usage = s.token_usage; this.maxContext = s.max_context || 0; this.compressRatio = s.compress_ratio || 0.8;
      await this.loadMessages();
      this.openStream();
    },
    findSession(sid) {
      for (const p of this.projects) {
        const s = (p.sessions || []).find(x => x.id === sid);
        if (s) return s;
      }
      return null;
    },
    async loadMessages() {
      if (!this.currentSid) return;
      const r = await this.api('/api/sessions/' + this.currentSid + '/messages'); this.messages = await r.json();
      this.scrollChat(true);
      await this.loadPending();
    },
    async loadPending() {
      if (!this.currentSid) return;
      const r = await this.api('/api/sessions/' + this.currentSid + '/pending');
      this.pending = await r.json() || [];
    },
    async newSession(projectId) {
      if (!projectId) return;
      const r = await this.api('/api/sessions', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ project_id: projectId }) });
      const s = await r.json();
      this.selectedProjectId = projectId;
      const p = this.projects.find(x => x.id === projectId);
      if (p) p.expanded = true;
      await this.loadProjects();
      await this.selectSession(s.id);
    },
    async forkMessage(sid, index) {
      const r = await this.api('/api/sessions/' + sid + '/fork', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ message_index: index }) });
      const s = await r.json();
      this.selectedProjectId = s.project_id;
      await this.loadProjects();
      await this.selectSession(s.id);
    },
    async deleteSession(sid) {
      if (!confirm('确定删除该会话？此操作不可恢复。')) return;
      await this.api('/api/sessions/' + sid, { method:'DELETE' });
      if (this.currentSid === sid) {
        this.currentSid = null;
        this.messages = [];
        this.pending = [];
        this.endStreamCycle();
        this.usage = null;
        this.closeStream();
      }
      await this.loadProjects();
    },
    async toggleIm(sid) {
      await this.api('/api/sessions/' + sid + '/im/toggle', { method:'POST' });
      await this.loadProjects();
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
      await this.api('/api/sessions/' + this.currentSid + '/message', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content: text}) });
      this.endStreamCycle();
      if (!this.es) this.openStream();
    },
    async resumeSession() {
      if (!this.currentSid) return;
      await this.api('/api/sessions/' + this.currentSid + '/resume', { method:'POST' });
    },
    async pauseSession() {
      if (!this.currentSid) return;
      await this.api('/api/sessions/' + this.currentSid + '/pause', { method:'POST' });
    },
    async togglePause() {
      if (!this.currentSid) return;
      if (this.currentSession && this.currentSession.status === 'running') await this.pauseSession();
      else await this.resumeSession();
    },
    openStream() {
      this.closeStream();
      if (!this.currentSid) return;
      const sid = this.currentSid;
      this.es = new EventSource('/api/sessions/' + sid + '/stream');
      this.es.onmessage = (ev) => {
        if (sid !== this.currentSid) return;  // 忽略残留旧流事件，避免切换会话时串台
        const d = JSON.parse(ev.data);
        const cur = this.findSession(sid);
        if (d.type === 'text') this.pushStream('text', d.content);
        else if (d.type === 'reasoning') this.pushStream('reasoning', d.content);
        else if (d.type === 'message') {
          this.messages.push(d.message);
          if (cur) cur.message_count = this.messages.length;
          this.scrollChat();
          if (d.message && d.message.role === 'assistant') this.endStreamCycle();
        }
        else if (d.type === 'pending_add') {
          if (!this.pending.some(x => x.id === d.item.id)) this.pending.push(d.item);
        }
        else if (d.type === 'pending_remove') {
          this.pending = this.pending.filter(x => x.id !== d.id);
        }
        else if (d.type === 'usage') this.usage = d.usage;
        else if (d.type === 'restart') {
          this.loadProjects().then(() => this.selectSession(d.new_id));
        }
        else if (d.type === 'status') {
          if (cur) cur.status = d.status;
          if (d.status === 'paused' || d.status === 'complete' || d.status === 'error') {
            this.endStreamCycle();
            this.loadMessages(); this.loadProjects();
          }
        }
      };
    },
    closeStream() {
      if (streamTimer) { clearTimeout(streamTimer); streamTimer = null; }
      this.flushStream();
      if (this.es) { this.es.close(); this.es = null; }
    },
    openGlobalStream() {
      if (this.globalEs) return;
      this.globalEs = new EventSource('/api/events');
      this.globalEs.onmessage = (ev) => {
        const d = JSON.parse(ev.data);
        if (d.type === 'session') this.upsertSession(d.session);
        else if (d.type === 'project') this.upsertProject(d.project);
      };
    },
    upsertSession(sess) {
      const p = this.projects.find(x => x.id === sess.project_id);
      if (!p) return;
      p.sessions = p.sessions || [];
      const i = p.sessions.findIndex(x => x.id === sess.id);
      if (i >= 0) p.sessions[i] = sess;
      else p.sessions.push(sess);
      p.sessions.sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
      this.sortProjects();
    },
    upsertProject(project) {
      if (project.deleted) {
        const i = this.projects.findIndex(x => x.id === project.id);
        if (i >= 0) this.projects.splice(i, 1);
        if (this.selectedProjectId === project.id) {
          this.selectedProjectId = null; this.currentSid = null; this.messages = [];
          this.closeStream();
        }
        return;
      }
      const i = this.projects.findIndex(x => x.id === project.id);
      if (i >= 0) {
        const old = this.projects[i];
        this.projects[i] = { ...project, sessions: old.sessions || [], expanded: old.expanded, menuOpen: old.menuOpen };
      } else {
        this.projects.push({ ...project, sessions: [], expanded: false, menuOpen: false });
      }
      this.sortProjects();
    },
    async createProject() {
      if (!this.projectForm.path.trim()) { alert('请填写项目路径'); return; }
      await this.api('/api/projects', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ path: this.projectForm.path, im_link: this.projectForm.im_link }) });
      this.showProjectForm = false;
      this.projectForm = { path: '', im_link: { type: 'feishu', chat_id: '' } };
      await this.loadProjects();
    },
    openProjectCode(pid) {
      this.closeProjectMenus();
      window.open('/code?project=' + encodeURIComponent(pid), '_blank');
    },
    openProjectGit(pid) {
      this.closeProjectMenus();
      window.open('/git?project=' + encodeURIComponent(pid), '_blank');
    },
    openProjectSettings(pid) {
      const p = this.projects.find(x => x.id === pid);
      if (!p) return;
      this.closeProjectMenus();
      this.selectedProjectId = pid;
      this.workspaceView = 'project_settings';
      this.deleteProjectInput = '';
      this.projectForm = { path: p.path, im_link: { ...(p.im_link || {}) } };
    },
    async saveProjectSettings() {
      if (!this.selectedProjectId) return;
      await this.api('/api/projects/' + this.selectedProjectId, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ path: this.projectForm.path, im_link: this.projectForm.im_link }) });
      await this.loadProjects();
    },
    async deleteProject(pid) {
      const p = this.projects.find(x => x.id === pid);
      if (!p || !this.deleteProjectInput || this.deleteProjectInput !== p.name) return;
      await this.api('/api/projects/' + pid, { method:'DELETE' });
      if (this.selectedProjectId === pid) {
        this.selectedProjectId = null; this.currentSid = null; this.messages = [];
        this.workspaceView = 'chat';
        this.closeStream();
      }
      this.deleteProjectInput = '';
      await this.loadProjects();
    },
    },
};