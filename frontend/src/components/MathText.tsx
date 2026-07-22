import katex from "katex";
import "katex/dist/katex.min.css";

/**
 * Render text that contains Codeforces-style inline LaTeX. CF delimits math with
 * triple dollars (`$$$...$$$`), so splitting on that yields alternating plain-text
 * and math segments (odd indices are math). Newlines are preserved via CSS
 * (`white-space: pre-wrap` on `.prose-math`).
 */
export function MathText({ text }: { text: string }) {
  const parts = text.split("$$$");
  return (
    <div className="prose-math">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <span
            key={i}
            // KaTeX output is trusted (we generate it); throwOnError:false renders
            // malformed TeX as red source text instead of crashing the page.
            dangerouslySetInnerHTML={{
              __html: katex.renderToString(part, { throwOnError: false }),
            }}
          />
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </div>
  );
}
