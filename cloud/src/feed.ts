// Private RSS 2.0 podcast feeds with iTunes and Podcasting 2.0 tags.
//
// Every URL in a feed carries FEED_TOKEN, because podcast apps fetch episodes,
// transcripts and chapters without cookies. The channel is blocked from every
// directory (itunes:block, googleplay:block, podcast:block) and locked
// (podcast:locked) so a hosting platform won't import it.

import { Item, listItems } from "./db";
import { Env, escapeXml as x, formatDuration, rfc822 } from "./util";

export function audioName(contentType: string): string {
  return contentType === "audio/mpeg" ? "audio.mp3" : "audio.m4a";
}

// Apple's documented enclosure type for .m4a.
function enclosureType(contentType: string): string {
  return contentType === "audio/mpeg" ? "audio/mpeg" : "audio/x-m4a";
}

export function itemDescription(item: Item): string {
  if (item.description) return item.description;
  const parts: string[] = [];
  const what = item.template ? `${item.template} on` : "A program about";
  parts.push(`${what} “${item.title}”${item.author && item.author !== "Unknown Author" ? ` by ${item.author}` : ""}.`);
  if (item.speakers.length) parts.push(`Voices: ${item.speakers.map((s) => s.name).join(", ")}.`);
  parts.push("Generated with enTalkenator for private listening.");
  return parts.join(" ");
}

function episodeTitle(item: Item): string {
  return item.template ? `${item.title} (${item.template})` : item.title;
}

function contentHtml(item: Item): string {
  const rows: string[] = [`<p>${x(itemDescription(item))}</p>`];
  if (item.speakers.length) {
    rows.push("<ul>" + item.speakers.map((s) => `<li>${x(s.name)}${s.voice ? ` <small>(voice: ${x(s.voice)})</small>` : ""}</li>`).join("") + "</ul>");
  }
  if (item.duration) rows.push(`<p>Length ${x(formatDuration(item.duration))}.</p>`);
  return rows.join("");
}

export async function buildFeed(env: Env, origin: string, token: string, tag: { slug: string; name: string } | null): Promise<string> {
  const items = (await listItems(env, { publishedOnly: true, tagSlug: tag?.slug })).filter((i) => i.files.audio);
  const library = env.LIBRARY_TITLE || "enTalkenator Library";
  const title = tag ? `${library}: ${tag.name}` : library;
  const self = `${origin}/feed/${token}${tag ? "/" + tag.slug : ""}.xml`;
  const media = (id: string, name: string) => `${origin}/media/${token}/${encodeURIComponent(id)}/${name}`;
  const cover = `${origin}/media/${token}/cover.png`;
  // The latest change, so the feed (and its ETag) only changes when the library does.
  const changed = items.map((i) => i.updatedAt).sort().pop();

  const out: string[] = [];
  out.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  out.push(
    `<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:googleplay="http://www.google.com/schemas/play-podcasts/1.0">`,
  );
  out.push(`<channel>`);
  out.push(`<title>${x(title)}</title>`);
  out.push(`<link>${x(origin)}/app/</link>`);
  out.push(`<atom:link href="${x(self)}" rel="self" type="application/rss+xml"/>`);
  out.push(
    `<description>${x(
      `A private feed of programs generated with enTalkenator from documents you chose. For your own listening: please don't share this feed address or list it anywhere.`,
    )}</description>`,
  );
  out.push(`<language>en</language>`);
  if (changed) out.push(`<lastBuildDate>${rfc822(changed)}</lastBuildDate>`);
  out.push(`<generator>enTalkenator Library</generator>`);
  out.push(`<image><url>${x(cover)}</url><title>${x(title)}</title><link>${x(origin)}/app/</link></image>`);
  out.push(`<itunes:image href="${x(cover)}"/>`);
  out.push(`<itunes:author>enTalkenator</itunes:author>`);
  out.push(`<itunes:summary>${x(`Private enTalkenator programs${tag ? ` tagged “${tag.name}”` : ""}.`)}</itunes:summary>`);
  out.push(`<itunes:type>episodic</itunes:type>`);
  out.push(`<itunes:explicit>false</itunes:explicit>`);
  out.push(`<itunes:category text="Education"/>`);
  out.push(`<itunes:block>Yes</itunes:block>`);
  out.push(`<googleplay:block>yes</googleplay:block>`);
  out.push(`<podcast:block>yes</podcast:block>`);
  out.push(`<podcast:locked>yes</podcast:locked>`);

  for (const item of items) {
    const audio = item.files.audio!;
    const desc = itemDescription(item);
    out.push(`<item>`);
    out.push(`<title>${x(episodeTitle(item))}</title>`);
    out.push(`<guid isPermaLink="false">entalkenator:${x(item.id)}</guid>`);
    out.push(`<link>${x(origin)}/app/#/item/${x(encodeURIComponent(item.id))}</link>`);
    out.push(`<pubDate>${rfc822(item.publishedAt || item.createdAt)}</pubDate>`);
    out.push(`<description>${x(desc)}</description>`);
    out.push(`<itunes:summary>${x(desc)}</itunes:summary>`);
    out.push(`<content:encoded>${x(contentHtml(item))}</content:encoded>`);
    out.push(`<enclosure url="${x(media(item.id, audioName(audio.contentType)))}" length="${audio.size}" type="${enclosureType(audio.contentType)}"/>`);
    if (item.duration) out.push(`<itunes:duration>${Math.round(item.duration)}</itunes:duration>`);
    if (item.author) out.push(`<itunes:author>${x(item.author)}</itunes:author>`);
    out.push(`<itunes:episodeType>full</itunes:episodeType>`);
    out.push(`<itunes:explicit>false</itunes:explicit>`);
    if (item.files.transcript) {
      if (item.files.chapters) {
        out.push(`<podcast:transcript url="${x(media(item.id, "transcript.vtt"))}" type="text/vtt" rel="captions"/>`);
        out.push(`<podcast:transcript url="${x(media(item.id, "transcript.json"))}" type="application/json"/>`);
      }
      out.push(`<podcast:transcript url="${x(media(item.id, "transcript.txt"))}" type="text/plain"/>`);
    }
    if (item.files.chapters) {
      out.push(`<podcast:chapters url="${x(media(item.id, "chapters.json"))}" type="application/json+chapters"/>`);
    }
    for (const t of item.tags) out.push(`<category>${x(t.name)}</category>`);
    out.push(`</item>`);
  }
  out.push(`</channel>`);
  out.push(`</rss>`);
  return out.join("\n") + "\n";
}
