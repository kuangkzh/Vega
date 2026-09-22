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
    },
    closeTask() {
      this.taskDetail = null;
    },
    async approveTask(tid, approved) {
      await this.api('/api/tasks/' + tid + '/approve', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({approved}) });
      await this.loadTasks();
      await this.openTask(tid);
    },
  },
};