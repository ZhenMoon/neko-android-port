export interface SearchResult {
  title: string
  url: string
  description: string
  engine: string
  score?: number
  publishedDate?: string
}

export interface SearchOptions {
  query: string
  maxResults?: number
  engines?: string[]
  timeout?: number
}

export interface SearchEngine {
  readonly name: string
  search(query: string, maxResults: number, signal?: AbortSignal): Promise<SearchResult[]>
}
