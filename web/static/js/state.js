window.CAState = {
  data() {
    return {
      loading: true, tab: 'workspace',
      projects: [], selectedProjectId: null, workspaceView: 'chat',
      workspaceMenuOpen: false, showProjectForm: false,
      deleteProjectInput: '',
      projectForm: { path: '', im_link: { type: 'feishu', app_id: '', app_secret: '', chat_id: '' } },
      currentSid: null, messages: [], pending: [], streamText: '', streamReasoning: '',
      inputText: '', usage: null, maxContext: 0, compressRatio: 0, es: null, globalEs: null,
      browseEntries: [], browsePath: '.', currentFilePath: null, fileContent: '', codeBrowseOpen: false, mdPreview: false,
      fileDiff: null, diffDetail: null,
      gitCurrent: '', gitErr: '',
      gitBranchList: [], gitStatusList: [], gitLogList: [], gitStatusMap: {},
      gitCommitExpand: null, gitCommitFiles: [], gitCommitMeta: null,
      gitCommitLoading: false, gitCommitError: '',
      tasks: [], taskFilter: '', taskNameFilter: '', taskDetail: null,
      approvalReason: '',
      config: null, models: [], activeModel: null,
      showModelForm: false, modelEdit: null,
      modelForm: { id:'', base_url:'', api_key:'', max_context:128000, compress_ratio:0.8, input_price:0, output_price:0, cache_price:0 },
      pluginList: [],
    };
  },
  computed: {
    currentProject() {
      return this.projects.find(p => p.id === this.selectedProjectId) || null;
    },
    tokenUsed() {
      if (!this.usage) return 0;
      return (this.usage.input || 0) + (this.usage.output || 0);
    },
    usagePct() {
      if (!this.usage || !this.maxContext) return 0;
      return Math.min(100, Math.round(this.tokenUsed / this.maxContext * 100));
    },
    compressThresholdPct() {
      const ratio = Number(this.compressRatio);
      if (!ratio || ratio <= 0 || ratio >= 1) return 0;
      return Math.min(100, Math.round(ratio * 100));
    },
  },
};