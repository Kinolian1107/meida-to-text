import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SubtitleLang, TimelineSegment } from "../api";

/**
 * Player shell with its own controls.
 *
 * The native `<video controls>` UI cannot do what this view needs: its control
 * bar paints a fixed gradient scrim that covers the picture, and cue placement
 * is left to the user agent, which parks subtitles well above the bottom edge.
 * Owning the chrome makes both exact — a flat translucent bar, and subtitles
 * pinned to the bottom (dropping to the very edge whenever the bar is away).
 * Right-click still reaches Chrome's own menu for picture-in-picture etc.
 *
 * The bar is shown purely by hovering the strip it lives in, handled in CSS.
 * No timers, so it never lingers after a pause and never pops up for a
 * keyboard seek — it is on screen exactly while the pointer asks for it.
 */

export type SubtitleMode = SubtitleLang | "off";

/** ←/→ alone, with Shift, and with Ctrl/⌘. */
const SEEK_STEPS = { plain: 1, shift: 5, ctrl: 30 } as const;
const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 1.75, 2];

type Cue = { start: number; end: number; zh: string; orig: string };
type CueLine = { text: string; dim: boolean };

type Props = {
  src: string;
  segments: TimelineSegment[];
  subtitleMode: SubtitleMode;
  /** Multiplier on the responsive base subtitle size. */
  subtitleScale: number;
  onMediaElement: (el: HTMLVideoElement | null) => void;
};

function buildCues(segments: TimelineSegment[]): Cue[] {
  return segments
    .filter((s) => s.type === "speech")
    .map((s) => ({
      start: s.start,
      end: s.end,
      zh: (s.text_zh ?? "").trim(),
      orig: s.text.trim(),
    }))
    .sort((a, b) => a.start - b.start);
}

/** Last cue that has started, if it has not ended yet. */
function findCue(cues: Cue[], t: number): Cue | null {
  let lo = 0;
  let hi = cues.length - 1;
  let started: Cue | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) {
      started = cues[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return started && t < started.end ? started : null;
}

function cueLines(cue: Cue | null, mode: SubtitleMode): CueLine[] {
  if (!cue || mode === "off") return [];
  if (mode === "en") return cue.orig ? [{ text: cue.orig, dim: false }] : [];
  if (mode === "zh") {
    const text = cue.zh || cue.orig;
    return text ? [{ text, dim: false }] : [];
  }
  const lines: CueLine[] = [];
  if (cue.zh) lines.push({ text: cue.zh, dim: false });
  // Untranslated lines still read as the primary line rather than a footnote.
  if (cue.orig) lines.push({ text: cue.orig, dim: cue.zh !== "" });
  return lines;
}

function fmtClock(t: number): string {
  if (!Number.isFinite(t) || t < 0) return "0:00";
  const total = Math.floor(t);
  const h = Math.floor(total / 3600);
  const m = Math.floor(total / 60) % 60;
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={path} />
    </svg>
  );
}

// All paths are closed shapes so a single `fill: currentColor` renders them;
// stroke-based icons would need per-icon fill overrides.
const ICONS = {
  play: "M8 5v14l11-7z",
  pause: "M6 5h4v14H6zm8 0h4v14h-4z",
  volume:
    "M3 10v4h4l5 4V6L7 10H3zm11.5 2c0-1.6-.9-3-2.3-3.6v7.2c1.4-.6 2.3-2 2.3-3.6z" +
    "M12 3.2v2.1c2.9.7 5 3.3 5 6.7s-2.1 6-5 6.7v2.1c4-.8 7-4.3 7-8.8s-3-8-7-8.8z",
  muted:
    "M3 10v4h4l5 4V6L7 10H3zm13.5-1.5-1.4 1.4L16.6 12l-1.5 1.5 1.4 1.4L18 13.4" +
    "l1.5 1.5 1.4-1.4L19.4 12l1.5-1.5-1.4-1.4L18 10.6z",
  expand: "M7 14H5v5h5v-2H7zm-2-4h2V7h3V5H5zm12 7h-3v2h5v-5h-2zM14 5v2h3v3h2V5z",
  collapse: "M5 16h3v3h2v-5H5zm3-8H5v2h5V5H8zm6 11h2v-3h3v-2h-5zm2-11V5h-2v5h5V8z",
};

