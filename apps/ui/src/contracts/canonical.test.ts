import { describe, expect, test } from "vitest";
import vectorSource from "../../../../tests/fixtures/contracts/v0.4/hash_vectors.json?raw";
import {
  CanonicalizationError,
  canonicalText,
  contentHash,
  contentHashInput,
  dateTime,
  decimal,
  float,
  type CanonicalValue,
} from "./canonical";

/**
 * The nineteen committed hash vectors, replayed by the TypeScript twin.
 *
 * `tests/fixtures/contracts/v0.4/hash_vectors.json` is read from the repository rather than
 * copied into `src/__fixtures__/`. A copy would be a second source of truth for the one thing
 * this file exists to check — that both implementations hash the same values to the same
 * digests — and the copy would still pass on the day the Python side changed.
 *
 * The digests are `crypto.subtle`'s, which is the API the app would actually call, and the ones
 * they are compared against were produced by Python's `hashlib` when the fixture was sealed. So
 * the comparison is already between two independent implementations of SHA-256 over two
 * independent implementations of the canonical form; what a green run says is that the FORM
 * agrees, because the hashers cannot.
 *
 * The file is read through Vite's `?raw` rather than `node:fs`: `tsconfig.app.json` typechecks
 * everything under `src/` against the browser lib and carries no `@types/node`, which
 * `tsconfig.node.json` states as the reason `budget/` lives outside `src/`.
 */

interface Vector {
  readonly name: string;
  readonly description: string;
  readonly input: CanonicalValue;
  readonly sha256: string;
}

/* ---------------------------------------------------------------------------------------- */
/* The loader: JSON with the number lexemes kept, and the fixture's typed literals revived.    */
/* ---------------------------------------------------------------------------------------- */

/**
 * Parse the vector file WITHOUT `JSON.parse`.
 *
 * `JSON.parse` destroys exactly the two distinctions the vectors are about: it reads `1.0` and
 * `1` into the same value (and `float_one` must hash differently from `integer_one`), and it
 * rounds `12345678901234567890` to `12345678901234567000` (and `large_integer` must keep every
 * digit). So the numbers are read as LEXEMES: one containing `.`, `e` or `E` is a float, one
 * too large for a safe integer is a bigint, and everything else is a number.
 */
function parseVectors(text: string): unknown {
  let at = 0;
  const skip = () => {
    while (at < text.length && " \t\r\n".includes(text[at])) at += 1;
  };
  const literal = (word: string, value: unknown) => {
    if (text.startsWith(word, at)) {
      at += word.length;
      return { value };
    }
    return null;
  };
  const parseValue = (): unknown => {
    skip();
    const character = text[at];
    if (character === "{") {
      at += 1;
      const object: Record<string, unknown> = {};
      skip();
      if (text[at] === "}") return (at += 1), object;
      for (;;) {
        skip();
        const key = parseValue() as string;
        skip();
        at += 1; // ':'
        object[key] = parseValue();
        skip();
        if (text[at] === ",") (at += 1);
        else return (at += 1), object; // '}'
      }
    }
    if (character === "[") {
      at += 1;
      const items: unknown[] = [];
      skip();
      if (text[at] === "]") return (at += 1), items;
      for (;;) {
        items.push(parseValue());
        skip();
        if (text[at] === ",") (at += 1);
        else return (at += 1), items; // ']'
      }
    }
    if (character === '"') {
      const start = at;
      at += 1;
      while (text[at] !== '"') at += text[at] === "\\" ? 2 : 1;
      at += 1;
      return JSON.parse(text.slice(start, at));
    }
    for (const [word, value] of [["true", true], ["false", false], ["null", null]] as const) {
      const hit = literal(word, value);
      if (hit) return hit.value;
    }
    const start = at;
    while (at < text.length && "+-0123456789.eE".includes(text[at])) at += 1;
    const lexeme = text.slice(start, at);
    if (/[.eE]/.test(lexeme)) return float(Number(lexeme));
    return Number.isSafeInteger(Number(lexeme)) ? Number(lexeme) : BigInt(lexeme);
  };
  const parsed = parseValue();
  skip();
  if (at < text.length) throw new Error(`trailing input at ${at}`);
  return parsed;
}

