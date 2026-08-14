const SYNONYM_MAP: Record<string, string[]> = {
  rust: ['Rust语言', 'Rust编程', 'Rust教程'],
  python: ['Python语言', 'Python编程', 'Python教程'],
  javascript: ['JavaScript语言', 'JS', 'ECMAScript'],
  typescript: ['TypeScript', 'TS', 'TypeScript教程'],
  react: ['React.js', 'React教程', 'React框架'],
  vue: ['Vue.js', 'Vue教程', 'Vue框架'],
  docker: ['Docker容器', 'Docker教程'],
  kubernetes: ['K8s', 'Kubernetes集群'],
  ai: ['AI', '人工智能', '机器学习'],
  ml: ['机器学习', '深度学习'],
  deeplearning: ['深度学习', '神经网络'],
  database: ['数据库', 'DB'],
  api: ['API设计', 'RESTful', '接口开发'],
  node: ['Node.js', 'NodeJS', 'Node后端'],
  css: ['CSS3', 'CSS布局', '样式表'],
  html: ['HTML5', '网页开发'],
  git: ['Git版本控制', 'Git操作'],
  linux: ['Linux命令', 'Linux系统'],
  windows: ['Windows系统', 'Win10'],
  mac: ['macOS', 'Mac电脑'],
  android: ['Android开发', '安卓'],
  ios: ['iOS开发', '苹果开发'],
}

export function expandQuery(query: string): string[] {
  const lower = query.toLowerCase()
  const queries = [query]

  for (const [keyword, synonyms] of Object.entries(SYNONYM_MAP)) {
    if (lower.includes(keyword) || lower === keyword) {
      for (const syn of synonyms) {
        const variant = query.replace(new RegExp(keyword, 'gi'), syn)
        if (variant !== query) queries.push(variant)
      }
      break
    }
  }

  return [...new Set(queries)]
}