export default function VideoPlayer({
  src,
  segments,
  subtitleMode,
  subtitleScale,
  onMediaElement,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const clickTimer = useRef<number | undefined>(undefined);
  // Kept in a ref so the video's ref callback stays stable across renders.
  const onMediaElementRef = useRef(onMediaElement);
  onMediaElementRef.current = onMediaElement;

  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [rate, setRate] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);

  const cues = useMemo(() => buildCues(segments), [segments]);
  const lines = cueLines(findCue(cues, currentTime), subtitleMode);

  useEffect(() => () => window.clearTimeout(clickTimer.current), []);

  const setVideoEl = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    onMediaElementRef.current(el);
  }, []);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const onTime = () => setCurrentTime(el.currentTime);
    const onMeta = () => setDuration(el.duration);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onVolume = () => {
      setMuted(el.muted);
      setVolume(el.volume);
    };
    const onRate = () => setRate(el.playbackRate);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("seeking", onTime);
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("durationchange", onMeta);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("volumechange", onVolume);
    el.addEventListener("ratechange", onRate);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("seeking", onTime);
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("durationchange", onMeta);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("volumechange", onVolume);
      el.removeEventListener("ratechange", onRate);
    };
  }, [src]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function togglePlay() {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }

  /** Hold the play toggle briefly so a double click only goes fullscreen. */
  function onVideoClick() {
    window.clearTimeout(clickTimer.current);
    clickTimer.current = window.setTimeout(togglePlay, 200);
  }

  function onVideoDoubleClick() {
    window.clearTimeout(clickTimer.current);
    void toggleFullscreen();
  }

  function seekBy(delta: number) {
    const el = videoRef.current;
    if (!el) return;
    const limit = Number.isFinite(el.duration) ? el.duration : el.currentTime;
    el.currentTime = Math.min(Math.max(0, el.currentTime + delta), limit);
    setCurrentTime(el.currentTime);
  }

  async function toggleFullscreen() {
    const wrap = wrapRef.current;
    if (!wrap) return;
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await wrap.requestFullscreen();
      // Keep the keyboard shortcuts alive after the transition.
      wrap.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    // Let the seek/volume sliders keep their own arrow-key behaviour.
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "SELECT") return;

    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const step =
        e.ctrlKey || e.metaKey
          ? SEEK_STEPS.ctrl
          : e.shiftKey
            ? SEEK_STEPS.shift
            : SEEK_STEPS.plain;
      seekBy(e.key === "ArrowLeft" ? -step : step);
      return;
    }
    // A focused control button already activates on Space/Enter; handling it
    // here as well would toggle twice and cancel itself out.
    if (tag === "BUTTON") return;

    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      togglePlay();
      return;
    }
    if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      void toggleFullscreen();
      return;
    }
    if (e.key === "m" || e.key === "M") {
      e.preventDefault();
      const el = videoRef.current;
      if (el) el.muted = !el.muted;
    }
  }

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div
      ref={wrapRef}
      className={`vp${fullscreen ? " fullscreen" : ""}`}
      style={{ "--vp-sub-scale": subtitleScale } as React.CSSProperties}
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <video
        ref={setVideoEl}
        className="vp-video"
        src={src}
        onClick={onVideoClick}
        onDoubleClick={onVideoDoubleClick}
        playsInline
      />

      {lines.length > 0 && (
        <div className="vp-subtitles">
          {lines.map((line, i) => (
            <p key={i} className={`vp-sub-line${line.dim ? " dim" : ""}`}>
              {line.text}
            </p>
          ))}
        </div>
      )}

      {/* Hover strip: the bar is visible only while the pointer is in here. */}
      <div className="vp-dock">
        <div className="vp-controls">
          <button
            type="button"
            className="vp-icon"
            onClick={togglePlay}
            title={playing ? "暫停（Space）" : "播放（Space）"}
            aria-label={playing ? "暫停" : "播放"}
          >
            <Icon path={playing ? ICONS.pause : ICONS.play} />
          </button>

          <span className="vp-time">
            {fmtClock(currentTime)} / {fmtClock(duration)}
          </span>

          <input
            className="vp-seek"
            type="range"
            min={0}
            max={duration || 0}
            step={1}
            value={Math.min(currentTime, duration || 0)}
            onChange={(e) => {
              const el = videoRef.current;
              if (!el) return;
              el.currentTime = Number(e.target.value);
              setCurrentTime(el.currentTime);
            }}
            style={{
              background: `linear-gradient(to right, rgba(78,201,168,.85) ${progress}%, rgba(255,255,255,.22) ${progress}%)`,
            }}
            aria-label="播放進度"
          />

          <button
            type="button"
            className="vp-icon"
            onClick={() => {
              const el = videoRef.current;
              if (el) el.muted = !el.muted;
            }}
            title={muted ? "取消靜音（M）" : "靜音（M）"}
            aria-label={muted ? "取消靜音" : "靜音"}
          >
            <Icon path={muted ? ICONS.muted : ICONS.volume} />
          </button>

          <input
            className="vp-volume"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(e) => {
              const el = videoRef.current;
              if (!el) return;
              el.volume = Number(e.target.value);
              el.muted = el.volume === 0;
            }}
            aria-label="音量"
          />

          <select
            className="vp-rate"
            value={rate}
            onChange={(e) => {
              const el = videoRef.current;
              if (el) el.playbackRate = Number(e.target.value);
            }}
            aria-label="播放速度"
          >
            {PLAYBACK_RATES.map((r) => (
              <option key={r} value={r}>
                {r}×
              </option>
            ))}
          </select>

          <button
            type="button"
            className="vp-icon"
            onClick={() => void toggleFullscreen()}
            title={fullscreen ? "離開全螢幕（F）" : "全螢幕（F）"}
            aria-label={fullscreen ? "離開全螢幕" : "全螢幕"}
          >
            <Icon path={fullscreen ? ICONS.collapse : ICONS.expand} />
          </button>
        </div>
      </div>
    </div>
  );
}
