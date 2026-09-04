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

/* ------------------------------------------------------------------------------------- */
/* M9 PR5a: the computed-style probe behind the Tailwind port's rendering diff.             */
/* ------------------------------------------------------------------------------------- */

/**
 * The operational definition of "pixel-preserving".
 *
 * Every property `styles.css` declares, expanded to the longhands `getComputedStyle`
 * actually resolves. Nothing here is aspirational: the list was derived by parsing the
 * stylesheet's 447 rule blocks for declared property names (82 of them) and expanding each
 * shorthand, so a property the sheet never sets is absent and a property it sets is
 * measured on every element.
 *
 * Two consequences worth stating plainly.
 *
 * FIRST, this list is the claim. `style-diff.spec.ts` proves that a port changed nothing
 * ABOUT THESE PROPERTIES. A rule that only affects something outside the list would pass
 * unnoticed, which is why the list is derived from the sheet rather than hand-picked, and
 * why the shorthands are expanded rather than trusted: `getComputedStyle(el).background`
 * resolves to a serialisation that drops values it considers initial, so measuring the
 * shorthand alone loses `backgroundImage` - and every panel in this app is a gradient.
 *
 * SECOND, the expensive-looking entries are the load-bearing ones. `boxShadow` is how the
 * selected run row and the focus rings are drawn; `transform` is the brand mark's rotation
 * and every React Flow node's position; `backdropFilter` is the sticky nav's blur;
 * `outlineWidth`/`Style`/`Color`/`Offset` are the four focus-visible rules that the v0.2
 * accessibility findings turned into real fixes. Dropping any of them would leave the gate
 * green over exactly the rules that were hardest to get right.
 */
export const COMPUTED_STYLE_PROPERTIES: readonly string[] = [
  // Box and position.
  "boxSizing", "display", "position", "top", "right", "bottom", "left", "zIndex",
  "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
  "marginTop", "marginRight", "marginBottom", "marginLeft",
  "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
  "overflowX", "overflowY", "float", "resize", "pointerEvents", "cursor", "opacity",
  // Flex and grid. `gap` resolves as two longhands and `place-items` as two more, both of
  // which are already named here.
  "flexDirection", "flexWrap", "flexGrow", "flexShrink", "flexBasis", "order",
  "alignItems", "alignContent", "alignSelf",
  "justifyContent", "justifyItems", "justifySelf",
  "rowGap", "columnGap",
  "gridTemplateColumns", "gridColumnStart", "gridColumnEnd",
  // Border, including the four corner radii the shorthand hides.
  "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
  "borderTopStyle", "borderRightStyle", "borderBottomStyle", "borderLeftStyle",
  "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor",
  "borderTopLeftRadius", "borderTopRightRadius",
  "borderBottomRightRadius", "borderBottomLeftRadius",
  "borderCollapse",
  // Background. `backgroundImage` carries every gradient in the app.
  "backgroundColor", "backgroundImage", "backgroundPosition", "backgroundSize",
  "backgroundRepeat", "backgroundOrigin", "backgroundClip", "backgroundAttachment",
  // Effects.
  "boxShadow", "backdropFilter", "transform",
  "outlineWidth", "outlineStyle", "outlineColor", "outlineOffset",
  // Typography.
  "color", "fontFamily", "fontSize", "fontStyle", "fontWeight", "fontStretch",
  "fontSynthesis", "lineHeight", "letterSpacing",
  "textAlign", "textTransform", "textOverflow", "textAnchor", "whiteSpace", "overflowWrap",
  "textDecorationLine", "textDecorationColor", "textDecorationStyle",
  // Generated content and lists.
  "content", "listStyleType", "listStylePosition", "listStyleImage",
  // SVG, for the frozen research curve.
  "fill", "stroke", "strokeWidth", "strokeDasharray",
  // Form controls.
  "accentColor",
];

