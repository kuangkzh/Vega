window.CACode = {
  methods: {
    browse(path) {
      if (path === '..') {
        this.browsePath = this.browsePath.includes('/') ? (this.browsePath.slice(0, this.browsePath.lastIndexOf('/')) || '.') : '.';
      } else {
        this.browsePath = path;
      }
      this.fetchBrowse(this.browsePath);
    },
    browseJoin(name) { this.browse(this.browsePath === '.' ? name : this.browsePath + '/' + name); },
    browseJoinPath(name) { return this.browsePath === '.' ? name : this.browsePath + '/' + name; },
    async fetchBrowse(path) {
      const r = await this.api('/api/browse?path=' + encodeURIComponent(path || '.'));
      const d = await r.json();
      if (d.entries) { this.browseEntries = d.entries; this.browsePath = d.path; }
    },
    async openFile(path) {
      const r = await this.api('/api/file?path=' + encodeURIComponent(path));
      const d = await r.json();
      if (d.content !== undefined) { this.currentFilePath = path; this.fileContent = d.content; }
    },
    async saveFile() {
      await this.api('/api/file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path: this.currentFilePath, content: this.fileContent}) });
    },
  },
};