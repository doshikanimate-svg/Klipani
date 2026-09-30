"use client";

import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { api, Analysis, Clip, Health, Highlight, Job, Publication, Video, YtStatus } from "../lib/api";

const formats = ["video/mp4", "video/quicktime", "video/webm", "video/x-matroska"];
const time = (value: number) => new Date(Math.max(0, value) * 1000).toISOString().slice(11, 19);
const toMinSec = (value: number) => {
  const total = Math.max(0, value);
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
};
const parseMinSec = (raw: string): number => {
  const text = raw.trim();
  if (/^\d+(\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(":").map(Number);
  if (parts.some((p) => !Number.isFinite(p) || p < 0)) return NaN;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return NaN;
};
const bytes = (value: number) => `${(value / 1024 / 1024).toFixed(1)} MB`;
const stepLabel = (step: string) => {
  if (step === "DOWNLOADING_MODEL") return "Скачивание модели Whisper (первый раз, может занять время)";
  if (step === "LOADING_MODEL") return "Загрузка модели Whisper";
  if (step === "TRANSCRIBING") return "Распознавание речи (Whisper)";
  if (step === "ANALYZING") return "Поиск лучших моментов";
  if (step === "RENDERING") return "Создание клипа";
  if (step === "PREPARING") return "Подготовка";
  if (step === "CANCELLED") return "Отменено";
  return step;
};

type Draft = { start: string; end: string };

export default function Home() {
  const [video, setVideo] = useState<Video | undefined>();
  const [job, setJob] = useState<Job | undefined>();
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | undefined>();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [clips, setClips] = useState<Clip[]>([]);
  const [withSubtitles, setWithSubtitles] = useState(true);
  const [exportStyle, setExportStyle] = useState("crop");
  const [yt, setYt] = useState<YtStatus | undefined>();
  const [tt, setTt] = useState<YtStatus | undefined>();
  const [pubProvider, setPubProvider] = useState<"youtube" | "tiktok">("youtube");
  const [pubOpen, setPubOpen] = useState<string>();
  const [pubTitle, setPubTitle] = useState("");
  const [pubPrivacy, setPubPrivacy] = useState("unlisted");
  const [stats, setStats] = useState<Record<string, { views: string; likes: string }>>({});
  const [clipSort, setClipSort] = useState<"new" | "views" | "likes" | "duration">("new");
  const [clipTab, setClipTab] = useState<"current" | "history">("current");
  const [history, setHistory] = useState<Clip[]>([]);
  const [pubs, setPubs] = useState<Record<string, Publication[]>>({});
  const [pubBusy, setPubBusy] = useState<string>();
  const [storage, setStorage] = useState<Record<string, number> | undefined>();
  const [wm, setWm] = useState<Record<string, string>>({});
  const [wmSaved, setWmSaved] = useState(false);
  const [health, setHealth] = useState<Health | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | undefined>();
  const input = useRef<HTMLInputElement>(null);
  const pollRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
    api.ytStatus().then(setYt).catch(() => undefined);
    api.storage().then(setStorage).catch(() => undefined);
    api.appSettings().then(setWm).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (video) refreshClips(video.id);
  }, [video]);

  const busy = !!job && (job.status === "QUEUED" || job.status === "RUNNING");

  const modeLabel = () => {
    if (!health) return "…";
    if (health.whisper && health.whisper_ready && health.llm) return `Whisper + Ollama (${health.whisper_model})`;
    if (health.whisper && health.whisper_ready) return `Whisper (${health.whisper_model}) + Mock LLM`;
    if (health.whisper && !health.whisper_ready) return "Mock (модель Whisper ещё не скачана)";
    return "Mock mode";
  };

  const choose = async (file?: File) => {
    if (!file) return;
    setError(undefined);
    if (!formats.includes(file.type) && !/\.(mp4|mkv|mov|webm)$/i.test(file.name)) {
      setError("Поддерживаются MP4, MKV, MOV и WEBM.");
      return;
    }
    try {
      setLoading(true);
      setVideo(await api.upload(file));
      setHighlights([]);
      setAnalysis(undefined);
      setDrafts({});
      setClips([]);
      setJob(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить файл.");
    } finally {
      setLoading(false);
    }
  };

  const poll = (id: string, done: () => void) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const updated = await api.job(id);
        setJob(updated);
        if (updated.status === "DONE" || updated.status === "FAILED") {
          if (pollRef.current) window.clearInterval(pollRef.current);
          if (updated.status === "DONE") done();
          else setError(updated.error || "Задача завершилась с ошибкой.");
        }
      } catch {
        if (pollRef.current) window.clearInterval(pollRef.current);
        setError("Потеряно соединение с сервером.");
      }
    }, 900);
  };

  const analyze = async () => {
    if (!video) return;
    try {
      setError(undefined);
      const next = await api.analyze(video.id);
      setJob(next);
      poll(next.id, async () => {
        const items = await api.highlights(video.id);
        setHighlights(items);
        setDrafts(Object.fromEntries(items.map((h) => [h.id, { start: toMinSec(h.start_time), end: toMinSec(h.end_time) }])));
        api.analysis(video.id).then(setAnalysis).catch(() => undefined);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось запустить анализ.");
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      setJob(await api.cancel(job.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отменить задачу.");
    }
  };

  const removeVideo = async () => {
    if (!video || busy) return;
    if (!window.confirm(`Удалить «${video.filename}» и все клипы/транскрипты? Файлы будут стёрты с диска.`)) return;
    try {
      setLoading(true);
      await api.deleteVideo(video.id);
      setVideo(undefined);
      setHighlights([]);
      setAnalysis(undefined);
      setDrafts({});
      setClips([]);
      setJob(undefined);
      api.storage().then(setStorage).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить видео.");
    } finally {
      setLoading(false);
    }
  };

  const clearCache = async () => {
    if (busy) return;
    const note = video
      ? `Удалить все загруженные видео, кроме текущего («${video.filename}»)? Текущая нарезка сохранится.`
      : "Удалить все загруженные видео, клипы и транскрипты?";
    if (!window.confirm(note)) return;
    try {
      setLoading(true);
      setError(undefined);
      await api.clearCache(video?.id);
      api.storage().then(setStorage).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось очистить кэш.");
    } finally {
      setLoading(false);
    }
  };

  const saveWm = async () => {
    try {
      setError(undefined);
      setWmSaved(false);
      setWm(await api.saveSettings(wm));
      setWmSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить настройки.");
    }
  };

  const saveBounds = async (highlight: Highlight) => {
    const draft = drafts[highlight.id];
    if (!draft) return;
    const start = parseMinSec(draft.start);
    const end = parseMinSec(draft.end);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      setError("Введите границы в секундах или формате м:сс (например 1:23).");
      return;
    }
    try {
      setSavingId(highlight.id);
      setError(undefined);
      const updated = await api.updateHighlight(highlight.id, start, end);
      setHighlights((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setDrafts((prev) => ({ ...prev, [updated.id]: { start: toMinSec(updated.start_time), end: toMinSec(updated.end_time) } }));
    } catch (e) {
      const message = e instanceof Error ? e.message : "Не удалось сохранить границы.";
      setError(message);
      throw e;
    } finally {
      setSavingId(undefined);
    }
  };

  const generate = async (highlight: Highlight) => {
    try {
      setError(undefined);
      await saveBounds(highlight);
      const next = await api.generate(highlight.id, withSubtitles, exportStyle);
      setJob(next);
      poll(next.id, async () => {
        if (!video) return;
        await refreshClips(video.id);
        await refreshHistory();
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать клип.");
    }
  };

  const montageVideo = async () => {
    if (!video) return;
    try {
      setError(undefined);
      const next = await api.montageVideo(video.id, withSubtitles, exportStyle);
      setJob(next);
      poll(next.id, async () => {
        if (!video) return;
        await refreshClips(video.id);
        await refreshHistory();
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось собрать монтаж.");
    }
  };

  const loadStats = async (clipId: string) => {
    try {
      const s = await api.clipStats(clipId);
      setStats((prev) => ({ ...prev, [clipId]: { views: String(s.views), likes: String(s.likes) } }));
    } catch {
      /* ignore */
    }
  };

  const loadedStatsRef = useRef<Set<string>>(new Set());
  const loadStatsOnce = (clipId: string) => {
    if (loadedStatsRef.current.has(clipId)) return;
    loadedStatsRef.current.add(clipId);
    loadStats(clipId);
  };

  const refreshClips = async (videoId: string) => {
    const items = await api.clips(videoId);
    setClips(items);
    items.forEach((c) => loadStatsOnce(c.id));
    api.storage().then(setStorage).catch(() => undefined);
  };

  const refreshHistory = async () => {
    try {
      const items = await api.allClips();
      setHistory(items);
      items.forEach((c) => loadStatsOnce(c.id));
    } catch {
      /* ignore */
    }
  };

  const removeClip = async (clip: Clip) => {
    if (!window.confirm("Удалить этот клип с диска?")) return;
    try {
      await api.deleteClip(clip.id);
      if (video) await refreshClips(video.id);
      await refreshHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить клип.");
    }
  };

  const visibleClips = clipTab === "current" ? clips : history;
  const sortedClips = [...visibleClips].sort((a, b) => {
    const num = (id: string, key: "views" | "likes") => Number(stats[id]?.[key] || 0);
    if (clipSort === "views") return num(b.id, "views") - num(a.id, "views");
    if (clipSort === "likes") return num(b.id, "likes") - num(a.id, "likes");
    if (clipSort === "duration") return b.end_time - b.start_time - (a.end_time - a.start_time);
    return 0;
  });

  const saveStats = async (clipId: string) => {
    const row = stats[clipId] || { views: "0", likes: "0" };
    try {
      const s = await api.saveStats(clipId, Math.max(0, Number(row.views) || 0), Math.max(0, Number(row.likes) || 0));
      setStats((prev) => ({ ...prev, [clipId]: { views: String(s.views), likes: String(s.likes) } }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить статистику.");
    }
  };

  const openPublish = async (clip: Clip) => {
    if (pubOpen === clip.id) {
      setPubOpen(undefined);
      return;
    }
    setPubOpen(clip.id);
    setPubTitle(`Клип ${time(clip.start_time)} — ${time(clip.end_time)} #Shorts`);
    api.publications(clip.id).then((items) => setPubs((prev) => ({ ...prev, [clip.id]: items }))).catch(() => undefined);
    api.ytStatus().then(setYt).catch(() => undefined);
    api.ttStatus().then(setTt).catch(() => undefined);
  };

  const connectProvider = async () => {
    try {
      const { url } = pubProvider === "youtube" ? await api.ytAuthUrl() : await api.ttAuthUrl();
      window.open(url, "_blank");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось начать авторизацию.");
    }
  };

  const publishClip = async (clip: Clip) => {
    if (!pubTitle.trim()) {
      setError("Введите название видео.");
      return;
    }
    try {
      setPubBusy(clip.id);
      setError(undefined);
      const desc = pubProvider === "youtube" ? "#Shorts" : "#fyp";
      const pub =
        pubProvider === "youtube"
          ? await api.publish(clip.id, pubTitle.trim(), desc, pubPrivacy)
          : await api.publishTiktok(clip.id, pubTitle.trim(), desc, pubPrivacy);
      setPubs((prev) => ({ ...prev, [clip.id]: [pub, ...(prev[clip.id] || [])] }));
      api.ytStatus().then(setYt).catch(() => undefined);
      api.ttStatus().then(setTt).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось опубликовать.");
    } finally {
      setPubBusy(undefined);
    }
  };

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-5 py-10 md:px-10">
      <header className="mb-12 flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-[.28em] text-violet-400">LOCAL VIDEO STUDIO</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">KLIPANI</h1>
        </div>
        <span className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-xs text-zinc-400">{modeLabel()}</span>
      </header>
      {storage !== undefined && storage.total > 0 && (
        <p className="mb-4 flex items-center justify-end gap-3 text-xs text-zinc-500">
          <span>Хранилище: {bytes(storage.total)}</span>
          <button
            onClick={clearCache}
            title="Удалить все видео, кроме текущего. Текущая нарезка не сбросится."
            className="rounded-lg border border-zinc-700 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800"
          >
            Очистить кэш
          </button>
        </p>
      )}

      <section className="rounded-3xl border border-zinc-800 bg-gradient-to-b from-zinc-900 to-zinc-950 p-8 shadow-2xl shadow-black/30">
        <h2 className="text-2xl font-semibold">Превращает стримы в TikTok-клипы</h2>
        <p className="mt-2 text-zinc-400">
          Загрузите запись — Whisper распознает речь, локальная модель или mock найдёт моменты, FFmpeg соберёт вертикальные ролики.
        </p>
        <div
          onDragOver={(e: DragEvent) => e.preventDefault()}
          onDrop={(e: DragEvent) => {
            e.preventDefault();
            choose(e.dataTransfer.files[0]);
          }}
          onClick={() => input.current?.click()}
          className="mt-7 cursor-pointer rounded-2xl border border-dashed border-violet-500/50 bg-violet-500/5 px-6 py-14 text-center hover:bg-violet-500/10"
        >
          <div className="text-lg font-medium">{loading ? "Загрузка и проверка видео…" : "Перетащите запись стрима сюда"}</div>
          <div className="mt-2 text-sm text-zinc-400">
            или <span className="text-violet-300">выберите файл</span> · MP4 / MKV / MOV / WEBM
          </div>
          <input ref={input} onChange={(e: ChangeEvent<HTMLInputElement>) => choose(e.target.files?.[0])} className="hidden" type="file" accept=".mp4,.mkv,.mov,.webm,video/*" />
        </div>

        {error && <p className="mt-5 rounded-xl border border-red-900/60 bg-red-950/40 p-3 text-sm text-red-300">{error}</p>}

        {video && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-zinc-800 bg-zinc-950/70 p-5">
            <div>
              <p className="font-medium">{video.filename}</p>
              <p className="mt-1 text-sm text-zinc-400">
                {bytes(video.size)} · {time(video.duration)} · {video.width}×{video.height} · {video.fps} fps
              </p>
            </div>
            <div className="flex gap-2">
              {busy && (
                <button onClick={cancel} className="rounded-xl border border-zinc-700 px-4 py-3 text-sm text-zinc-300 hover:bg-zinc-800">
                  Отменить
                </button>
              )}
              {!busy && (
                <button onClick={removeVideo} title="Удалить видео, клипы и транскрипты с диска" className="rounded-xl border border-red-900/60 px-4 py-3 text-sm text-red-300 hover:bg-red-950/40">
                  Удалить
                </button>
              )}
              <button disabled={busy} onClick={analyze} className="rounded-xl bg-violet-500 px-5 py-3 font-semibold text-white hover:bg-violet-400 disabled:opacity-50">
                Начать анализ
              </button>
            </div>
          </div>
        )}

        {job && (
          <div className="mt-5">
            <div className="mb-2 flex justify-between text-sm">
              <span>{stepLabel(job.current_step)}</span>
              <span>{job.progress}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-zinc-800">
              <div className="h-full bg-violet-500 transition-all" style={{ width: `${job.progress}%` }} />
            </div>
          </div>
        )}
      </section>

      {highlights.length > 0 && (
        <section className="mt-12">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">Найдено моментов: {highlights.length}</h2>
            <div className="flex items-center gap-3">
              <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={withSubtitles}
                  onChange={(e) => setWithSubtitles(e.target.checked)}
                  className="h-4 w-4 accent-violet-500"
                />
                Субтитры
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                Кадр
                <select
                  value={exportStyle}
                  onChange={(e) => setExportStyle(e.target.value)}
                  className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200"
                >
                  <option value="crop">Обрезка</option>
                  <option value="blur">Блюр-фон</option>
                </select>
              </label>
              <button
                disabled={busy}
                onClick={montageVideo}
                title="Склеить лучшие моменты всего видео в один клип"
                className="rounded-xl border border-amber-500/50 px-4 py-2 text-sm font-medium text-amber-200 hover:bg-amber-500/10 disabled:opacity-50"
              >
                Собрать монтаж
              </button>
            </div>
          </div>
          {clips.some((c) => c.kind === "MONTAGE") && (
            <p className="mt-2 text-xs text-amber-300/80">Монтаж уже собран — смотри в «Истории клипов» ниже.</p>
          )}
          {analysis &&
            (analysis.has_transcript && analysis.llm_provider === "ollama" ? (
              <p className="mt-2 inline-block rounded-full border border-emerald-800 bg-emerald-950/50 px-3 py-1 text-xs text-emerald-300">
                Whisper + Ollama · транскрипт: {analysis.segments} сегментов
              </p>
            ) : analysis.has_transcript ? (
              <p className="mt-2 inline-block rounded-full border border-amber-800 bg-amber-950/50 px-3 py-1 text-xs text-amber-300">
                Транскрипт есть ({analysis.segments} сегм.), но Ollama недоступна — моменты от mock-ранжирования
              </p>
            ) : (
              <p className="mt-2 inline-block rounded-full border border-amber-800 bg-amber-950/50 px-3 py-1 text-xs text-amber-300">
                Речь не распознана — моменты по длительности (mock), без анализа содержания
              </p>
            ))}
          <p className="mt-1 text-sm text-zinc-400">Подправьте границы в секундах и пересоберите клип.</p>
          <div className="mt-5 grid gap-4 md:grid-cols-3">
            {highlights.map((h) => (
              <article key={h.id} className="rounded-2xl border border-zinc-800 bg-zinc-900 p-5">
                <div className="flex justify-between">
                  <span className="rounded-full bg-violet-500/15 px-2 py-1 text-xs font-semibold text-violet-300">{h.category}</span>
                  <span className="text-sm text-amber-300">{h.score}/100</span>
                </div>
                <p className="mt-4 text-sm text-zinc-300">{h.reason}</p>
                {h.transcript_excerpt && <p className="mt-3 line-clamp-3 text-xs text-zinc-500">«{h.transcript_excerpt}»</p>}
                <div className="mt-4 grid grid-cols-2 gap-2">
                  <label className="text-xs text-zinc-500">
                    Старт (м:сс)
                    <input
                      className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-mono text-sm text-zinc-200"
                      value={drafts[h.id]?.start ?? toMinSec(h.start_time)}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [h.id]: { start: e.target.value, end: prev[h.id]?.end ?? toMinSec(h.end_time) } }))}
                    />
                  </label>
                  <label className="text-xs text-zinc-500">
                    Конец (м:сс)
                    <input
                      className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-mono text-sm text-zinc-200"
                      value={drafts[h.id]?.end ?? toMinSec(h.end_time)}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [h.id]: { start: prev[h.id]?.start ?? toMinSec(h.start_time), end: e.target.value } }))}
                    />
                  </label>
                </div>
                <p className="mt-2 font-mono text-xs text-zinc-500">
                  {time(parseMinSec(drafts[h.id]?.start ?? String(h.start_time)))} — {time(parseMinSec(drafts[h.id]?.end ?? String(h.end_time)))}
                </p>
                <div className="mt-4 flex gap-2">
                  <button
                    disabled={savingId === h.id || busy}
                    onClick={() => saveBounds(h)}
                    className="flex-1 rounded-xl border border-zinc-700 py-2 text-sm text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    Сохранить
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => generate(h)}
                    className="flex-1 rounded-xl border border-violet-500/50 py-2 text-sm font-medium text-violet-200 hover:bg-violet-500/10 disabled:opacity-50"
                  >
                    Создать клип
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {video && (
        <section className="mt-12 rounded-2xl border border-zinc-800 bg-zinc-900 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Водяной знак</h2>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={wm.watermark_enabled === "1" || wm.watermark_enabled === "true"}
                onChange={(e) => setWm((prev) => ({ ...prev, watermark_enabled: e.target.checked ? "1" : "0" }))}
                className="h-4 w-4 accent-violet-500"
              />
              Включить
            </label>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-4">
            <label className="text-xs text-zinc-500">
              Площадка
              <select
                value={wm.watermark_platform || "twitch"}
                onChange={(e) => setWm((prev) => ({ ...prev, watermark_platform: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              >
                <option value="twitch">Twitch</option>
                <option value="youtube">YouTube</option>
              </select>
            </label>
            <label className="text-xs text-zinc-500">
              Никнейм
              <input
                value={wm.watermark_text || ""}
                onChange={(e) => setWm((prev) => ({ ...prev, watermark_text: e.target.value }))}
                placeholder="nickname"
                maxLength={60}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-mono text-sm text-zinc-200"
              />
            </label>
            <label className="text-xs text-zinc-500">
              Позиция
              <select
                value={wm.watermark_position || "top-right"}
                onChange={(e) => setWm((prev) => ({ ...prev, watermark_position: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              >
                <option value="top-right">Сверху справа</option>
                <option value="top-left">Сверху слева</option>
                <option value="bottom-right">Снизу справа</option>
                <option value="bottom-left">Снизу слева</option>
              </select>
            </label>
            <div className="flex items-end">
              <button onClick={saveWm} className="w-full rounded-xl border border-zinc-700 py-2 text-sm text-zinc-300 hover:bg-zinc-800">
                {wmSaved ? "Сохранено ✓" : "Сохранить"}
              </button>
            </div>
          </div>
          <p className="mt-3 text-xs text-zinc-500">
            Логотип — по желанию: положите PNG в storage/watermarks/twitch.png или youtube.png, иначе будет только @ник.
          </p>
        </section>
      )}

      {clips.length > 0 && (
        <section className="mt-12 pb-12">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h2 className="mr-2 text-xl font-semibold">Клипы</h2>
              {(["current", "history"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => {
                    setClipTab(t);
                    if (t === "history") refreshHistory();
                  }}
                  className={`rounded-lg px-3 py-1.5 text-sm ${
                    clipTab === t ? "bg-zinc-700 text-white" : "bg-zinc-900 text-zinc-400 hover:bg-zinc-800"
                  }`}
                >
                  {t === "current" ? "Текущие" : "История"}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              Сортировка
              <select
                value={clipSort}
                onChange={(e) => setClipSort(e.target.value as typeof clipSort)}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200"
              >
                <option value="new">Сначала новые</option>
                <option value="views">По просмотрам</option>
                <option value="likes">По лайкам</option>
                <option value="duration">По длительности</option>
              </select>
            </label>
          </div>
          {clipTab === "history" && (
            <p className="mt-2 text-xs text-zinc-500">
              Все клипы. Внесите просмотры спустя несколько дней — категории с лучшими просмотрами получат бонус при следующем анализе.
            </p>
          )}
          <div className="mt-5 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {sortedClips.map((c) => (
              <article key={c.id} className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900">
                <video className="aspect-[9/16] w-full bg-black" controls preload="metadata" poster={api.thumbnailUrl(c.id)} src={api.videoUrl(c.id)} />
                <div className="p-4">
                  <p className="flex items-center justify-between text-sm text-zinc-400">
                    <span>
                      {time(c.start_time)} — {time(c.end_time)}
                      {c.kind === "MONTAGE" && (
                        <span className="ml-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-300">
                          монтаж
                        </span>
                      )}
                    </span>
                    <button
                      onClick={() => removeClip(c)}
                      title="Удалить клип с диска"
                      className="rounded-lg px-2 py-1 text-xs text-zinc-600 hover:bg-red-950/40 hover:text-red-300"
                    >
                      ✕
                    </button>
                  </p>
                  <div className="mt-3 flex gap-2">
                    <a className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-center text-sm hover:bg-zinc-700" href={api.videoUrl(c.id)} download>
                      Скачать MP4
                    </a>
                    <button
                      onClick={() => openPublish(c)}
                      className="flex-1 rounded-lg border border-red-500/40 px-3 py-2 text-sm text-red-200 hover:bg-red-500/10"
                    >
                      YouTube
                    </button>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-xs text-zinc-500">
                    <label className="flex flex-1 items-center gap-1">
                      👁
                      <input
                        value={stats[c.id]?.views ?? ""}
                        placeholder="0"
                        inputMode="numeric"
                        onChange={(e) => {
                          loadStatsOnce(c.id);
                          setStats((prev) => ({ ...prev, [c.id]: { views: e.target.value, likes: prev[c.id]?.likes ?? "0" } }));
                        }}
                        className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-300"
                      />
                    </label>
                    <label className="flex flex-1 items-center gap-1">
                      ♥
                      <input
                        value={stats[c.id]?.likes ?? ""}
                        placeholder="0"
                        inputMode="numeric"
                        onChange={(e) => {
                          loadStatsOnce(c.id);
                          setStats((prev) => ({ ...prev, [c.id]: { views: prev[c.id]?.views ?? "0", likes: e.target.value } }));
                        }}
                        className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-300"
                      />
                    </label>
                    <button
                      onClick={() => saveStats(c.id)}
                      className="rounded-lg border border-zinc-800 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800"
                    >
                      OK
                    </button>
                  </div>
                  {pubOpen === c.id && (
                    <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-950 p-3">
                      <div className="mb-3 flex gap-2">
                        {(["youtube", "tiktok"] as const).map((p) => (
                          <button
                            key={p}
                            disabled={p === "tiktok"}
                            title={p === "tiktok" ? "В разработке" : undefined}
                            onClick={() => setPubProvider(p)}
                            className={`flex-1 rounded-lg px-3 py-1.5 text-sm ${
                              pubProvider === p ? "bg-zinc-700 text-white" : "bg-zinc-900 text-zinc-400 hover:bg-zinc-800"
                            } disabled:cursor-not-allowed disabled:opacity-50`}
                          >
                            {p === "youtube" ? "YouTube" : "TikTok (скоро)"}
                          </button>
                        ))}
                      </div>
                      {(() => {
                        const st = pubProvider === "youtube" ? yt : tt;
                        const needKeys =
                          pubProvider === "youtube"
                            ? "Нужен OAuth-ключ: положите client_secrets в storage/yt_client.json (см. README)."
                            : "Нужны ключи: создайте приложение на developers.tiktok.com и задайте TIKTOK_CLIENT_KEY/SECRET в .env.";
                        if (!st?.connected) {
                          return (
                            <div className="text-sm">
                              <p className="text-zinc-400">
                                {st && !st.configured ? needKeys : `Подключите ${pubProvider === "youtube" ? "YouTube" : "TikTok"}-аккаунт для публикации.`}
                              </p>
                              <button
                                onClick={connectProvider}
                                disabled={st && !st.configured}
                                className="mt-2 w-full rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
                              >
                                Подключить {pubProvider === "youtube" ? "YouTube" : "TikTok"}
                              </button>
                              {pubProvider === "tiktok" && (
                                <p className="mt-2 text-xs text-zinc-500">
                                  Без аудита TikTok видео публикуется в приват — откройте его потом вручную в приложении.
                                </p>
                              )}
                            </div>
                          );
                        }
                        return (
                          <div>
                            {pubProvider === "youtube" && <p className="text-xs text-zinc-500">Канал: {yt?.channel || "подключён"}</p>}
                            <input
                              value={pubTitle}
                              onChange={(e) => setPubTitle(e.target.value)}
                              placeholder="Название видео"
                              maxLength={100}
                              className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-200"
                            />
                            <div className="mt-2 flex gap-2">
                              <select
                                value={pubPrivacy}
                                onChange={(e) => setPubPrivacy(e.target.value)}
                                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-200"
                              >
                                <option value="private">Приватное</option>
                                {pubProvider === "youtube" && <option value="unlisted">По ссылке</option>}
                                <option value="public">Публичное</option>
                              </select>
                              <button
                                disabled={pubBusy === c.id}
                                onClick={() => publishClip(c)}
                                className="flex-1 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
                              >
                                {pubBusy === c.id ? "Загрузка…" : "Опубликовать"}
                              </button>
                            </div>
                          </div>
                        );
                      })()}
                      {(pubs[c.id] || []).length > 0 && (
                        <div className="mt-2 space-y-1">
                          {(pubs[c.id] || []).map((p) => (
                            <a key={p.id} href={p.url} target="_blank" rel="noreferrer" className="block truncate text-xs text-emerald-300 hover:underline">
                              {p.url}
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
