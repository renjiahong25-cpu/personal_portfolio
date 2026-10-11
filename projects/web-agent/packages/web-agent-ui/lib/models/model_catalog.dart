class AiModel {
  const AiModel({
    required this.id,
    required this.name,
    required this.group,
    required this.description,
  });

  final String id;
  final String name;

  /// 'web-agent' (可并行) 或 'opencode-native' (仅展示)
  final String group;
  final String description;
}

const webAgentModels = <AiModel>[
  AiModel(
    id: 'deepseek',
    name: 'DeepSeek',
    group: 'web-agent',
    description: 'www.deepseek.com 网页对话',
  ),
  AiModel(
    id: 'doubao',
    name: '豆包',
    group: 'web-agent',
    description: 'www.doubao.com/chat 网页对话',
  ),
  AiModel(
    id: 'chatgpt',
    name: 'ChatGPT',
    group: 'web-agent',
    description: 'chat.openai.com 网页对话',
  ),
  AiModel(
    id: 'gemini',
    name: 'Gemini',
    group: 'web-agent',
    description: 'gemini.google.com 网页对话',
  ),
  AiModel(
    id: 'claude',
    name: 'Claude',
    group: 'web-agent',
    description: 'claude.ai 网页对话',
  ),
  AiModel(
    id: 'grok',
    name: 'Grok',
    group: 'web-agent',
    description: 'grok.com 网页对话',
  ),
];

const parallelSystemPrompt =
    '你是 Web Agent 中的一个 AI 角色，请在网页版 AI 对话中独立、直接地回复用户，'
    '不要提及“我在和谁并行”这类过程信息，也不要询问其他角色。';