/**
 * Properties read through CSS Typed OM instead of `getComputedStyle`.
 *
 * `getComputedStyle(el).marginLeft` is NOT DETERMINISTIC for an `auto` margin in Chromium.
 * Measured over 40 loads of `/runs/:runId` against the same two builds while this gate was
 * being built, `div.shell` (`margin: 0 auto`) reported:
 *
 *   1440px:  marginLeft 57.5938px  ...and, on two loads out of eight, 0px
 *   660px:   marginLeft 26.4062px  ...and, on two loads out of eight, 0px
 *
 * The box was correctly centred every time - `getBoundingClientRect().x` was 58 and 26 in
 * both readings - so the LAYOUT never differed. Chromium was returning the computed value
 * (`0px`) rather than the used value, stably for the whole page load, and no forced layout
 * flush changed it: reading `documentElement.offsetHeight`, the element's own
 * `getBoundingClientRect()`, or a fresh `CSSStyleDeclaration` first all still gave `0px`.
 *
 * `element.computedStyleMap().get("margin-left")` returned `auto` on all forty loads. So
 * margins are read there instead. The trade is stated rather than hidden: the diff now
 * compares margins as COMPUTED values (`auto`) rather than used ones (`57.5938px`), so a
 * layout consequence of an auto margin is observed through the element's `width` and
 * through its neighbours' geometry rather than through the margin itself - while a real
 * change to a margin declaration (`auto` to `10px`, `36px` to `40px`) is reported exactly
 * as before, and reported reliably instead of one run in four.
 *
 * Typed OM is per-element and has no pseudo-element form, so pseudo-element margins still
 * come from `getComputedStyle`. No rule in this stylesheet gives a generated box an auto
 * margin, so nothing is currently read unreliably there.
 */
export const TYPED_OM_PROPERTIES: Readonly<Record<string, string>> = {
  marginTop: "margin-top",
  marginRight: "margin-right",
  marginBottom: "margin-bottom",
  marginLeft: "margin-left",
};

/**
 * The subset that is meaningful on `::first-letter`, and the gate for capturing it.
 *
 * `::before` and `::after` can be gated on `content`, which resolves to `none` unless a
 * rule generates the box. `::first-letter` cannot: measured in Chromium while writing this,
 * `getComputedStyle(el, '::first-letter').content` returns `normal` for every element on
 * the page, so a content gate would capture a third style map for all of them and bury the
 * report in noise.
 *
 * So the gate is difference: `::first-letter` inherits from its originating element, and an
 * element with no `::first-letter` rule resolves identically to it on every property that
 * applies to a first letter. When one of these differs, a rule is in play and the whole map
 * is captured; when none does, there is nothing to compare.
 *
 * The list is the CSS-spec set of properties that apply to `::first-letter`, minus the ones
 * this sheet never declares. `transform` is deliberately NOT here, and the omission is a
 * finding rather than an oversight - see the note on `computedStyleProbe`.
 */
export const FIRST_LETTER_PROPERTIES: readonly string[] = [
  "color", "backgroundColor", "backgroundImage",
  "fontFamily", "fontSize", "fontStyle", "fontWeight", "fontStretch",
  "lineHeight", "letterSpacing", "textTransform",
  "textDecorationLine", "textDecorationColor", "textDecorationStyle",
  "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
  "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor",
  "float", "opacity",
];

/**
 * Properties re-measured under focus and hover.
 *
 * `styles.css` has seven interaction-only rules (:21, :25, :31, :42-43, :76, :94, :304) and
 * a load-and-measure sweep never renders any of them: no element is hovered and nothing has
 * focus on a freshly loaded page. They change outline, border, shadow, background, colour
 * and opacity and nothing else, so re-capturing the whole property list per interaction
 * would multiply the sweep's cost for no additional coverage.
 */
export const INTERACTION_PROPERTIES: readonly string[] = [
  "outlineWidth", "outlineStyle", "outlineColor", "outlineOffset",
  "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
  "borderTopStyle", "borderRightStyle", "borderBottomStyle", "borderLeftStyle",
  "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor",
  "boxShadow", "backgroundColor", "backgroundImage", "color", "opacity",
];

/** Every element the interaction pass gives focus to. */
export const FOCUSABLE_SELECTOR = "input, textarea, select, button, a[href], [tabindex]";

