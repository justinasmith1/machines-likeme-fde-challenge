// @vitest-environment node
//
// The stylesheet's promises, checked from the CSS itself: readable contrast for every
// colour pair used for text, visible focus, and the styles that carry rules (a suggestion
// and a corrected value must not look like a value the model read).

import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

function variable(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(css);
  if (!match) throw new Error(`--${name} is not a six-digit colour in globals.css`);
  return match[1];
}

function luminance(hex: string): number {
  const channel = (offset: number) => {
    const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** WCAG contrast ratio between two colours. */
function contrast(first: string, second: string): number {
  const [light, dark] = [luminance(variable(first)), luminance(variable(second))].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no rule for ${selector}`);
  return css.slice(start, css.indexOf("}", start));
}

describe("contrast (WCAG AA: 4.5 for text)", () => {
  const TEXT_PAIRS: Array<[string, string]> = [
    ["text", "page"],
    ["text", "surface"],
    ["text", "surface-alt"],
    ["muted", "surface"],
    ["muted", "page"],
    ["muted", "surface-alt"],
    ["brand", "surface"],
    ["brand", "brand-soft"],
    ["brand-strong", "brand-soft"],
    ["on-brand", "brand"],
    ["on-brand", "brand-strong"],
    // Status families: badge, tag and notice text on their own background.
    ["success", "success-bg"],
    ["warning", "warning-bg"],
    ["danger", "danger-bg"],
    ["progress", "progress-bg"],
    ["neutral", "neutral-bg"],
  ];

  test.each(TEXT_PAIRS)("%s on %s", (foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });

  test("the focus ring stands out from every background it appears on (3 for non-text)", () => {
    for (const background of ["page", "surface", "brand-soft", "surface-alt"]) {
      expect(contrast("focus", background)).toBeGreaterThanOrEqual(3);
    }
  });

  test("the primary colour is not a default blue", () => {
    const brand = variable("brand");
    const [red, green, blue] = [1, 3, 5].map((offset) => Number.parseInt(brand.slice(offset, offset + 2), 16));
    expect(green).toBeGreaterThan(blue);
    expect(green).toBeGreaterThan(red);
  });
});

describe("status colours", () => {
  test("the four status families are different colours from one another", () => {
    const backgrounds = ["success-bg", "warning-bg", "danger-bg", "progress-bg", "neutral-bg"].map(variable);
    expect(new Set(backgrounds).size).toBe(backgrounds.length);
    const texts = ["success", "warning", "danger", "progress", "neutral"].map(variable);
    expect(new Set(texts).size).toBe(texts.length);
  });

  test("every badge tone has a background, a text colour and a border", () => {
    for (const tone of ["success", "warning", "danger", "progress", "neutral"]) {
      const badge = rule(`.badge-${tone}`);
      expect(badge).toContain(`background: var(--${tone}-bg)`);
      expect(badge).toContain(`color: var(--${tone})`);
      expect(badge).toContain(`border-color: var(--${tone}-line)`);
    }
  });
});

describe("styles that carry rules", () => {
  test("a suggestion has a dashed border and italic text, unlike a value (DL-18)", () => {
    const suggestion = rule(".suggestion");
    expect(suggestion).toContain("border: 1px dashed");
    expect(suggestion).toContain("font-style: italic");
    expect(rule(".suggestion-label")).toContain("text-transform: uppercase");
  });

  test("a corrected value has its own tag style, different from 'read'", () => {
    const corrected = rule(".tag-corrected");
    const read = rule(".tag-read");
    expect(corrected).toContain("var(--progress-bg)");
    expect(read).toContain("var(--success-bg)");
    expect(corrected).toContain("border: 1px solid");
    expect(read).not.toContain("border:");
  });

  test("the decision panel is marked as the main action", () => {
    const primary = rule(".panel-primary");
    expect(primary).toContain("border-top: 4px solid var(--brand)");
  });
});

describe("interaction and layout", () => {
  test("keyboard focus is visible everywhere, including the hidden file input's button", () => {
    expect(rule(":focus-visible")).toContain("outline: 3px solid var(--focus)");
    expect(css).toContain(".filepicker-input:focus-visible + .filepicker-button");
  });

  test("the native file input is hidden visually, not with display:none or visibility", () => {
    const input = rule(".filepicker-input");
    expect(input).toContain("opacity: 0");
    expect(input).not.toContain("display: none");
    expect(input).not.toContain("visibility");
  });

  test("buttons and table rows react to hover", () => {
    expect(css).toContain("button:hover:not(:disabled)");
    expect(css).toContain("tbody tr:hover td");
  });

  test("amounts use tabular figures so they line up", () => {
    expect(css).toMatch(/\.num,\s*\.amount,[^{]*\{[^}]*font-variant-numeric: tabular-nums/);
  });

  test("document and data sit side by side on wide screens and stack on narrow ones", () => {
    expect(rule(".split")).toContain("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)");
    expect(css).toMatch(/@media \(max-width: 1080px\)\s*\{\s*\.split\s*\{\s*grid-template-columns: 1fr;/);
  });

  test("on wide screens the document column sticks while the other column scrolls", () => {
    const wide = /@media \(min-width: 1081px\)\s*\{\s*\.split-document\s*\{([^}]*)\}/.exec(css);
    expect(wide).not.toBeNull();
    expect(wide![1]).toContain("position: sticky");
    expect(wide![1]).toContain("top: 12px");
    // Never taller than the window, or its end could not be reached.
    expect(wide![1]).toContain("max-height: calc(100vh - 24px)");
    expect(wide![1]).toContain("overflow-y: auto");
    // The columns start at the top, so the shorter one is not stretched.
    expect(rule(".split")).toContain("align-items: start");
    // Stacked on narrow screens: nothing sticks there.
    expect(css.replace(wide![0], "")).not.toContain("position: sticky");
  });

  test("motion is switched off for people who ask for that", () => {
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });

  test("no stylesheet or font is loaded from another site", () => {
    expect(css).not.toMatch(/@import|url\(\s*["']?https?:/);
  });
});
