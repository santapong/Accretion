/**
 * The two browser measurements axe does not make for us.
 *
 * `docs/releases/v0.3/browser-a11y-evidence.md` recorded findings F1 (390 px horizontal
 * overflow) and F2 (text contrast) from a hand-driven Chromium session, because jsdom has
 * no layout engine and `src/test/setup.ts` fakes `getBoundingClientRect`. That document is
 * static: nothing re-measures it, and it is what discharges issue #52. These functions are
 * that procedure, written down so CI repeats it.
 *
 * Both run *in the page* via `page.evaluate`, so everything below has to be
 * self-contained - no imports reach the browser context.
 *
 * WHY NOT JUST USE AXE. axe has a `color-contrast` rule, and leaning on it alone would be
 * weaker than what was recorded. axe skips nodes whose background it cannot resolve -
 * gradients, transforms, images - and files them as `incomplete` rather than `violations`.
 * The evidence run used `resultTypes: ["violations"]`, which *hides* incompletes, and it
 * measured 1,421 text nodes with zero skips, explicitly including text over the panel
 * gradient. Deleting this sweep in favour of the axe rule would silently stop checking the
 * hardest cases.
 */

/** One text node that failed the contrast threshold. */
export interface ContrastFailure {
  readonly selector: string;
  readonly text: string;
  readonly color: string;
  readonly background: string;
  readonly ratio: number;
  readonly required: number;
  readonly fontSize: number;
  readonly bold: boolean;
}

export interface ContrastReport {
  readonly measured: number;
  /** Nodes whose background could not be resolved. The evidence run had zero. */
  readonly skipped: number;
  readonly failures: readonly ContrastFailure[];
}

/** One element sticking out past the viewport with nothing clipping it. */
export interface OverflowOffender {
  readonly selector: string;
  readonly right: number;
  readonly width: number;
}

export interface OverflowReport {
  readonly scrollWidth: number;
  readonly clientWidth: number;
  readonly offenders: readonly OverflowOffender[];
}

/**
 * F1. Two assertions, not one.
 *
 * `scrollWidth > clientWidth` catches a page that scrolls sideways. On its own it is easy
 * to satisfy dishonestly - `overflow: hidden` on a wrapper makes the symptom vanish while
 * the content is still unreachable. So the evidence also enumerated every element whose
 * right edge passes the viewport AND WHICH HAS NO SCROLLABLE ANCESTOR. An element clipped
 * by its own scroll container is not an overflow; that distinction is what made
 * `.registry-card { overflow-x: auto }` a real fix rather than a cover-up.
 */
export function overflowProbe(): string {
  return `(() => {
    const doc = document.documentElement;
    const viewport = doc.clientWidth;
    const scrollable = (el) => {
      const s = getComputedStyle(el);
      return /(auto|scroll)/.test(s.overflowX) || /(auto|scroll)/.test(s.overflow);
    };
    const path = (el) => {
      if (el.id) return '#' + el.id;
      const cls = (el.className && typeof el.className === 'string')
        ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.')
        : '';
      return el.tagName.toLowerCase() + cls;
    };
    const offenders = [];
    for (const el of document.querySelectorAll('body *')) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      if (rect.right <= viewport + 0.5) continue;
      let ancestor = el.parentElement, clipped = false;
      while (ancestor && ancestor !== doc) {
        if (scrollable(ancestor)) { clipped = true; break; }
        ancestor = ancestor.parentElement;
      }
      if (!clipped) offenders.push({ selector: path(el), right: Math.round(rect.right), width: Math.round(rect.width) });
    }
    return { scrollWidth: doc.scrollWidth, clientWidth: viewport, offenders };
  })()`;
}

/**
 * F2. Contrast over text-bearing leaf elements.
 *
 * Technique fixed by the evidence (browser-a11y-evidence.md:97-104): foreground is the
 * element's computed `color`; background is the NEAREST OPAQUE ANCESTOR's background
 * colour; ratio is WCAG 2.x relative luminance; the threshold is 4.5:1, dropping to 3:1
 * for large text, where large means >= 24px or >= 18.66px bold.
 *
 * Borders are deliberately out of scope. The evidence says so outright: they are not text,
 * and WCAG's 3:1 non-text requirement was not applied to them. Widening this function to
 * borders would be a new claim, not a faithful reproduction.
 *
 * `skipped` is reported rather than swallowed. A node whose background cannot be resolved
 * is unmeasured, not passing, and the evidence's claim was zero skips across 1,421 nodes.
 */
export function contrastProbe(): string {
  return `(() => {
    const parse = (value) => {
      const m = value.match(/rgba?\\(([^)]+)\\)/);
      if (!m) return null;
      const parts = m[1].split(',').map((n) => parseFloat(n.trim()));
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };
    const luminance = ({ r, g, b }) => {
      const chan = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
      return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
    };
    const ratio = (fg, bg) => {
      const a = luminance(fg), b = luminance(bg);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    // Composite a translucent foreground over the background it sits on, so alpha does not
    // silently read as full strength.
    const flatten = (fg, bg) => fg.a >= 1 ? fg : {
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a),
      a: 1,
    };
    const opaqueBackground = (el) => {
      let node = el;
      while (node && node !== document.documentElement) {
        const c = parse(getComputedStyle(node).backgroundColor);
        if (c && c.a === 1) return c;
        node = node.parentElement;
      }
      const root = parse(getComputedStyle(document.documentElement).backgroundColor);
      return root && root.a === 1 ? root : null;
    };
    const path = (el) => {
      if (el.id) return '#' + el.id;
      const cls = (el.className && typeof el.className === 'string')
        ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.')
        : '';
      return el.tagName.toLowerCase() + cls;
    };

    let measured = 0, skipped = 0;
    const failures = [];
    for (const el of document.querySelectorAll('body *')) {
      // Leaf elements only: a container's own text is measured on the child that holds it.
      if (el.children.length > 0) continue;
      const text = (el.textContent || '').trim();
      if (!text) continue;
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      if (parseFloat(style.opacity) === 0) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      const bg = opaqueBackground(el);
      const rawFg = parse(style.color);
      if (!bg || !rawFg) { skipped++; continue; }
      const fg = flatten(rawFg, bg);

      const size = parseFloat(style.fontSize);
      const weight = parseInt(style.fontWeight, 10) || 400;
      const bold = weight >= 700;
      const large = size >= 24 || (bold && size >= 18.66);
      const required = large ? 3 : 4.5;
      const value = ratio(fg, bg);
      measured++;
      if (value + 0.005 < required) {
        failures.push({
          selector: path(el), text: text.slice(0, 60),
          color: style.color, background: 'rgb(' + [bg.r, bg.g, bg.b].map(Math.round).join(', ') + ')',
          ratio: Math.round(value * 100) / 100, required, fontSize: size, bold,
        });
      }
    }
    return { measured, skipped, failures };
  })()`;
}
