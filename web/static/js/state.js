window.CAState = {
  data() {
    return {
      loading: true, tab: 'chat',
      sessions: [], currentSid: null, messages: [], streamText: '', streamReasoning: '',
      inputText: '', usage: null, maxContext: 0, es: null, globalEs: null, sessionsOpen: false,
      browseEntries: [], browsePath: '.', currentFilePath: null, fileContent: '', codeBrowseOpen: false, mdPreview: false,
      gitCurrent: '', gitErr: '',
      gitBranchList: [], gitStatusList: [], gitLogList: [],
      tasks: [], taskFilter: '', taskNameFilter: '', taskDetail: null,
      config: null, models: [], activeModel: null,
      showModelForm: false, modelEdit: null,
      modelForm: { id:'', base_url:'', api_key:'', max_context:128000, compress_ratio:0.8, input_price:0, output_price:0, cache_price:0 },
      pluginList: [],
    };
  },
  computed: {
    tokenUsed() {
      if (!this.usage) return 0;
      return (this.usage.input || 0) + (this.usage.output || 0);
    },
    usagePct() {
      if (!this.usage || !this.maxContext) return 0;
      return Math.min(100, Math.round(this.tokenUsed / this.maxContext * 100));
    },
  },
};