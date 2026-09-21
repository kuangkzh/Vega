window.CAState = {
  data() {
    return {
      loading: true, tab: 'chat',
      sessions: [], currentSid: null, messages: [], streamText: '', streamTools: [],
      inputText: '', usage: null, es: null,
      browseEntries: [], browsePath: '.', currentFilePath: null, fileContent: '',
      gitCurrent: '', gitBranches: '', gitLog: '', gitStatus: '', gitErr: '',
      tasks: [], taskFilter: '', taskNameFilter: '', taskDetail: null,
      config: null, models: [], activeModel: null,
      showModelForm: false, modelEdit: null,
      modelForm: { id:'', base_url:'', api_key:'', max_context:128000, compress_ratio:0.8, input_price:0, output_price:0, cache_price:0 },
      pluginList: [],
    };
  },
  computed: {
    usagePct() {
      if (!this.usage) return 0;
      const total = this.usage.input + this.usage.output + this.usage.cache;
      return Math.min(100, Math.round(total / 10000 * 100));
    },
  },
};