/**
 * The fields pydantic writes that the fixture's `$type: model` literals leave implicit.
 *
 * `model_dump(mode="python")` emits every field of a model, set or not, so a model vector is
 * its stated fields plus its class's defaults. Two classes appear in the file and this is what
 * they declare (`src/accretion/contracts/__init__.py`).
 *
 * The table is not taken on trust: `model_equivalent_plain_dict` is the same value written out
 * as a bare dict with those defaults spelled in, the fixture records the SAME digest for both,
 * and the test below asserts the equality as well as the two digests. A wrong default here
 * reddens the model vector against its recorded sha and breaks the pair.
 */
const MODEL_DEFAULTS: Record<string, Record<string, CanonicalValue>> = {
  PluginSignature: { algorithm: "SHA256_PIN" },
  ArtifactRef: { sha256: null },
};

/** Turn the fixture's `$type` literals into the values they encode. */
function revive(value: unknown): CanonicalValue {
  if (Array.isArray(value)) return value.map(revive);
  if (value !== null && typeof value === "object" && !("canonicalKind" in value)) {
    const record = value as Record<string, unknown>;
    const kind = record.$type;
    if (kind === "decimal") return decimal(record.value as string);
    if (kind === "datetime") return dateTime(record.value as string);
    if (kind === "model") {
      const className = record.class as string;
      const defaults = MODEL_DEFAULTS[className];
      if (!defaults) throw new Error(`no declared defaults for the contract ${className}`);
      const fields = revive(record.fields) as Record<string, CanonicalValue>;
      return { ...defaults, ...fields };
    }
    if (kind !== undefined) throw new Error(`unknown typed literal ${String(kind)}`);
    return Object.fromEntries(
      Object.entries(record).map(([key, item]) => [key, revive(item)]),
    ) as CanonicalValue;
  }
  return value as CanonicalValue;
}

const vectors = (parseVectors(vectorSource) as Vector[]).map((vector) => ({
  ...vector,
  input: revive(vector.input),
}));

/* ---------------------------------------------------------------------------------------- */
/* The claim.                                                                                 */
/* ---------------------------------------------------------------------------------------- */

test("the committed vector file is the one the Python twin replays", () => {
  expect(vectors).toHaveLength(19);
  expect(new Set(vectors.map((vector) => vector.name)).size).toBe(19);
  // Named rather than counted, so that deleting a vector from the fixture is a red test here
  // as well as in `tests/test_v04_m0_canonical.py`.
  expect(vectors.map((vector) => vector.name)).toContain("astral_key_sort_order");
  expect(vectors.map((vector) => vector.name)).toContain("float_one");
  expect(vectors.map((vector) => vector.name)).toContain("large_integer");
});

describe("every committed vector hashes to its recorded digest", () => {
  for (const vector of vectors) {
    test(vector.name, async () => {
      expect(await contentHash(vector.input), vector.description).toBe(vector.sha256);
      // `contentHashInput` is what `contentHash` hashes; asserting the bytes as well is what
      // makes a failure legible - a moved digest says WHICH document changed, not just that
      // one did.
      expect(new TextDecoder().decode(contentHashInput(vector.input)).length).toBeGreaterThan(1);
    });
  }
});

test("the two vectors holding one value in two shapes agree, model and plain dict alike", () => {
  const asModel = vectors.find((vector) => vector.name === "model_datetime_offset_normalises_to_z");
  const asDict = vectors.find((vector) => vector.name === "model_equivalent_plain_dict");
  expect(asModel && asDict).toBeTruthy();
  expect(asModel!.sha256).toBe(asDict!.sha256);
  expect(canonicalText(asModel!.input)).toBe(canonicalText(asDict!.input));
});

test("keys sort by code point, which is not what Array.sort does", () => {
  // The mutation this kills: `Object.keys(value).sort()`. JavaScript's default comparator walks
  // UTF-16 code units, so the emoji's leading surrogate (0xD83D) sorts BELOW U+FF7D and the
  // emoji comes first — a different byte string and a different digest.
  const astral = { "ｽ": 1, "😀": 2 };
  expect(canonicalText(astral)).toBe('{"ｽ":1,"😀":2}');
  expect(Object.keys(astral).sort()).toEqual(["😀", "ｽ"]);
});

