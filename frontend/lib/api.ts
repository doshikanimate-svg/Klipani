const BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export type Video = {
  id: string;
  filename: string;
  size: number;
  duration: number;
  width: number;
  height: number;
  fps: number;
};
export type Job = {
  id: string;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
  progress: number;
  current_step: string;
  error?: string;
};
export type Highlight = {
  id: string;
  start_time: number;
  end_time: number;
  score: number;
  category: string;
  reason: string;
  transcript_excerpt?: string;
};
export type Clip = { id: string; start_time: number; end_time: number; status: string; kind?: string };
export type YtStatus = { provider: string; configured: boolean; connected: boolean; channel?: string };
export type Publication = { id: string; clip_id: string; provider: string; external_id: string; url: string; created_at: string };
export type Health = {
  status: string;
  ffmpeg: boolean;
  ffprobe: boolean;
  whisper: boolean;
  whisper_ready?: boolean;
  llm: boolean;
  llm_provider: string;
  whisper_model: string;
};
export type Transcript = {
  language: string;
  segments: { start: number; end: number; text: string }[];
};
export type Analysis = {
  video_id: string;
  has_transcript: number;
  segments: number;
  llm_provider: string;
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "Ошибка сервера");
  }
  return res.json();
}

export const api = {
  health: () => request<Health>("/api/health"),
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<Video>("/api/videos/upload", { method: "POST", body: data });
  },
  analyze: (id: string) => request<Job>(`/api/videos/${id}/analyze`, { method: "POST" }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  highlights: (id: string) => request<Highlight[]>(`/api/videos/${id}/highlights`),
  transcript: (id: string) => request<Transcript>(`/api/videos/${id}/transcript`),
  analysis: (id: string) => request<Analysis>(`/api/videos/${id}/analysis`),
  updateHighlight: (id: string, start_time: number, end_time: number) =>
    request<Highlight>(`/api/highlights/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_time, end_time }),
    }),
  generate: (id: string, subtitles = true, style = "crop") =>
    request<Job>(`/api/highlights/${id}/generate?subtitles=${subtitles}&style=${style}`, { method: "POST" }),
  montageVideo: (videoId: string, subtitles = true, style = "crop") =>
    request<Job>(`/api/videos/${videoId}/montage?subtitles=${subtitles}&style=${style}`, { method: "POST" }),
  clips: (id: string) => request<Clip[]>(`/api/clips?video_id=${id}`),
  allClips: () => request<Clip[]>("/api/clips"),
  ytStatus: () => request<YtStatus>("/api/publish/youtube/status"),
  ytAuthUrl: () => request<{ url: string }>("/api/publish/youtube/auth-url"),
  ttStatus: () => request<YtStatus>("/api/publish/tiktok/status"),
  ttAuthUrl: () => request<{ url: string }>("/api/publish/tiktok/auth-url"),
  publications: (clipId: string) => request<Publication[]>(`/api/clips/${clipId}/publications`),
  publish: (clipId: string, title: string, description: string, privacy: string) =>
    request<Publication>(`/api/clips/${clipId}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description, privacy }),
    }),
  publishTiktok: (clipId: string, title: string, description: string, privacy: string) =>
    request<Publication>(`/api/clips/${clipId}/publish-tiktok`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description, privacy }),
    }),
  sendTelegram: (clipId: string) =>
    request<{ sent_to: number[] }>(`/api/clips/${clipId}/send-telegram`, { method: "POST" }),
  clipStats: (clipId: string) => request<{ clip_id: string; views: number; likes: number }>(`/api/clips/${clipId}/stats`),
  deleteClip: (clipId: string) => request<{ deleted: boolean; removed_files: number }>(`/api/clips/${clipId}`, { method: "DELETE" }),
  saveStats: (clipId: string, views: number, likes: number) =>
    request<{ clip_id: string; views: number; likes: number }>(`/api/clips/${clipId}/stats`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ views, likes }),
    }),
  videoUrl: (id: string) => `${BASE}/api/clips/${id}/video`,
  thumbnailUrl: (id: string) => `${BASE}/api/clips/${id}/thumbnail`,
  storage: () => request<Record<string, number>>("/api/storage"),
  deleteVideo: (id: string) => request<{ deleted: boolean; removed_files: number }>(`/api/videos/${id}`, { method: "DELETE" }),
  licenseStatus: () => request<{ active: boolean; plan?: string; exp?: number } | { active: boolean }>("/api/license"),
  licenseActivate: (key: string) =>
    request<{ active: boolean; plan: string; exp: number }>(`/api/license`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }),
  clearCache: (keepVideoId?: string) =>
    request<{ deleted_videos: number; removed_files: number; freed_bytes: number }>(
      `/api/storage/cache${keepVideoId ? `?keep_video_id=${keepVideoId}` : ""}`,
      { method: "DELETE" },
    ),
  appSettings: () => request<Record<string, string>>("/api/settings"),
  saveSettings: (values: Record<string, string>) =>
    request<Record<string, string>>("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
};
