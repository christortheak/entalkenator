// Transcripts: "[Name]: text" turns (engine.py finalize) paired with the per-turn
// chapter times render_audio.py writes, and turned into WebVTT and Podcasting 2.0
// JSON transcripts for podcast apps.

export interface Turn {
  speaker: string;
  text: string;
  start: number | null;
  end: number | null;
}

interface Chapter {
  startTime?: number;
  endTime?: number;
  speaker?: string;
  title?: string;
}

// Same pattern as render_audio.py's TURN_RE, so turns line up with the audio clips.
const TURN_RE = /^\[([^\]\n]{1,80})\]:[ \t]?/gm;

export function parseTranscript(text: string): { speaker: string; text: string }[] {
  const matches = [...text.matchAll(TURN_RE)];
  if (!matches.length) return text.trim() ? [{ speaker: "Narrator", text: text.trim() }] : [];
  const turns: { speaker: string; text: string }[] = [];
  matches.forEach((m, i) => {
    const end = i + 1 < matches.length ? matches[i + 1].index! : text.length;
    const body = text.slice(m.index! + m[0].length, end).trim();
    if (body) turns.push({ speaker: m[1].trim(), text: body });
  });
  return turns;
}

/** Pair turns with chapter times when the chapters are one per turn, in the same order. */
export function timedTurns(transcript: string, chaptersJson: string | null): Turn[] {
  const turns = parseTranscript(transcript);
  let chapters: Chapter[] = [];
  if (chaptersJson) {
    try {
      const parsed = JSON.parse(chaptersJson) as { chapters?: Chapter[] };
      chapters = Array.isArray(parsed.chapters) ? parsed.chapters : [];
    } catch {
      chapters = [];
    }
  }
  const aligned =
    chapters.length === turns.length &&
    chapters.every((c, i) => typeof c.startTime === "number" && (!c.speaker || c.speaker === turns[i].speaker));
  return turns.map((t, i) => {
    if (!aligned) return { ...t, start: null, end: null };
    const c = chapters[i];
    const next = chapters[i + 1];
    const end = typeof c.endTime === "number" ? c.endTime : next && typeof next.startTime === "number" ? next.startTime : null;
    return { ...t, start: c.startTime!, end };
  });
}

/** Split a turn into caption-sized pieces (a sentence or two, about 30 words at most). */
function captionPieces(text: string): string[] {
  const flat = text.replace(/\s+/g, " ").trim();
  const sentences = flat.split(/(?<=[.!?…]["”’)]?)\s+/);
  const pieces: string[] = [];
  let cur = "";
  for (const s of sentences) {
    const words = (cur + " " + s).trim().split(" ").length;
    if (cur && words > 30) {
      pieces.push(cur);
      cur = s;
    } else {
      cur = (cur + " " + s).trim();
    }
  }
  if (cur) pieces.push(cur);
  // A single sentence over 45 words gets cut at word boundaries.
  return pieces.flatMap((p) => {
    const words = p.split(" ");
    if (words.length <= 45) return [p];
    const out: string[] = [];
    for (let i = 0; i < words.length; i += 30) out.push(words.slice(i, i + 30).join(" "));
    return out;
  });
}

interface Segment {
  speaker: string;
  start: number;
  end: number;
  body: string;
}

/** Caption segments with times spread over each turn in proportion to length. */
export function segments(turns: Turn[], duration: number | null): Segment[] | null {
  if (!turns.length || turns.some((t) => t.start === null)) return null;
  const out: Segment[] = [];
  turns.forEach((t, i) => {
    const start = t.start!;
    let end = t.end ?? (i + 1 < turns.length ? turns[i + 1].start! : duration ?? start + 5);
    if (end <= start) end = start + 1;
    const pieces = captionPieces(t.text);
    const total = pieces.reduce((n, p) => n + p.length, 0) || 1;
    let cursor = start;
    pieces.forEach((p, j) => {
      const len = j === pieces.length - 1 ? end - cursor : ((end - start) * p.length) / total;
      out.push({ speaker: t.speaker, start: cursor, end: cursor + len, body: p });
      cursor += len;
    });
  });
  return out;
}

function vttTime(s: number): string {
  const ms = Math.max(0, Math.round(s * 1000));
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  const milli = ms % 1000;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}.${String(milli).padStart(3, "0")}`;
}

function vttEscape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function toVtt(segs: Segment[]): string {
  const lines = ["WEBVTT", ""];
  segs.forEach((s, i) => {
    lines.push(String(i + 1), `${vttTime(s.start)} --> ${vttTime(s.end)}`, `<v ${vttEscape(s.speaker)}>${vttEscape(s.body)}`, "");
  });
  return lines.join("\n");
}

/** Podcasting 2.0 JSON transcript. */
export function toJsonTranscript(segs: Segment[]): string {
  const round = (n: number) => Math.round(n * 1000) / 1000;
  return JSON.stringify({
    version: "1.0.0",
    segments: segs.map((s) => ({ speaker: s.speaker, startTime: round(s.start), endTime: round(s.end), body: s.body })),
  });
}