/**
 * Elements the interaction pass hovers, one instance each.
 *
 * ## Completeness, which is checked rather than claimed
 *
 * This list must name a hover target for EVERY `:hover` rule the pinned stylesheet
 * declares, and nothing else. `cssPort.test.ts` ("the interaction pass covers every hover
 * rule") re-derives the set from `e2e/fixtures/styles.pre-pr5.css` - split each rule's
 * selector list on top-level commas, keep the parts containing `:hover`, truncate each at
 * the pseudo-class - and fails if the two sets differ in either direction. That test exists
 * because the earlier four-entry list silently left three rules unmeasured
 * (`.benchmark-table tbody tr`, `.projection-flow .react-flow__controls-button` and
 * `.projection-node.react-flow__node-default.selectable`), and a later slice that moves
 * those rules would have reported "zero differences" over hover states it never triggered.
 *
 * The last two are qualified on React Flow's own classes, so hovering them is also the only
 * measurement that would catch an override losing to xyflow after a careless move - which is
 * exactly what PR5c does to them.
 *
 * ## Why one instance each
 *
 * Every hover rule in the sheet is class-keyed: `.run:hover` styles the first run row
 * exactly as it styles the fiftieth. Hovering all of them would multiply a 17-route sweep
 * by the length of the run list for no new rule coverage. Completeness is over RULES, not
 * over elements.
 *
 * A selector no route renders is skipped by the sweep and named in its log line; the
 * stylesheet-level check above is what keeps the list itself honest.
 *
 * ## One of the seven is unreachable today, and the log says so
 *
 * `.projection-node.react-flow__node-default.selectable:hover` needs xyflow's `.selectable`
 * class, which xyflow only emits when `elementsSelectable` is on. `RunExecution.tsx:227`
 * sets it to `false`, so the rule matches nothing in the shipped app - it is dead in the
 * same way `.brand-mark::first-letter { transform }` is, and for the same kind of reason.
 * It stays listed: the entry is what makes the sweep print "rendered nowhere" for it every
 * run, and union equality in `cssPort.test.ts` is what holds the rule itself to moving
 * byte-verbatim. If PR9b's custom node types ever turn selection on, the target is already
 * pointed at it.
 */
export const HOVER_SELECTORS: readonly string[] = [
  ".nav-links a",
  ".primary-button",
  ".secondary-button",
  ".run",
  ".benchmark-table tbody tr",
  ".projection-flow .react-flow__controls-button",
  ".projection-node.react-flow__node-default.selectable",
];

/**
 * Capture every element's computed styles, in `body *` order, with its pseudo-elements.
 *
 * The order is the alignment key: `style-diff.spec.ts` compares index N of one build against
 * index N of the other and only does so after a structural fingerprint (tag plus child
 * count, from the same walk) says the two trees match.
 *
 * ## Pseudo-elements
 *
 * `body *` cannot see them, and four rules in this sheet depend on them: `.brand-mark::
 * first-letter` (:28), `.runtime-card::after` (:63), `.candidate-tree::before` (:202) and
 * `.candidate-card::before` (:204). Each is captured under a `<pseudo>.<property>` key on
 * its originating element, so a rule that stops generating a box removes keys and
 * `diffStyles` reports them as `(absent)` rather than passing in silence.
 *
 * ## A rule this probe cannot see, stated rather than hidden
 *
 * `.brand-mark::first-letter { transform: rotate(-45deg) }` is UNOBSERVABLE. `transform` is
 * not in the CSS specification's set of properties that apply to `::first-letter`, and
 * Chromium duly reports `none` there even with the rule matching - measured directly while
 * this probe was written. `.brand-mark` is also `display: grid`, which generates no
 * first-letter box at all. The declaration has no effect in the shipped app today, this
 * gate cannot prove the port preserves it, and the union-equality test in `cssPort.test.ts`
 * is what holds it to moving byte-verbatim. Saying so here is cheaper than a future reader
 * concluding the rendering diff covers every rule in the file.
 */
export function computedStyleProbe(): string {
  return `(() => {
    const PROPERTIES = ${JSON.stringify(COMPUTED_STYLE_PROPERTIES)};
    const FIRST_LETTER = ${JSON.stringify(FIRST_LETTER_PROPERTIES)};
    const TYPED_OM = ${JSON.stringify(TYPED_OM_PROPERTIES)};
    const read = (style, properties, into, prefix) => {
      for (const property of properties) into[prefix + property] = String(style[property] ?? '');
      return into;
    };
    const elements = [];
    for (const el of document.querySelectorAll('body *')) {
      const own = getComputedStyle(el);
      const styles = read(own, PROPERTIES, {}, '');
      // Overwrite the properties whose used-value reporting is unreliable. See
      // TYPED_OM_PROPERTIES: this is the difference between a gate that fails on one run in
      // four and one that fails only when something changed.
      if (el.computedStyleMap) {
        const map = el.computedStyleMap();
        for (const property of Object.keys(TYPED_OM)) {
          styles[property] = String(map.get(TYPED_OM[property]));
        }
      }
      for (const pseudo of ['::before', '::after']) {
        const generated = getComputedStyle(el, pseudo);
        // 'none' is what a pseudo-element with no rule resolves to. Anything else - '""'
        // included - means a box exists and its styles are part of the rendering.
        if (generated.content === 'none') continue;
        read(generated, PROPERTIES, styles, pseudo + '.');
      }
      const firstLetter = getComputedStyle(el, '::first-letter');
      if (FIRST_LETTER.some((property) => firstLetter[property] !== own[property])) {
        read(firstLetter, FIRST_LETTER, styles, '::first-letter.');
      }
      elements.push({ tag: el.tagName.toLowerCase(), children: el.children.length, styles });
    }
    return elements;
  })()`;
}

