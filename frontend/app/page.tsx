"use client";

import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { api, Analysis, ApiError, Clip, Health, Highlight, Job, Publication, Video, YtStatus } from "../lib/api";

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
const etaLabel = (seconds?: number | null) => {
  if (seconds === undefined || seconds === null) return "—";
  if (seconds < 60) return `${seconds} сек`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
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
  const [tgBusy, setTgBusy] = useState<string>();
  const [tgSent, setTgSent] = useState<Record<string, boolean>>({});
  const [storage, setStorage] = useState<Record<string, number> | undefined>();
  const [wm, setWm] = useState<Record<string, string>>({});
  const [wmSaved, setWmSaved] = useState(false);
  const [license, setLicense] = useState<{
    active: boolean;
    plan?: string | null;
    exp?: number | null;
    free?: boolean;
    days_left?: number;
  }>({ active: false });
  const [licenseKey, setLicenseKey] = useState("");
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [health, setHealth] = useState<Health | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | undefined>();
  const input = useRef<HTMLInputElement>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const pollRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
    api.ytStatus().then(setYt).catch(() => undefined);
    api.storage().then(setStorage).catch(() => undefined);
    api.appSettings().then(setWm).catch(() => undefined);
    api.licenseStatus().then(setLicense).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (video) refreshClips(video.id);
  }, [video]);

  const busy = !!job && (job.status === "QUEUED" || job.status === "RUNNING");

  const step = !video ? 1 : highlights.length === 0 ? 2 : clips.length === 0 ? 3 : 4;
  const steps = ["Видео", "Моменты", "Клипы"];

  /** Server said the license is missing/expired: refresh state and show the profile. */
  const handleError = (e: unknown, fallback: string): boolean => {
    if (e instanceof ApiError && e.isLicenseRequired) {
      setLicense({ active: false });
      setError("Подписка нужна для этой операции — продлите её в профиле.");
      setProfileOpen(true);
      return true;
    }
    setError(e instanceof Error ? e.message : fallback);
    return false;
  };

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
      handleError(e, "Не удалось загрузить файл.");
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
      handleError(e, "Не удалось запустить анализ.");
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

  const activateLicense = async () => {
    if (!licenseKey.trim()) {
      setError("Вставьте лицензионный ключ из Telegram-бота.");
      return;
    }
    try {
      setLicenseBusy(true);
      setError(undefined);
      setLicense(await api.licenseActivate(licenseKey.trim()));
      setLicenseKey("");
      api.storage().then(setStorage).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось активировать ключ.");
    } finally {
      setLicenseBusy(false);
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
      handleError(e, "Не удалось создать клип.");
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
      handleError(e, "Не удалось собрать монтаж.");
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

  const sendToTelegram = async (clip: Clip) => {
    try {
      setTgBusy(clip.id);
      setError(undefined);
      await api.sendTelegram(clip.id);
      setTgSent((prev) => ({ ...prev, [clip.id]: true }));
    } catch (e) {
      handleError(e, "Не удалось отправить в Telegram.");
    } finally {
      setTgBusy(undefined);
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
      handleError(e, "Не удалось опубликовать.");
    } finally {
      setPubBusy(undefined);
    }
  };

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-5 py-8 md:px-10">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.png" alt="KLIPANI" className="h-11 w-11 rounded-2xl shadow-lg shadow-brand-pink/20" />
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">
              KLIPANI <span className="font-light text-zinc-500">studio</span>
            </h1>
            <p className="text-xs text-zinc-500">стримы → вертикальные клипы</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {storage !== undefined && storage.total > 0 && (
            <span className="flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-xs text-zinc-400">
              {bytes(storage.total)}
              <button
                onClick={clearCache}
                title="Удалить все видео, кроме текущего. Текущая нарезка не сбросится."
                className="text-zinc-500 hover:text-zinc-200"
              >
                Очистить кэш
              </button>
            </span>
          )}
          <span className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-xs text-zinc-400">{modeLabel()}</span>
          <button
            onClick={() => setProfileOpen(true)}
            title="Профиль: подписка и привязанные аккаунты"
            className="relative flex h-9 w-9 items-center justify-center rounded-full border border-zinc-700 bg-zinc-900 text-base hover:border-zinc-500"
          >
            👤
            <span
              className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-zinc-950 ${
                license.active ? "bg-brand-cyan" : "bg-zinc-600"
              }`}
            />
          </button>
        </div>
      </header>

      <nav className="mb-8 flex items-center gap-2" aria-label="Шаги">
        {steps.map((label, i) => {
          const n = i + 1;
          const done = step > n || (n === 3 && clips.length > 0);
          const active = step === n;
          return (
            <div key={label} className="flex flex-1 items-center gap-2 last:flex-none">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  done ? "bg-brand-cyan text-black" : active ? "border-2 border-brand-cyan text-brand-cyan" : "border border-zinc-700 text-zinc-600"
                }`}
              >
                {done ? "✓" : n}
              </span>
              <span className={`text-sm ${active || done ? "text-zinc-200" : "text-zinc-600"}`}>{label}</span>
              {i < steps.length - 1 && <span className="mx-1 h-px flex-1 bg-zinc-800" />}
            </div>
          );
        })}
      </nav>

      <section className="anim-rise rounded-3xl border border-zinc-800/80 bg-brand-panel p-6 shadow-2xl shadow-black/40 md:p-8">
        <div
          onDragOver={(e: DragEvent) => e.preventDefault()}
          onDrop={(e: DragEvent) => {
            e.preventDefault();
            choose(e.dataTransfer.files[0]);
          }}
          onClick={() => input.current?.click()}
          className="cursor-pointer rounded-2xl border-2 border-dashed border-zinc-700 px-6 py-12 text-center transition hover:border-brand-cyan/60 hover:bg-brand-cyan/5"
        >
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-cyan/10 text-2xl">⬆</div>
          <div className="text-lg font-semibold">{loading ? "Загрузка и проверка видео…" : "Перетащите запись стрима сюда"}</div>
          <div className="mt-2 text-sm text-zinc-400">
            или <span className="font-medium text-brand-cyan">выберите файл</span>
          </div>
          <div className="mt-4 flex justify-center gap-2">
            {["MP4", "MKV", "MOV", "WEBM"].map((f) => (
              <span key={f} className="rounded-md bg-zinc-800 px-2 py-0.5 font-mono text-[11px] text-zinc-400">
                {f}
              </span>
            ))}
          </div>
          <input ref={input} onChange={(e: ChangeEvent<HTMLInputElement>) => choose(e.target.files?.[0])} className="hidden" type="file" accept=".mp4,.mkv,.mov,.webm,video/*" />
        </div>

        {error && <p className="mt-5 rounded-xl border border-brand-pink/40 bg-brand-pink/10 p-3 text-sm text-red-200">{error}</p>}

        {video && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl bg-black/40 p-5 ring-1 ring-zinc-800">
            <div className="min-w-0">
              <p className="truncate font-medium">{video.filename}</p>
              <p className="mt-1 font-mono text-xs text-zinc-400">
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
                <button onClick={removeVideo} title="Удалить видео, клипы и транскрипты с диска" className="rounded-xl border border-zinc-800 px-4 py-3 text-sm text-zinc-500 hover:border-red-900/60 hover:text-red-300">
                  Удалить
                </button>
              )}
              <button disabled={busy} onClick={analyze} className="btn-glow rounded-xl bg-brand-cyan px-6 py-3 font-bold text-black hover:brightness-110 disabled:opacity-50">
                {highlights.length > 0 ? "Анализ заново" : "Найти моменты"}
              </button>
            </div>
          </div>
        )}

        {job && (
          <div className="mt-5">
            <div className="mb-2 flex justify-between text-sm">
              <span className="text-zinc-300">{stepLabel(job.current_step)}</span>
              <span className="font-mono text-zinc-400">{job.progress}%</span>
            </div>
            <div className="progress-shimmer h-2 overflow-hidden rounded-full bg-zinc-800">
              <div className="h-full rounded-full bg-gradient-to-r from-brand-cyan to-brand-pink transition-all" style={{ width: `${job.progress}%` }} />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs">
              <span className="text-zinc-500">
                {job.status === "RUNNING" || job.status === "QUEUED"
                  ? `Осталось ${etaLabel(job.eta_seconds)}`
                  : job.status === "DONE"
                    ? "Готово"
                    : job.status === "FAILED"
                      ? "Ошибка"
                      : "Ожидание"}
              </span>
              <span className="text-zinc-600">
                {job.type === "ANALYZE" ? "Анализ: распознавание речи и подбор моментов" : job.type === "MONTAGE" ? "Сборка монтажа" : "Рендер клипа"}
              </span>
            </div>
          </div>
        )}
      </section>

      {highlights.length > 0 && (
        <section className="mt-12">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-bold tracking-widest text-zinc-500">ШАГ 2 · МОМЕНТЫ</p>
              <h2 className="mt-1 text-xl font-bold">Что нарезать <span className="font-normal text-zinc-500">· {highlights.length}</span></h2>
            </div>
            <div className="flex items-center gap-3">
              <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-zinc-800 bg-brand-panel px-3 py-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={withSubtitles}
                  onChange={(e) => setWithSubtitles(e.target.checked)}
                  className="h-4 w-4 accent-[#00F2EA]"
                />
                Субтитры
              </label>
              <label className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-brand-panel px-3 py-2 text-sm text-zinc-300">
                Кадр
                <select
                  value={exportStyle}
                  onChange={(e) => setExportStyle(e.target.value)}
                  className="bg-transparent text-sm text-zinc-200"
                >
                  <option value="crop">Обрезка</option>
                  <option value="blur">Блюр-фон</option>
                </select>
              </label>
              <button
                disabled={busy}
                onClick={montageVideo}
                title="Склеить лучшие моменты всего видео в один клип"
                className="btn-glow-pink rounded-xl bg-brand-pink px-4 py-2 text-sm font-bold text-white hover:brightness-110 disabled:opacity-50"
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
              <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-brand-cyan/10 px-3 py-1 text-xs text-brand-cyan">
                <span className="dot-live h-1.5 w-1.5 rounded-full bg-brand-cyan" /> Whisper + Ollama · {analysis.segments} сегментов речи
              </p>
            ) : analysis.has_transcript ? (
              <p className="mt-3 inline-block rounded-full bg-amber-500/10 px-3 py-1 text-xs text-amber-300">
                Транскрипт есть ({analysis.segments} сегм.), но Ollama недоступна — оценка по тексту
              </p>
            ) : (
              <p className="mt-3 inline-block rounded-full bg-amber-500/10 px-3 py-1 text-xs text-amber-300">
                Речь не распознана — моменты расставлены по длительности
              </p>
            ))}
          <div className="mt-5 grid gap-4 md:grid-cols-3">
            {highlights.map((h, i) => (
              <article key={h.id} style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }} className="card-lift anim-rise flex flex-col rounded-2xl border border-zinc-800/80 bg-brand-panel p-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-zinc-800 text-xs font-bold text-zinc-300">#{i + 1}</span>
                    <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-300">{h.category}</span>
                  </div>
                  <span className="font-mono text-sm font-bold tabular-nums text-brand-cyan">{h.score}</span>
                </div>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-zinc-800">
                  <div className="h-full rounded-full bg-brand-cyan/70" style={{ width: `${h.score}%` }} />
                </div>
                <p className="mt-3 flex-1 text-sm leading-snug text-zinc-300">{h.reason}</p>
                {h.transcript_excerpt && <p className="mt-2 line-clamp-2 text-xs italic text-zinc-500">«{h.transcript_excerpt}»</p>}
                <div className="mt-4 grid grid-cols-2 gap-2">
                  <label className="text-[11px] uppercase tracking-wide text-zinc-500">
                    Старт · м:сс
                    <input
                      className="mt-1 w-full rounded-lg border border-zinc-800 bg-black/40 px-2 py-1.5 font-mono text-sm tabular-nums text-zinc-200"
                      value={drafts[h.id]?.start ?? toMinSec(h.start_time)}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [h.id]: { start: e.target.value, end: prev[h.id]?.end ?? toMinSec(h.end_time) } }))}
                    />
                  </label>
                  <label className="text-[11px] uppercase tracking-wide text-zinc-500">
                    Конец · м:сс
                    <input
                      className="mt-1 w-full rounded-lg border border-zinc-800 bg-black/40 px-2 py-1.5 font-mono text-sm tabular-nums text-zinc-200"
                      value={drafts[h.id]?.end ?? toMinSec(h.end_time)}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [h.id]: { start: prev[h.id]?.start ?? toMinSec(h.start_time), end: e.target.value } }))}
                    />
                  </label>
                </div>
                <p className="mt-2 font-mono text-[11px] tabular-nums text-zinc-600">
                  {time(parseMinSec(drafts[h.id]?.start ?? String(h.start_time)))} — {time(parseMinSec(drafts[h.id]?.end ?? String(h.end_time)))}
                </p>
                <div className="mt-3 flex gap-2">
                  <button
                    disabled={savingId === h.id || busy}
                    onClick={() => saveBounds(h)}
                    title="Сохранить границы"
                    className="rounded-xl border border-zinc-700 px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    {savingId === h.id ? "…" : "OK"}
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => generate(h)}
                    className="flex-1 rounded-xl bg-zinc-100 py-2 text-sm font-bold text-black hover:bg-white disabled:opacity-50"
                  >
                    В клип →
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {video && (
        <details className="mt-10 rounded-2xl border border-zinc-800/80 bg-brand-panel">
          <summary className="flex cursor-pointer list-none items-center justify-between p-5">
            <span>
              <span className="text-xs font-bold tracking-widest text-zinc-500">НАСТРОЙКА</span>
              <span className="ml-3 text-base font-bold">Водяной знак {wm.watermark_enabled === "1" || wm.watermark_enabled === "true" ? <span className="text-brand-cyan">· вкл</span> : <span className="text-zinc-600">· выкл</span>}</span>
            </span>
            <span className="text-zinc-500">▾</span>
          </summary>
          <div className="px-5 pb-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={wm.watermark_enabled === "1" || wm.watermark_enabled === "true"}
                onChange={(e) => setWm((prev) => ({ ...prev, watermark_enabled: e.target.checked ? "1" : "0" }))}
                className="h-4 w-4 accent-[#00F2EA]"
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
          </div>
        </details>
      )}

      {profileOpen && (
        <div className="fixed inset-0 z-50" role="dialog" aria-label="Профиль">
          <div className="absolute inset-0 bg-black/60" onClick={() => setProfileOpen(false)} />
          <aside className="drawer-in absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-zinc-800 bg-brand-panel">
            <div className="flex items-center justify-between border-b border-zinc-800/80 p-5">
              <span>
                <span className="text-xs font-bold tracking-widest text-zinc-500">ПРОФИЛЬ</span>
                <span className="ml-3 text-base font-bold">
                  {license.active ? (
                    <span className="text-brand-cyan">{license.plan === "trial" ? "пробная" : license.plan || "активна"}</span>
                  ) : (
                    <span className="text-brand-pink">нужна подписка</span>
                  )}
                </span>
              </span>
              <button
                onClick={() => setProfileOpen(false)}
                title="Закрыть"
                className="icon-btn rounded-lg px-2 py-1 text-lg text-zinc-400 hover:text-zinc-100"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5">
          {license.active && license.exp ? (
            <>
              <p className="text-sm text-zinc-400">
                {license.plan === "trial" ? (
                  <>
                    Пробная подписка до {new Date(license.exp * 1000).toLocaleDateString("ru-RU")}. На каждом клипе есть
                    небольшая отметка @Klipani_bot — она исчезнет на оплаченном тарифе.{" "}
                  </>
                ) : (
                  <>
                    Подписка активна до {new Date(license.exp * 1000).toLocaleDateString("ru-RU")}.{" "}
                  </>
                )}
                <a href="https://t.me/Klipani_bot" target="_blank" rel="noreferrer" className="text-brand-cyan hover:underline">
                  Продлить в боте →
                </a>
              </p>
              {license.plan === "trial" && license.days_left !== undefined && (
                <p className="mt-1 text-xs text-zinc-500">
                  Осталось {license.days_left} дн. Оплатите тариф, и watermark исчезнет.
                </p>
              )}
            </>
          ) : (
            <div className="rounded-xl border border-brand-pink/40 bg-brand-pink/10 p-4">
              <p className="text-sm font-bold text-brand-pink">Нужна подписка</p>
              <p className="mt-1 text-sm text-zinc-300">
                Без ключа приложение не обрабатывает видео. Купите подписку в Telegram-боте{" "}
                <a href="https://t.me/Klipani_bot" target="_blank" rel="noreferrer" className="text-brand-cyan hover:underline">
                  @Klipani_bot
                </a>{" "}
                и вставьте ключ сюда.
              </p>
            </div>
          )}
          <div className="mt-3 flex gap-2">
            <input
              value={licenseKey}
              onChange={(e) => setLicenseKey(e.target.value)}
              placeholder="KLIP-..."
              className="flex-1 rounded-xl border border-zinc-800 bg-black/40 px-3 py-2 font-mono text-sm text-zinc-200"
            />
            <button
              disabled={licenseBusy}
              onClick={activateLicense}
              className="rounded-xl bg-zinc-100 px-4 py-2 text-sm font-bold text-black hover:bg-white disabled:opacity-50"
            >
              {licenseBusy ? "…" : "Активировать"}
            </button>
          </div>
          <div className="mt-5 border-t border-zinc-800/80 pt-4">
            <p className="text-xs font-bold tracking-widest text-zinc-500">ПРИВЯЗАННЫЕ АККАУНТЫ</p>
            <div className="mt-3 space-y-3">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="text-zinc-300">
                  ▶️ YouTube{" "}
                  <span className={yt?.connected ? "text-brand-cyan" : "text-zinc-600"}>
                    {yt?.connected ? `· ${yt.channel || "подключён"}` : "· не привязан"}
                  </span>
                </span>
                {!yt?.connected && (
                  <span className="text-xs text-zinc-500">Привязка — на карточке клипа, кнопка публикации</span>
                )}
              </div>
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="text-zinc-300">
                  🎵 TikTok{" "}
                  <span className={tt?.connected ? "text-brand-cyan" : "text-zinc-600"}>
                    {tt?.connected ? "· подключён" : "· не привязан (скоро)"}
                  </span>
                </span>
              </div>
              <div className="text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-zinc-300">
                    ✈️ Telegram{" "}
                    <span className={wm.tg_chat_id ? "text-brand-cyan" : "text-zinc-600"}>
                      {wm.tg_chat_id ? `· ${wm.tg_chat_id}` : "· не привязан"}
                    </span>
                  </span>
                </div>
                <p className="mt-1 text-xs text-zinc-500">
                  Узнайте Chat ID командой /myid в боте{" "}
                  <a href="https://t.me/Klipani_bot" target="_blank" rel="noreferrer" className="text-brand-cyan hover:underline">
                    @Klipani_bot
                  </a>{" "}
                  и впишите сюда — кнопка ✈️ на клипе отправит видео одной кнопкой.
                </p>
                <div className="mt-2 flex gap-2">
                  <input
                    value={wm.tg_chat_id || ""}
                    onChange={(e) => setWm((prev) => ({ ...prev, tg_chat_id: e.target.value }))}
                    placeholder="123456789"
                    inputMode="numeric"
                    className="flex-1 rounded-xl border border-zinc-800 bg-black/40 px-3 py-2 font-mono text-sm text-zinc-200"
                  />
                  <button
                    onClick={saveWm}
                    className="rounded-xl border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
                  >
                    Сохранить
                  </button>
                </div>
              </div>
            </div>
          </div>
            </div>
          </aside>
        </div>
      )}

      {clips.length > 0 && (
        <section className="mt-10 pb-12">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-bold tracking-widest text-zinc-500">ШАГ 3 · РЕЗУЛЬТАТ</p>
              <div className="mt-1 flex items-center gap-1 rounded-xl border border-zinc-800 bg-brand-panel p-1">
                {(["current", "history"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => {
                      setClipTab(t);
                      if (t === "history") refreshHistory();
                    }}
                    className={`rounded-lg px-4 py-1.5 text-sm font-medium ${
                      clipTab === t ? "bg-zinc-100 text-black" : "text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {t === "current" ? "Текущие" : "История"}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-zinc-400">
              Сортировка
              <select
                value={clipSort}
                onChange={(e) => setClipSort(e.target.value as typeof clipSort)}
                className="rounded-lg border border-zinc-800 bg-brand-panel px-2 py-1.5 text-sm text-zinc-200"
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
            {sortedClips.map((c, i) => (
              <article key={c.id} style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }} className="card-lift anim-rise group overflow-hidden rounded-2xl border border-zinc-800/80 bg-brand-panel">
                <div className="relative">
                  <video className="aspect-[9/16] w-full bg-black" controls preload="metadata" poster={api.thumbnailUrl(c.id)} src={api.videoUrl(c.id)} />
                  {c.kind === "MONTAGE" && (
                    <span className="absolute left-3 top-3 rounded-full bg-brand-pink px-2.5 py-0.5 text-[11px] font-bold text-white">
                      МОНТАЖ
                    </span>
                  )}
                  <button
                    onClick={() => removeClip(c)}
                    title="Удалить клип с диска"
                    className="icon-btn absolute right-3 top-3 rounded-full bg-black/60 px-2 py-0.5 text-xs text-zinc-400 opacity-0 group-hover:opacity-100 hover:text-red-300"
                  >
                    ✕
                  </button>
                </div>
                <div className="p-4">
                  <p className="font-mono text-xs tabular-nums text-zinc-400">
                    {time(c.start_time)} — {time(c.end_time)}
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <a className="rounded-xl bg-zinc-100 px-3 py-2 text-center text-sm font-bold text-black hover:bg-white" href={api.videoUrl(c.id)} download>
                      Скачать
                    </a>
                    <button
                      onClick={() => openPublish(c)}
                      className="rounded-xl bg-brand-pink/15 px-3 py-2 text-sm font-bold text-brand-pink hover:bg-brand-pink/25"
                    >
                      Опубликовать
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
                    <div className="anim-pop mt-3 rounded-xl border border-zinc-800 bg-zinc-950 p-3">
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
                        <button
                          onClick={() => sendToTelegram(c)}
                          disabled={tgBusy === c.id}
                          title="Отправить MP4 в Telegram-бота — забрать с телефона"
                          className="icon-btn flex aspect-square items-center justify-center rounded-lg bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                        >
                          {tgBusy === c.id ? (
                            "…"
                          ) : tgSent[c.id] ? (
                            "✓"
                          ) : (
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                              <path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z" />
                            </svg>
                          )}
                        </button>
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
                                className="btn-glow-pink mt-2 w-full rounded-lg bg-brand-pink px-3 py-2 text-sm font-bold text-white hover:brightness-110 disabled:opacity-50"
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
                                className="btn-glow-pink flex-1 rounded-lg bg-brand-pink px-3 py-1.5 text-sm font-bold text-white hover:brightness-110 disabled:opacity-50"
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
