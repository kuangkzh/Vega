// 可与后端 /api/file/raw 配合直接展示的图片后缀
const CODE_IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg', 'avif']);

window.CACode = {
  watch: {
    workspaceView(v) {
      if (v === 'code') {
        this.$nextTick(() => this.initEditor());
      } else if (this.cm) {
        this.cm.toTextArea();
        this.cm = null;
      }
    },
  },
  data() {
    return {
      treeChildren: {},   // 目录路径 -> 子项列表（'.' 为根目录）
      treeExpanded: {},   // 目录路径 -> true 表示已展开
      ctxMenu: null,      // 树节点右键菜单：{ x, y, node }
    };
  },
  computed: {
    // 目录树按展开状态拍平为可见行，节点自带 depth 用于缩进
    visibleRows() {
      const rows = [];
      const walk = (dir, depth) => {
        for (const e of (this.treeChildren[dir] || [])) {
          rows.push({ ...e, depth });
          if (e.is_dir && this.treeExpanded[e.path]) walk(e.path, depth + 1);
        }
      };
      walk('.', 0);
      return rows;
    },
    isMarkdownFile() {
      return ['md', 'markdown'].includes(this.extOf(this.currentFilePath));
    },
    isImageFile() {
      return CODE_IMAGE_EXTS.has(this.extOf(this.currentFilePath));
    },
  },
  methods: {
    extOf(path) {
      return ((path || '').split('.').pop() || '').toLowerCase();
    },
    fileSize(bytes) {
      if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return bytes + ' B';
    },
    formatMtime(ts) {
      const d = new Date(ts * 1000);
      const p = n => String(n).padStart(2, '0');
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    },
    // 读目录内容进树；子目录按需加载
    async loadDir(dir) {
      await this.loadGitStatus();
      const r = await this.api('/api/browse?path=' + encodeURIComponent(dir) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      if (d.error) { alert(d.error); return; }
      this.treeChildren[dir] = d.entries.map(e => {
        const st = this.entryStatus(e.path);
        return { ...e, statusLetters: st.text, statusCls: st.cls };
      });
    },
    // 逐级展开到 dir（含加载沿途目录），用于定位当前打开文件
    async expandTo(dir) {
      const parts = (dir || '').split('/').filter(p => p && p !== '.');
      let cur = '.';
      for (const seg of parts) {
        cur = cur === '.' ? seg : cur + '/' + seg;
        this.treeExpanded[cur] = true;
        if (!this.treeChildren[cur]) await this.loadDir(cur);
      }
    },
    async toggleDir(node) {
      if (this.treeExpanded[node.path]) { delete this.treeExpanded[node.path]; return; }
      this.treeExpanded[node.path] = true;
      if (!this.treeChildren[node.path]) await this.loadDir(node.path);
    },
    openCtxMenu(ev, node) {
      // 菜单高度约 72px，靠近视口底部时上移，避免被裁掉
      this.ctxMenu = { x: ev.clientX, y: Math.min(ev.clientY, window.innerHeight - 72), node };
    },
    async copyCtx(which) {
      const m = this.ctxMenu;
      if (!m) return;
      const text = which === 'name' ? m.node.name : m.node.path;
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
      } else {
        // 非安全上下文（如通过局域网 IP 用 HTTP 访问）没有 clipboard API，退回 execCommand
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      this.ctxMenu = null;
    },
    // 面包屑：把项目相对路径切成可点击段（hasFile=true 时最后一段视为文件名，不可点）
    pathSegments(path, hasFile) {
      const parts = (path || '').split('/').filter(p => p && p !== '.');
      let acc = '';
      return parts.map((name, i) => {
        acc = acc ? acc + '/' + name : name;
        return { name, path: acc, isFile: !!hasFile && i === parts.length - 1 };
      });
    },
    // 以 base 所在目录（或项目根）为基准，解析出项目相对路径
    resolvePath(href, base) {
      let raw;
      try { raw = decodeURI(href.split('#')[0].split('?')[0]); } catch { raw = href.split('#')[0].split('?')[0]; }
      if (!raw) return null;
      if (raw.startsWith('/')) return raw.replace(/^\/+/, '');
      const parts = (base || '').includes('/') ? base.split('/').slice(0, -1) : [];
      for (const seg of raw.split('/')) {
        if (seg === '..') parts.pop();
        else if (seg && seg !== '.') parts.push(seg);
      }
      return parts.join('/');
    },
    // 路径分发：目录在树里展开，文件进编辑器
    goPath(path, isDir) {
      if (!path && path !== '.') return;
      if (isDir) { this.codeBrowseOpen = true; this.expandTo(path); }
      else this.openFile(path);
    },
    onMdClick(e) {
      const a = e.target.closest && e.target.closest('a');
      if (!a) return;
      const href = a.getAttribute('href') || '';
      if (!href || href.startsWith('#')) return;
      if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
        if (/^https?:/i.test(href)) { e.preventDefault(); window.open(href, '_blank', 'noopener'); }
        return;
      }
      e.preventDefault();
      this.goPath(this.resolvePath(href, this.currentFilePath), href.endsWith('/'));
    },
    entryStatus(path) {
      const letters = new Set();
      for (const p of Object.keys(this.gitStatusMap)) {
        if (p === path || p.startsWith(path + '/')) {
          for (const l of this.gitStatusMap[p].letters) letters.add(l);
        }
      }
      if (!letters.size) return { text: '', cls: '' };
      const arr = this.sortStatusLetters([...letters]);
      const clsMap = { A: 'git-badge-added', U: 'git-badge-added', M: 'git-badge-modified', D: 'git-badge-deleted', R: 'git-badge-renamed', C: 'git-badge-renamed' };
      return { text: arr.join(' '), cls: clsMap[arr[0]] || '' };
    },
    async loadFileDiff(path) {
      const r = await this.api('/api/file/diff?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      // 非 git 追踪的文件（含无 git 仓库的情况）没有基线版本，不展示差异
      const hasDiff = d.tracked && ((d.lines && d.lines.some(Boolean)) || (d.deleted && d.deleted.length));
      this.fileDiff = hasDiff ? d : null;
    },
    async openFile(path) {
      if (CODE_IMAGE_EXTS.has(this.extOf(path))) { this.showImage(path); return; }
      const r = await this.api('/api/file?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || ''));
      const d = await r.json();
      if (d.is_dir) { this.expandTo(path); return; }
      if (d.error) {
        // 无法打开（如超过 1MB）：改为在正文区提示，并保留下载入口
        if (this.cm) { this.cm.toTextArea(); this.cm = null; }
        this.currentFilePath = path;
        this.codeBrowseOpen = false;
        this.mdPreview = false;
        this.fileContent = '';
        this.fileDiff = null;
        this.fileNotice = d.error;
        this.syncCodeUrl();
        return;
      }
      if (d.content !== undefined) {
        const isMd = ['md', 'markdown'].includes(this.extOf(path));
        // markdown 直接进预览：先释放编辑器，避免 CodeMirror 的 DOM 残留在页面上
        if (isMd && this.cm) { this.cm.toTextArea(); this.cm = null; }
        this.currentFilePath = path;
        this.codeBrowseOpen = false;
        this.fileNotice = '';
        this.mdPreview = isMd;  // markdown 默认渲染展示
        this.fileContent = d.content;
        this.fileDiff = null;
        this.syncCodeUrl();
        await this.loadFileDiff(path);
        if (!isMd) this.$nextTick(() => this.initEditor());
      }
    },
    syncCodeUrl() {
      const url = new URL(location.href);
      if (this.selectedProjectId) url.searchParams.set('project', this.selectedProjectId);
      else url.searchParams.delete('project');
      if (this.currentFilePath) url.searchParams.set('path', this.currentFilePath);
      else url.searchParams.delete('path');
      history.replaceState(null, '', url);
    },
    toggleMdPreview() {
      if (this.mdPreview) {
        this.mdPreview = false;
        this.$nextTick(() => this.initEditor());
      } else {
        if (this.cm) { this.fileContent = this.cm.getValue(); this.cm.toTextArea(); this.cm = null; }
        this.mdPreview = true;
      }
    },
    downloadUrl(path) {
      return '/api/file/download?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || '');
    },
    // 图片走 inline 接口，供 code 页 <img> 与 markdown 内联图片使用
    rawUrl(path) {
      return '/api/file/raw?path=' + encodeURIComponent(path) + '&project=' + encodeURIComponent(this.selectedProjectId || '');
    },
    // markdown 相对图片地址：以当前 md 文件所在目录为基准解析，绝对地址原样保留
    mdImageUrl(src) {
      if (!src || /^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith('//') || !this.currentFilePath) return src;
      const path = this.resolvePath(src, this.currentFilePath);
      return path ? this.rawUrl(path) : src;
    },
    // 图片文件不进编辑器，直接展示
    showImage(path) {
      if (this.cm) { this.cm.toTextArea(); this.cm = null; }
      this.currentFilePath = path;
      this.codeBrowseOpen = false;
      this.mdPreview = false;
      this.fileContent = '';
      this.fileNotice = '';
      this.fileDiff = null;
      this.syncCodeUrl();
    },
    async saveFile() {
      if (this.cm) this.fileContent = this.cm.getValue();
      await this.api('/api/file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({project_id: this.selectedProjectId, path: this.currentFilePath, content: this.fileContent}) });
      await this.loadFileDiff(this.currentFilePath);
      this.applyDiffMarks();
    },
    fileMode(path) {
      const ext = this.extOf(path);
      const map = {
        py: 'python', pyw: 'python',
        js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript',
        html: 'htmlmixed', htm: 'htmlmixed', vue: 'htmlmixed',
        css: 'css', scss: 'css', less: 'css',
        xml: 'xml', svg: 'xml',
        json: { name: 'javascript', json: true },
        md: 'markdown', markdown: 'markdown',
        yml: 'yaml', yaml: 'yaml',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        c: 'clike', h: 'clike', cpp: 'clike', cc: 'clike', java: 'clike', cs: 'clike', php: 'clike', go: 'clike', rs: 'clike',
        sql: 'sql', txt: null,
      };
      return map[ext] === undefined ? null : map[ext];
    },
    langLabel() {
      const ext = this.extOf(this.currentFilePath);
      const label = {
        py: 'Python', js: 'JavaScript', mjs: 'JavaScript', jsx: 'JSX', ts: 'TypeScript',
        html: 'HTML', htm: 'HTML', css: 'CSS', scss: 'SCSS', xml: 'XML', json: 'JSON',
        md: 'Markdown', yml: 'YAML', yaml: 'YAML', sh: 'Shell', sql: 'SQL',
        c: 'C', h: 'C', cpp: 'C++', java: 'Java', cs: 'C#', php: 'PHP', go: 'Go', rs: 'Rust',
      };
      return label[ext] || '';
    },
    initEditor() {
      if (this.isImageFile) return;
      const ta = this.$refs.codeEditor;
      if (!ta || typeof window.CodeMirror === 'undefined') return;
      const mode = this.fileMode(this.currentFilePath);
      if (this.cm && this.cm.getTextArea() === ta) {
        this.cm.setOption('mode', mode);
        if (this.cm.getValue() !== this.fileContent) this.cm.setValue(this.fileContent);
        this.resizeEditor();
        this.applyDiffMarks();
        return;
      }
      if (this.cm) { this.cm.toTextArea(); this.cm = null; }
      this.cm = window.CodeMirror.fromTextArea(ta, {
        mode: mode,
        lineNumbers: true,
        gutters: ['CodeMirror-linenumbers', 'cm-diff-gutter'],
        lineWrapping: false,
        tabSize: 4,
        indentUnit: 4,
        readOnly: false,
        theme: 'default',
      });
      this.cm.on('change', () => { this.fileContent = this.cm.getValue(); this.resizeEditor(); });
      this.resizeEditor();
      this.applyDiffMarks();
    },
    clearDiffMarks() {
      if (!this.cm) return;
      for (let i = 0; i < this.cm.lineCount(); i++) {
        this.cm.removeLineClass(i, 'background', 'cm-diff-added');
        this.cm.removeLineClass(i, 'background', 'cm-diff-modified');
        this.cm.setGutterMarker(i, 'cm-diff-gutter', null);
      }
    },
    applyDiffMarks() {
      this.clearDiffMarks();
      if (!this.cm || !this.fileDiff) return;
      const d = this.fileDiff;
      const diffLines = d.lines || [];
      const lineCount = this.cm.lineCount();
      const byLine = {};
      for (const h of (d.hunks || [])) {
        if (h.type === 'delete') {
          const a = h.anchor < lineCount ? h.anchor : lineCount - 1;
          if (a >= 0) byLine[a] = h;
        } else {
          for (let n = h.newStart; n < h.newEnd && n < lineCount; n++) byLine[n] = h;
        }
      }
      this._diffHunksByLine = byLine;
      for (let i = 0; i < diffLines.length && i < lineCount; i++) {
        const type = diffLines[i];
        if (type === 'added') {
          this.cm.addLineClass(i, 'background', 'cm-diff-added');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('+', 'cm-diff-added', i));
        } else if (type === 'modified') {
          this.cm.addLineClass(i, 'background', 'cm-diff-modified');
          this.cm.setGutterMarker(i, 'cm-diff-gutter', this._gutterMarker('~', 'cm-diff-modified', i));
        }
      }
      for (const del of (d.deleted || [])) {
        let anchor = del.anchorNewNo;
        if (anchor >= lineCount) anchor = lineCount - 1;
        if (anchor < 0) continue;
        this.cm.setGutterMarker(anchor, 'cm-diff-gutter', this._gutterMarker('-', 'cm-diff-deleted', anchor));
      }
    },
    _gutterMarker(text, cls, lineNo) {
      const el = document.createElement('span');
      el.textContent = text;
      el.className = cls;
      if (lineNo !== undefined) {
        el.style.cursor = 'pointer';
        el.title = '点击查看改动前对比';
        el.addEventListener('click', () => this.showDiffDetail(lineNo));
      }
      return el;
    },
    showDiffDetail(lineNo) {
      const hunk = this._diffHunksByLine ? this._diffHunksByLine[lineNo] : null;
      if (!hunk || !this.fileDiff) return;
      const headLines = this.fileDiff.headLines || [];
      const curLines = (this.cm ? this.cm.getValue() : this.fileContent).split('\n');
      const head = headLines.slice(hunk.headStart, hunk.headEnd);
      const neu = curLines.slice(hunk.newStart, hunk.newEnd);
      let top = 0, left = 0, width = 520;
      if (this.cm) {
        const c = this.cm.charCoords({ line: lineNo, ch: 0 }, 'window');
        top = c.bottom;
        left = c.left;
        width = this.cm.getWrapperElement().getBoundingClientRect().width;
      }
      this.diffDetail = {
        type: hunk.type,
        oldLines: head.map((t, i) => ({ no: hunk.headStart + i + 1, text: t })),
        newLines: neu.map((t, i) => ({ no: hunk.newStart + i + 1, text: t })),
        top, left, width, anchorBottom: top,
      };
      this.$nextTick(() => this.fitDiffPopover());
    },
    fitDiffPopover() {
      const d = this.diffDetail;
      const el = this.$refs.diffPopover;
      if (!d || !el) return;
      const rect = el.getBoundingClientRect();
      const margin = 8;
      if (d.anchorBottom + rect.height > window.innerHeight - margin) {
        // 下方放不下，翻到锚点行上方
        d.top = d.anchorBottom - rect.height - (this.cm ? this.cm.defaultTextHeight() : 16);
        if (d.top < margin) d.top = margin;
      }
      if (d.left + rect.width > window.innerWidth - margin) {
        d.left = Math.max(margin, window.innerWidth - rect.width - margin);
      }
    },
    closeDiffDetail() { this.diffDetail = null; },
    resizeEditor() {
      if (!this.cm) return;
      const lh = this.cm.defaultTextHeight() || 16;
      const lines = Math.max(this.cm.lineCount(), 1);
      this.cm.setSize('100%', Math.max(320, lines * lh + 16) + 'px');
    },
  },
};
