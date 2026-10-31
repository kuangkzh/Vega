window.CASettings = {
  methods: {
    async loadSettings() {
      this.config = await (await this.api('/api/config')).json();
      this.config.approver = this.config.approver || {};
      this.config.im = this.config.im || {};
      this.config.im.feishu = this.config.im.feishu || {};
      this.config.include_reasoning_content = this.config.include_reasoning_content !== false;
      this.pluginList = await (await this.api('/api/plugins')).json();
      const d = await (await this.api('/api/models')).json();
      this.models = d.models; this.activeModel = d.active;
    },
    setTextlist(obj, key, event) { obj[key] = event.target.value.split('\n').map(x => x.trim()).filter(x => x); },
    async saveConfig() {
      const plugins = {};
      for (const p of this.pluginList) plugins[p.name] = p.value;
      const payload = {
        system_prompt: this.config.system_prompt,
        retry: this.config.retry,
        max_rounds: this.config.max_rounds,
        include_reasoning_content: this.config.include_reasoning_content,
        approver: this.config.approver,
        summarizer: this.config.summarizer,
        im: this.config.im,
        plugins,
      };
      await this.api('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      await this.loadSettings();
    },
    newModel() {
      this.modelEdit = null;
      this.modelForm = { id:'', base_url:'', api_key:'', max_context:128000, compress_ratio:0.8, tool_compat_mode:false };
      this.showModelForm = true;
    },
    editModel(m) {
      this.modelEdit = m;
      this.modelForm = { id:m.id, base_url:m.base_url, api_key:m.api_key, max_context:m.max_context, compress_ratio:m.compress_ratio, tool_compat_mode:!!m.tool_compat_mode };
      this.showModelForm = true;
    },
    async saveModel() {
      const url = this.modelEdit ? '/api/models/' + this.modelEdit.id : '/api/models';
      const method = this.modelEdit ? 'PUT' : 'POST';
      await this.api(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(this.modelForm) });
      this.showModelForm = false;
      await this.loadSettings();
    },
    async activateModel(mid) { await this.api('/api/models/' + mid + '/activate', { method:'POST' }); await this.loadSettings(); },
    async deleteModel(mid) { await this.api('/api/models/' + mid, { method:'DELETE' }); await this.loadSettings(); },
  },
};