/**
 * Focus every focusable element in turn and record what changes.
 *
 * Driven in the page rather than through Playwright's keyboard, because a `Tab` sweep would
 * take a few hundred round trips per route and would stop at whatever the browser considers
 * the end of the tab order, while `.event-list:focus-visible` and
 * `.registry-card:focus-visible` sit on elements that only have a `tabindex`.
 *
 * `preventScroll` is not cosmetic: `focus()` scrolls its target into view by default, and a
 * sweep that scrolls the page between the branch measurement and the base measurement would
 * change `getBoundingClientRect`-derived layout values and report differences that are
 * artefacts of the probe.
 *
 * Focus is restored to the body afterwards so the page is left as it was found.
 */
export function focusStateProbe(): string {
  return `(() => {
    const PROPERTIES = ${JSON.stringify(INTERACTION_PROPERTIES)};
    const captured = [];
    for (const el of document.querySelectorAll(${JSON.stringify(FOCUSABLE_SELECTOR)})) {
      el.focus({ preventScroll: true });
      const style = getComputedStyle(el);
      const styles = {};
      for (const property of PROPERTIES) styles[property] = String(style[property] ?? '');
      captured.push({ tag: el.tagName.toLowerCase(), children: el.children.length, styles });
      el.blur();
    }
    return captured;
  })()`;
}

/**
 * Measure one hovered element. `null` when the route has no such element.
 *
 * The hover itself is Playwright's: `:hover` needs a real pointer and cannot be simulated
 * from inside the page. This reads the result of that pointer being where it is, so the
 * call order is always `page.hover(selector)` then `page.evaluate(hoveredStyleProbe(selector))`.
 */
export function hoveredStyleProbe(selector: string): string {
  return `(() => {
    const PROPERTIES = ${JSON.stringify(INTERACTION_PROPERTIES)};
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    const style = getComputedStyle(el);
    const styles = {};
    for (const property of PROPERTIES) styles[property] = String(style[property] ?? '');
    return { tag: el.tagName.toLowerCase(), children: el.children.length, styles };
  })()`;
}

/**
 * Block until the page's geometry stops changing, or give up after a bounded wait.
 *
 * `openRoute`'s barriers say the route has LOADED and its polls have gone quiet. They do
 * not say the layout has finished settling, and on the run page it has not: React Flow
 * mounts a canvas, measures it through a `ResizeObserver`, runs `fitView`, and reflows.
 * Measured symptom, from two runs of this gate against the same pair of builds:
 *
 *   /runs/:runId @ 660: #26 div marginLeft 26.4062px → 0px      (first run)
 *   /runs/:runId @ 660: #26 div marginLeft 0px → 26.4062px      (second run, reversed)
 *
 * `#26` is `div.shell`, whose `margin: 0 auto` resolves to 26.4062px at a 660px viewport
 * and reads back as `0px` while the box is mid-reflow. The direction reversing between runs
 * is what identifies it: a stylesheet difference has a direction, a race does not.
 *
 * The fix is a barrier, not a retry and not a tolerance. Retrying would be retrying on a
 * style difference, which `styleDiff.ts` exists to forbid; widening `SUBPIXEL_EPSILON` to
 * 26px would disable the comparison. This waits for the same condition on both builds and
 * returns how many frames it took, so a reader can see whether it did anything.
 *
 * The frame cap is a real cap: on a page that genuinely never settles this returns anyway
 * and the measurement proceeds, because a hang here would be indistinguishable from a
 * backend that never answered.
 */
export function layoutSettledProbe(maxFrames = 60): string {
  return `(() => new Promise((resolve) => {
    const digest = () => {
      let out = '';
      for (const el of document.querySelectorAll('body *')) {
        const rect = el.getBoundingClientRect();
        out += Math.round(rect.x) + ',' + Math.round(rect.y) + ','
             + Math.round(rect.width) + ',' + Math.round(rect.height) + ';';
      }
      return out;
    };
    let previous = digest();
    let stable = 0;
    let frames = 0;
    const tick = () => {
      frames += 1;
      const current = digest();
      stable = current === previous ? stable + 1 : 0;
      previous = current;
      // Two consecutive identical frames, not one: a single match can happen between two
      // halves of the same reflow.
      if (stable >= 2 || frames >= ${maxFrames}) {
        resolve({ frames, settled: stable >= 2 });
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }))()`;
}
