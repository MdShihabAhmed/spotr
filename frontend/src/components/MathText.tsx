import katex from "katex";
import "katex/dist/katex.min.css";

/**
 * Render text containing LaTeX from any of our sources:
 * - Problem statements/constraints use triple dollars: `$$$…$$$` (inline).
 * - Generated hints and tutor chat use STANDARD LaTeX: `$…$` / `\(…\)` inline,
 *   `$$…$$` / `\[…\]` display.
 *
 * A single tokenizer walks the string trying delimiters LONGEST-FIRST, so `$$$` isn't
 * mis-read as `$$`+`$`, and `$$` isn't mis-read as two `$`. Non-math runs keep their
 * newlines via `white-space: pre-wrap` on `.prose-math`.
 */
const DELIMS: { open: string; close: string; display: boolean }[] = [
  { open: "$$$", close: "$$$", display: false }, // Codeforces inline
  { open: "$$", close: "$$", display: true }, // standard display
  { open: "\\[", close: "\\]", display: true }, // standard display
  { open: "\\(", close: "\\)", display: false }, // standard inline
  { open: "$", close: "$", display: false }, // standard inline
];

type Segment = { math: boolean; display: boolean; text: string };

function tokenize(text: string): Segment[] {
  const segments: Segment[] = [];
  let plain = "";
  let i = 0;
  const flushPlain = () => {
    if (plain) {
      segments.push({ math: false, display: false, text: plain });
      plain = "";
    }
  };

  while (i < text.length) {
    let matched = false;
    for (const d of DELIMS) {
      if (!text.startsWith(d.open, i)) continue;
      const end = text.indexOf(d.close, i + d.open.length);
      if (end === -1) continue;
      const body = text.slice(i + d.open.length, end);
      if (body.trim().length === 0) continue; // skip empty `$$`, stray delimiters
      flushPlain();
      segments.push({ math: true, display: d.display, text: body });
      i = end + d.close.length;
      matched = true;
      break;
    }
    if (!matched) {
      plain += text[i];
      i += 1;
    }
  }
  flushPlain();
  return segments;
}

export function MathText({ text }: { text: string }) {
  const segments = tokenize(text);
  return (
    <div className="prose-math">
      {segments.map((seg, i) =>
        seg.math ? (
          <span
            key={i}
            // KaTeX output is trusted (we generate it); throwOnError:false renders
            // malformed TeX as red source text instead of crashing the page.
            dangerouslySetInnerHTML={{
              __html: katex.renderToString(seg.text, {
                throwOnError: false,
                displayMode: seg.display,
              }),
            }}
          />
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </div>
  );
}
