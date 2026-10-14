window.CATasks = {
  methods: {
    async loadTasks() {
      const params = new URLSearchParams();
      if (this.taskFilter) params.set('status', this.taskFilter);
      if (this.taskNameFilter) params.set('name', this.taskNameFilter);
      const q = params.toString();
      const r = await this.api('/api/tasks' + (q ? '?' + q : ''));
      this.tasks = await r.json();
    },
    async openTask(tid) {
      const r = await this.api('/api/tasks/' + tid + '/logs');
      this.taskDetail = await r.json();
      this.approvalReason = '';
    },
    closeTask() {
      this.taskDetail = null;
      this.approvalReason = '';
    },
    async approveTask(tid, approved) {
      if (!approved && !this.approvalReason.trim()) { alert('请填写审批意见（拒绝时必填）'); return; }
      await this.api('/api/tasks/' + tid + '/approve', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({approved, reason: this.approvalReason}) });
      await this.loadTasks();
      await this.openTask(tid);
    },
  },
};