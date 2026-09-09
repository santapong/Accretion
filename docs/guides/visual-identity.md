# Visual identity

Accretion uses a graphite background, emerald green for governed workflow and
warm amber for runtime execution. The artwork represents several runtime streams
converging into an inspectable evidence trace.

## Assets

| Asset | Use | Source |
|---|---|---|
| [Project banner](../assets/accretion-banner.png) | README header; 2098 × 750 PNG | Built-in image generation, 9 September 2026 |
| [Execution model](../assets/project-overview.svg) | Define, plan, execute and verify | Editable repository-native SVG |
| [System architecture](../assets/accretion-architecture.svg) | Authority, runtime adapters and persistence | Editable repository-native SVG |
| [Earlier hero](../assets/accretion-hero.png) | Preserved original artwork and banner reference | Existing repository asset |

The banner is conceptual brand artwork, not a product screenshot, architecture
specification or acceptance artifact. Technical diagrams describe the implemented
control plane and its documented limits. Their text, geometry and accessible
metadata remain editable in Git.

Preserve the banner's aspect ratio and quiet space around the title. Avoid adding
release numbers, provider logos or operational claims to the artwork. Keep
release state in the README and versioned release records.

## Banner generation record

Mode: built-in image generation. The earlier hero was supplied as an edit
reference. No API key or external generation script was used. The resulting PNG
was copied into the repository without a subsequent raster edit.

The prompt below is retained for reproducibility of the design intent; generation
is nondeterministic and rerunning it may produce different pixels.

```text
Use case: ads-marketing. Asset type: final GitHub repository banner, wide landscape approximately 1792 x 640, no outer frame. Redesign the supplied Accretion hero into a highly polished, restrained editorial identity for a serious open-source developer tool. The existing image is the edit reference: preserve its deep graphite background, emerald-green and warm amber accent family, and the idea of multiple runtime streams converging into one governed control plane. Replace the crowded neon circuit-board scene with a much simpler sophisticated composition. Left 55 percent: ample quiet negative space, very crisp large warm-white typography reading exactly 'Accretion', beneath it two smaller clean lines reading exactly 'Control the workflow.' and 'Trust the evidence.' Keep all text at least 70 pixels from edges and exceptionally legible when reduced to GitHub README width. Right 45 percent: one elegant abstract orbital structure, precise thin emerald arcs and a few warm amber trajectories converging around a small dark geometric core; a short ordered chain of three tiny luminous points leaves the core, suggesting a verified event trace. The geometry is a conceptual brand illustration, not a UI screenshot or architecture diagram. Premium scientific-instrument art direction, mathematically elegant, low-key light, subtle depth, fine muted grain, clean typography, restrained glow, carefully balanced asymmetry. No extra text, no release numbers, no badges, no provider logos, no UI panels, no illegible labels, no chips or floating cubes, no clutter, no oversaturated neon, no decorative border. A professional enduring project banner, not a gaming or cryptocurrency advertisement.
```
