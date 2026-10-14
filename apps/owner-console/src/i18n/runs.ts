export const runCopy = {
  title: "运行记录",
  description: "查看每一期节目从来源归档到待发布的完整处理过程。",
  currentPanel: "当前运行概览与历史尝试",
  list: {
    empty: "还没有运行记录",
    emptyBody: "节目进入处理后会显示在这里。",
    cost: "Modal 预计成本",
    tokens: "Agent tokens",
    latestChange: "最近更新",
    queuedAt: "进入队列",
    firstClaimedAt: "首次开始处理",
    notClaimed: "尚未领取",
    open: "查看运行详情"
  },
  actions: {
    refresh: "刷新运行记录",
    back: "返回运行记录",
    retry: "从最近有效进度继续",
    acknowledgeAudit: "确认听感问题并继续",
    retrying: "正在重新排队",
    history: (count: number) => `历史尝试（${count}）`,
    technical: "技术信息",
    copyDiagnostics: "复制诊断信息",
    copyingDiagnostics: "正在生成诊断信息",
    diagnosticsCopied: "诊断信息已复制",
    diagnosticsFailed: "无法复制诊断信息"
  },
  states: {
    initialLoading: "正在载入运行记录…",
    refreshing: "正在后台更新",
    stale: "暂时无法刷新，正在显示上次成功获取的数据。",
    queued: "已排队",
    processing: "处理中",
    active: "处理中",
    action_required: "需要处理",
    waiting_retry: "等待重试",
    waiting_to_retry: "等待重试",
    publication_ready: "已就绪",
    paused: "已暂停",
    completed: "已完成",
    not_applicable: "不适用",
    not_started: "尚未开始",
    superseded: "已由后续重试取代",
    failed: "失败"
  },
  steps: {
    source_archive: "来源归档",
    source_analysis: "说话者分析与转录",
    translation_authoring: "翻译",
    translation_review: "独立校对",
    content_editing: "广告与开头高光处理",
    voice_render: "配音与拼接",
    publication_audio_quality: "母带与音频质检",
    publication_queue: "待发布",
    translation: "翻译",
    render: "配音与拼接",
    quality_gate: "母带与音频质检"
  },
  error: {
    title: "当前处理遇到问题",
    retryPlan: "将从这里继续",
    reused: "将复用",
    preservedTargets: "已完成的翻译段落会保留",
    repeatedTargets: "本次没有可恢复的翻译段落",
    summaries: {
      translation_stalled:
        "翻译连续两轮没有新增完成段落，系统已暂停，避免继续消耗 tokens。",
      translation_provider:
        "处理 Agent 本轮没有正常完成，已保存的翻译进度仍可继续使用。",
      translation_provider_mismatch:
        "翻译项目属于另一个处理 Agent；请重新处理整期节目以启动新的会话。",
      agent_auth:
        "处理 Agent 未在本机登录；请在运行 TransPodcast 的电脑上重新登录后重试。",
      translation_schema:
        "翻译结果没有通过确定性的结构完整性检查。",
      listening_audit:
        "听感审计在自动重新配音一次后仍发现严重问题。请在“母带与音频质检”中查看问题单元；确认后节目进入待发布，问题会保留在发布检查里。"
    }
  },
  history: {
    title: "历史尝试",
    duration: "耗时",
    checkpoint: "恢复检查点",
    transfer: "传输数据",
    modalAccount: "Modal 账户",
    modalRoute: "账户路由",
    startedAt: "开始时间",
    completedAt: "完成时间",
    timingEvidence: "时间依据",
    legacyTiming: "旧记录，时间为推算值",
    errorKind: "错误类型",
    empty: "还没有历史尝试。",
    openAttempt: (sequence: number, stage: string) =>
      `查看第 ${sequence} 次${stage}尝试详情`
  },
  progress: {
    targets: "段翻译完成",
    authorTurns: (count: number) => `翻译会话 ${count} 轮`,
    reviewerTurns: (count: number) => `校对会话 ${count} 轮`,
    unavailable: "这条旧记录没有独立进度数据"
  },
  analysisFlow: {
    title: "来源分析内部流程",
    wordTimeline: "声学与逐词时间线",
    wordTimelineHint: "说话人分轨 · ASR · 逐词对齐",
    speakerOwnership: "说话人归属",
    ownershipSummary: (
      regions: number,
      changedWords: number,
      disputedRegions: number
    ) =>
      `${regions} 个风险区 · ${changedWords} 个词已调整 · ${disputedRegions} 个争议区`,
    naturalSegmentation: "自然切分",
    segmentationSummary: (segments: number, fallbackBoundaries: number) =>
      `${segments} 个最终片段 · ${fallbackBoundaries} 个兜底切点`,
    degraded: "部分风险区已降级保留"
  },
  metrics: {
    archive_bytes: "原音归档",
    segments: "转录片段",
    speakers: "识别说话者",
    raw_speaker_candidates: "原始声学候选",
    transfer_bytes: "上传数据",
    call_seconds: "模型调用",
    estimated_cost_usd: "Modal 预计成本",
    turns: "Agent 轮次",
    sessions: "独立会话",
    input_tokens: "输入 tokens",
    output_tokens: "输出 tokens",
    rendered_segments: "配音片段",
    output_bytes: "输出音频",
    pronunciation_policy_version: "2.5 注音策略",
    pronunciation_annotation_count: "英文注音片段",
    pronunciation_fallback_count: "原文回退片段",
    chinese_replacement_count: "中文名称替换",
    codec: "编码",
    sample_rate_hz: "采样率",
    channels: "声道",
    bitrate_kbps: "平均码率",
    loudness_lufs: "综合响度",
    true_peak_dbtp: "真峰值",
    timeline_continuous: "时间线连续",
    boundary_artifacts: "边界异常",
    source_language_leakage: "原语言残留"
  },
  values: {
    present: "有",
    absent: "无",
    yes: "是",
    no: "否"
  },
  usage: {
    input: "输入",
    output: "输出"
  },
  timing: {
    title: "排队与开始时间"
  },
  technical: {
    modalAccount: "Modal 账户",
    modalRoute: "自动路由",
    parallelOperations: "并行模型调用",
    modalUsage: "Modal",
    localOnly: "不使用（本地处理）"
  }
} as const;