test("an integer and a float of the same value are different documents", () => {
  // The mutation this kills: writing `float(1)` as `1`. `float_one` and `integer_one` carry
  // different recorded digests precisely because the two are different inputs.
  expect(canonicalText({ count: 1 })).toBe('{"count":1}');
  expect(canonicalText({ count: float(1) })).toBe('{"count":1.0}');
  const integer = vectors.find((vector) => vector.name === "integer_one")!;
  const decimalPoint = vectors.find((vector) => vector.name === "float_one")!;
  expect(integer.sha256).not.toBe(decimalPoint.sha256);
});

test("floats print as CPython's repr, exponent form and all", () => {
  const cases: readonly (readonly [number, string])[] = [
    [1, "1.0"],
    [-0, "-0.0"],
    [0.1, "0.1"],
    [1 / 3, "0.3333333333333333"],
    [1e15, "1000000000000000.0"],
    [1e16, "1e+16"],
    [1.5e300, "1.5e+300"],
    [0.0001, "0.0001"],
    [0.00001, "1e-05"],
    [5e-324, "5e-324"],
    [-2.5, "-2.5"],
  ];
  for (const [value, expected] of cases) {
    expect(canonicalText(float(value)), `repr(${value})`).toBe(expected);
  }
});

test("a large integer keeps every digit through bigint", () => {
  expect(canonicalText({ n: 12345678901234567890n })).toBe('{"n":12345678901234567890}');
  // The failure this documents: the same literal through `JSON.parse` loses the last digits.
  expect(String(JSON.parse('{"n":12345678901234567890}').n)).toBe("12345678901234567000");
  // Written as a conversion rather than as a literal: the literal itself loses precision at
  // parse time, which is the failure being refused and is also an ESLint error to write down.
  expect(() => canonicalText({ n: Number("12345678901234567890") })).toThrow(
    CanonicalizationError,
  );
});

test("a decimal keeps its trailing zeros and a datetime normalises to UTC", () => {
  expect(canonicalText({ amount: decimal("1.50") })).toBe('{"amount":"1.50"}');
  expect(canonicalText({ amount: decimal("1.5") })).toBe('{"amount":"1.5"}');
  expect(canonicalText(dateTime("2026-09-05T12:00:00+02:00"))).toBe('"2026-09-05T10:00:00Z"');
  expect(canonicalText(dateTime("2026-09-05T10:00:00+00:00"))).toBe('"2026-09-05T10:00:00Z"');
  expect(canonicalText(dateTime("2026-09-05T10:00:00.123456Z"))).toBe('"2026-09-05T10:00:00.123456Z"');
  expect(canonicalText(new Date(Date.UTC(2026, 8, 5, 10, 0, 0)))).toBe('"2026-09-05T10:00:00Z"');
});

test("the top-level content_hash is excluded and a nested one is kept", () => {
  const nested = {
    content_hash: "ignored",
    ref: { content_hash: "kept", revision: 3 },
  };
  expect(new TextDecoder().decode(contentHashInput(nested))).toBe(
    '{"ref":{"content_hash":"kept","revision":3}}',
  );
  expect(new TextDecoder().decode(contentHashInput(nested, []))).toBe(
    '{"content_hash":"ignored","ref":{"content_hash":"kept","revision":3}}',
  );
});

test("the values with no single spelling are refused rather than guessed at", () => {
  const refused: readonly [string, unknown][] = [
    ["undefined", { a: undefined }],
    ["NaN", { r: Number.NaN }],
    ["Infinity", { r: Number.POSITIVE_INFINITY }],
    ["a naive datetime", dateTime("2026-09-05T10:00:00")],
    ["a non-finite decimal", decimal("NaN")],
    ["bytes", { raw: new Uint8Array([1, 2]) }],
    ["a set", { unordered: new Set(["a"]) }],
    ["a map", { m: new Map() }],
    ["an unpaired surrogate", { s: "\ud800" }],
  ];
  for (const [name, value] of refused) {
    expect(() => canonicalText(value as CanonicalValue), name).toThrow(CanonicalizationError);
  }
});

test("null is a value and does not collide with an absent key", () => {
  expect(canonicalText({ a: null })).toBe('{"a":null}');
  expect(canonicalText({ a: null })).not.toBe(canonicalText({}));
});
