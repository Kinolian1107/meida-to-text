const BASE = "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type CorrectionInfo = {
  applied: boolean;
  model: string | null;
  models: { c1?: string | null; c2?: string | null; c3?: string | null };
  corrected_at?: string | null;
};

export type VideoStatus = {
  id: string;
  status: string;
  stage_label: string;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  caption_source: string;
  source_type: string;
  filename: string;
  can_retranscribe_locally: boolean;
  correction?: CorrectionInfo | null;
};

export type TagKind = "speaker" | "show" | "channel" | "topic";
export type TagSource = "ai" | "manual" | "channel";

export type TagItem = {
  id: string;
  name: string;
  kind: TagKind;
  source: TagSource;
  count?: number | null;
};

export type VideoListItem = {
  id: string;
  filename: string;
  media_type: string;
  source_type: string;
  status: string;
  duration_sec: number | null;
  upload_time: string | null;
  caption_source: string;
  progress: number;
  error_code: string | null;
  source_url: string | null;
  has_media: boolean;
  tags: TagItem[];
};

export type VideoListResponse = {
  items: VideoListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type VideoListParams = {
  status?: string;
  q?: string;
  search_mode?: "keyword" | "semantic";
  tagIds?: string[];
  page?: number;
  page_size?: number;
};

export type TimelineSegment = {
  id: string;
  video_id: string;
  start: number;
  end: number;
  type: "speech" | "frame";
  text: string;
  text_zh: string | null;
  frame_path: string | null;
  edited: boolean;
  speaker?: string | null;
};

export type SubtitleLang = "zh" | "en" | "both";

export type TranslationState = {
  status: "idle" | "running" | "done" | "failed";
  translated: number;
  total: number;
  model: string | null;
  error: string | null;
  updated_at: string | null;
};

export type DiffItem = {
  start: number;
  end: number;
  type: string;
  before: string;
  after: string;
};

export type SummaryItem = {
  id: string;
  video_id: string;
  prompt_template: string;
  content: string;
  created_at: string;
};

export type PromptTemplate = {
  id: string;
  name: string;
  content: string;
  builtin?: boolean;
};

export type YoutubeProbe = {
  title: string | null;
  duration_sec: number | null;
  caption_source: string;
  caption_lang: string | null;
  message: string;
  available_manual: string[];
  available_auto: string[];
};

export type AccountItem = {
  id: string;
  account_type: string;
  account_label: string;
  last_verified_at: string | null;
  status: string;
};

export type CrossPoint = { text: string; sources: string[] };
export type CrossAnalysisItem = {
  id: string;
  source_summary_ids: string[];
  user_prompt: string;
  result: { common_points: CrossPoint[]; conflicts: CrossPoint[] };
  created_at: string;
};

export type RelatedItem = {
  summary_id: string;
  video_id: string;
  filename: string;
  score: number;
  snippet: string;
};

export const api = {
  health: () => req<{ status: string; ytdlp_version: string | null }>("/health"),
  listVideos: (params: VideoListParams = {}) => {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.q) qs.set("q", params.q);
    if (params.search_mode) qs.set("search_mode", params.search_mode);
    if (params.tagIds?.length) qs.set("tags", params.tagIds.join(","));
    if (params.page) qs.set("page", String(params.page));
    if (params.page_size) qs.set("page_size", String(params.page_size));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return req<VideoListResponse>(`/api/videos${suffix}`);
  },
  listTags: () => req<TagItem[]>("/api/tags"),
  addVideoTag: (videoId: string, name: string, kind: TagKind) =>
    req<TagItem>(`/api/videos/${videoId}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, kind }),
    }),
  removeVideoTag: (videoId: string, tagId: string) =>
    req<{ ok: boolean }>(`/api/videos/${videoId}/tags/${tagId}`, { method: "DELETE" }),
  regenerateVideoTags: (videoId: string) =>
    req<TagItem[]>(`/api/videos/${videoId}/generate-tags`, { method: "POST" }),
  deleteVideo: (id: string) =>
    req<{ ok: boolean; id: string }>(`/api/videos/${id}`, { method: "DELETE" }),
  status: (id: string) => req<VideoStatus>(`/api/videos/${id}/status`),
  timeline: (id: string) =>
    req<{
      video_id: string;
      caption_source: string;
      can_retranscribe_locally: boolean;
      segments: TimelineSegment[];
      correction?: CorrectionInfo | null;
      translation?: TranslationState | null;
    }>(`/api/videos/${id}/timeline`),
  translationStatus: (id: string) =>
    req<TranslationState>(`/api/videos/${id}/translation`),
  translateSubtitles: (id: string) =>
    req<TranslationState>(`/api/videos/${id}/translate`, { method: "POST" }),
  subtitlesUrl: (id: string, lang: SubtitleLang, version = 0) =>
    `/api/videos/${id}/subtitles.vtt?lang=${lang}&v=${version}`,
  subtitlesSrtUrl: (id: string, lang: SubtitleLang, version = 0) =>
    `/api/videos/${id}/subtitles.srt?lang=${lang}&v=${version}`,
  diff: (id: string) =>
    req<{ video_id: string; items: DiffItem[] }>(`/api/videos/${id}/diff`),
  patchSegment: (videoId: string, segmentId: string, text: string) =>
    req<TimelineSegment>(`/api/videos/${videoId}/timeline/${segmentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  rebuildTimeline: (id: string) =>
    req<{ id: string }>(`/api/videos/${id}/timeline/rebuild`, { method: "POST" }),
  retranscribe: (id: string) =>
    req<{ id: string }>(`/api/videos/${id}/retranscribe`, { method: "POST" }),
  resume: (id: string) =>
    req<{ id: string }>(`/api/videos/${id}/resume`, { method: "POST" }),
  exportUrl: (id: string, format: "md" | "docx" | "pdf") =>
    `/api/videos/${id}/export?format=${format}`,
  summaries: (id: string) => req<SummaryItem[]>(`/api/videos/${id}/summaries`),
  summarize: (id: string, prompt_template: string, custom_prompt?: string) =>
    req<SummaryItem>(`/api/videos/${id}/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_template, custom_prompt }),
    }),
  prompts: () => req<PromptTemplate[]>("/api/prompts/summary"),
  savePrompt: (id: string, name: string, content: string) =>
    req<PromptTemplate>("/api/prompts/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, name, content }),
    }),
  deletePrompt: (id: string) =>
    req<{ ok: boolean }>(`/api/prompts/summary/${id}`, { method: "DELETE" }),
  probeYoutube: (url: string) =>
    req<YoutubeProbe>(`/api/youtube/probe?url=${encodeURIComponent(url)}`),
  fromYoutube: (url: string, topic: string, hotwords: string, account_id?: string) =>
    req<{ id: string }>(`/api/videos/from-youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, topic, hotwords, account_id: account_id || null }),
    }),
  fromUrl: (url: string, topic: string, hotwords: string) =>
    req<{ id: string }>(`/api/videos/from-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, topic, hotwords }),
    }),
  fromDrive: (url: string, topic: string, hotwords: string, account_id?: string) =>
    req<{ id: string }>(`/api/videos/from-google-drive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, topic, hotwords, account_id: account_id || null }),
    }),
  upload: async (file: File, topic: string, hotwords: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("topic", topic);
    fd.append("hotwords", hotwords);
    return req<{ id: string }>("/api/videos/upload", { method: "POST", body: fd });
  },
  uploadBatch: async (files: File[], topic: string, hotwords: string) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    fd.append("topic", topic);
    fd.append("hotwords", hotwords);
    return req<{ ids: string[] }>("/api/videos/upload-batch", { method: "POST", body: fd });
  },
  frameUrl: (videoId: string, framePath: string) => {
    const name = framePath.split("/").pop() || framePath;
    return `/api/videos/${videoId}/frames/${name}`;
  },
  mediaUrl: (videoId: string) => `/api/videos/${videoId}/media`,
  listAccounts: (account_type?: string) =>
    req<AccountItem[]>(
      `/api/accounts${account_type ? `?account_type=${account_type}` : ""}`,
    ),
  createYoutubeAccount: (account_label: string, cookies_text: string) =>
    req<AccountItem>("/api/accounts/youtube", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_label, cookies_text }),
    }),
  verifyAccount: (id: string) =>
    req<AccountItem>(`/api/accounts/${id}/verify`, { method: "POST" }),
  deleteAccount: (id: string) =>
    req<{ ok: boolean }>(`/api/accounts/${id}`, { method: "DELETE" }),
  googleAuthUrl: (label: string) =>
    req<{ url: string }>(`/api/accounts/google/auth-url?label=${encodeURIComponent(label)}`),
  listCross: () => req<CrossAnalysisItem[]>("/api/cross-analysis"),
  createCross: (summary_ids: string[], user_prompt: string) =>
    req<CrossAnalysisItem>("/api/cross-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ summary_ids, user_prompt }),
    }),
  getCross: (id: string) => req<CrossAnalysisItem>(`/api/cross-analysis/${id}`),
  related: (summaryId: string) =>
    req<RelatedItem[]>(`/api/cross-analysis/related/${summaryId}`),
  progressWsUrl: (videoId: string) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/videos/${videoId}/progress`;
  },
};
