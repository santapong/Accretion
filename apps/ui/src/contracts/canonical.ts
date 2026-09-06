/**
 * The TypeScript twin of `src/accretion/contracts/canonical.py` (ADR-056; registry §3.1).
 *
 * Every v0.4 contract carries a `content_hash`, and the digest is only worth something if two
 * implementations that hold the same value agree on the bytes. The Python module fixes every
 * free parameter — key order, whitespace, ASCII escaping, number formatting — and this file
 * fixes them the same way, so that a browser can verify a receipt it was served instead of
 * trusting the field beside it.
 *
 * `canonical.test.ts` replays the nineteen committed vectors in
 * `tests/fixtures/contracts/v0.4/hash_vectors.json` — the same file
 * `tests/test_v04_m0_canonical.py` replays — and that is the whole claim: not "this looks like
 * the Python rules", but "these bytes are those bytes, on all nineteen".
 *
 * ## The rules, and where JavaScript would have got them wrong on its own
 *
 * * **Key order is by Unicode CODE POINT.** `Array.prototype.sort()` orders by UTF-16 code
 *   unit, which puts an astral character (`😀`, U+1F600, surrogate pair `D83D DE00`) BEFORE
 *   `ｽ` (U+FF7D) — the opposite of Python's order. That is the `astral_key_sort_order` vector,
 *   and it is the reason `compareCodePoints` exists rather than a bare `sort()`. RFC 8785 sorts
 *   the JavaScript way; registry decision 2 permits a documented equivalent and the Python
 *   module's docstring is that document.
 * * **Integers and floats are different values.** JavaScript has one numeric type and
 *   `String(1.0)` is `"1"`, so a float that happens to be integral would hash as an integer and
 *   `float_one` would collide with `integer_one`. A `number` here is therefore an INTEGER (and
 *   must be a safe one); a float is written `float(1)` and prints as Python's `repr` does.
 * * **Floats print as CPython's `repr`.** Shortest round-trip, exponent form when the decimal
 *   point falls outside `(-4, 16]`, two-digit exponents. `String(1e16)` is
 *   `"10000000000000000"` and `String(1e-7)` is `"1e-7"`; Python writes `1e+16` and `1e-07`.
 * * **Integers beyond 2^53 keep every digit**, which is what `bigint` is for — the
 *   `large_integer` vector is `12345678901234567890`, and `JSON.parse` alone turns it into
 *   `12345678901234567000`.
 * * **Decimals are their exact digits**, datetimes normalise to UTC with a literal `Z`, a
 *   datetime without an offset is refused, `null` is kept, and the top-level `content_hash` is
 *   excluded from its own digest while nested ones are not.
 *
 * ## What this module deliberately does not accept
 *
 * A bare JavaScript object, array, string, boolean, `null`, safe integer, `bigint` and `Date`,
 * plus the three tagged wrappers below. Everything else — `undefined`, `Map`, `Set`, a typed
 * array, a class instance, a non-finite number — is refused rather than guessed at, for the
 * reason the Python module refuses `bytes` and `set`: a guess produces a digest that the other
 * implementation cannot reproduce, which is worse than an error.
 */

/** A value cannot be canonicalized, so it cannot be hashed. The Python twin's error. */
export class CanonicalizationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CanonicalizationError";
  }
}

/** A float, even when its value is integral: `float(1)` hashes as `1.0`, not as `1`. */
export interface CanonicalFloat {
  readonly canonicalKind: "float";
  readonly value: number;
}

/** A decimal, carried as the digits `str(Decimal(...))` produces — trailing zeros included. */
export interface CanonicalDecimal {
  readonly canonicalKind: "decimal";
  readonly digits: string;
}

/** An offset-bearing timestamp. A naive one names no instant and is refused. */
export interface CanonicalDateTime {
  readonly canonicalKind: "datetime";
  readonly iso: string;
}

export type CanonicalTagged = CanonicalFloat | CanonicalDecimal | CanonicalDateTime;

export type CanonicalValue =
  | null
  | boolean
  | string
  | number
  | bigint
  | Date
  | CanonicalTagged
  | readonly CanonicalValue[]
  | { readonly [key: string]: CanonicalValue };

/** Top-level keys dropped before hashing — see {@link contentHash}. */
export const DEFAULT_HASH_EXCLUSIONS: readonly string[] = ["content_hash"];

/** Tag a number as a float, so that an integral one still prints with its `.0`. */
export function float(value: number): CanonicalFloat {
  return { canonicalKind: "float", value };
}

/**
 * Tag a decimal by its digits.
 *
 * The string is hashed as written, because that is what Python does: `Decimal("1.50")`
 * serializes as `"1.50"` and is deliberately a different value from `"1.5"`. The argument must
 * therefore be what `str(Decimal(...))` would print for the same value.
 */
export function decimal(digits: string): CanonicalDecimal {
  return { canonicalKind: "decimal", digits };
}

/** Tag an ISO-8601 timestamp. It must carry an offset; `Z` counts. */
export function dateTime(iso: string): CanonicalDateTime {
  return { canonicalKind: "datetime", iso };
}

/* ---------------------------------------------------------------------------------------- */
/* Numbers.                                                                                   */
/* ---------------------------------------------------------------------------------------- */

/**
 * CPython's `repr` for a finite double.
 *
 * `Number.prototype.toExponential()` with no argument yields the shortest digit string that
 * round-trips — the same digits CPython's `float_repr` computes — so only the LAYOUT has to be
 * re-derived. CPython (`format_float_short`, mode `r`) uses exponent notation when the decimal
 * point position `decpt` satisfies `decpt <= -4 || decpt > 16`, always writes at least one
 * fractional digit otherwise (`Py_DTSF_ADD_DOT_0`), and pads the exponent to two digits.
 */
function floatRepr(value: number): string {
  if (Number.isNaN(value) || !Number.isFinite(value)) {
    throw new CanonicalizationError(
      `non-finite float ${value} has no canonical JSON form; NaN and the infinities cannot ` +
        "be hashed deterministically",
    );
  }
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";

  const [mantissa, exponent] = value.toExponential().split("e");
  const sign = mantissa.startsWith("-") ? "-" : "";
  const digits = mantissa.replace("-", "").replace(".", "");
  // `value === 0.digits x 10 ** decpt`, which is how CPython addresses the decimal point.
  const decpt = Number(exponent) + 1;

  if (decpt <= -4 || decpt > 16) {
    const head = digits.slice(0, 1);
    const tail = digits.slice(1);
    const power = decpt - 1;
    const powerSign = power < 0 ? "-" : "+";
    return `${sign}${head}${tail ? `.${tail}` : ""}e${powerSign}${String(Math.abs(power)).padStart(2, "0")}`;
  }
  if (decpt <= 0) return `${sign}0.${"0".repeat(-decpt)}${digits}`;
  if (decpt >= digits.length) {
    return `${sign}${digits}${"0".repeat(decpt - digits.length)}.0`;
  }
  return `${sign}${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
}

/**
 * An integer, as JSON text.
 *
 * A `number` beyond `Number.MAX_SAFE_INTEGER` is refused rather than written: past 2^53 a
 * double no longer names one integer, and the Python value it is supposed to match is exact.
 * `bigint` is the way to say a large integer, which is what the `large_integer` vector does.
 */
function integerText(value: number): string {
  if (!Number.isSafeInteger(value)) {
    throw new CanonicalizationError(
      `integer ${value} is outside the safe range and cannot be written exactly; pass a ` +
        "bigint for an integer beyond 2**53, or float() if it was always a float",
    );
  }
  // `-0` is an integer zero here; the negative zero of the FLOAT rule is `float(-0)`.
  return String(value === 0 ? 0 : value);
}

/* ---------------------------------------------------------------------------------------- */
/* Strings, decimals and timestamps.                                                          */
/* ---------------------------------------------------------------------------------------- */

/**
 * A JSON string, escaped exactly as `json.dumps(..., ensure_ascii=False)` escapes it.
 *
 * `JSON.stringify` escapes the same set — the two mandatory characters and the C0 controls,
 * with `\b \f \n \r \t` spelled short — and leaves every other character, including DEL and
 * every non-ASCII one, as itself. The one divergence is a LONE SURROGATE, which `JSON.stringify`
 * escapes and `TextEncoder` would silently replace with U+FFFD while Python raises. It is
 * refused here so that the two implementations cannot disagree in silence.
 */
function stringText(value: string): string {
  for (const character of value) {
    const code = character.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) {
      throw new CanonicalizationError(
        "string contains an unpaired surrogate, which has no UTF-8 encoding; the canonical " +
          "form is UTF-8 bytes and a replacement character would silently change the digest",
      );
    }
  }
  return JSON.stringify(value);
}

/** Only finite decimals: `is_finite()` is the Python guard and this is the same one. */
function decimalText(digits: string): string {
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(digits)) {
    throw new CanonicalizationError(
      `decimal ${JSON.stringify(digits)} is not a finite decimal literal; NaN and the ` +
        "infinities cannot be hashed deterministically, and no other spelling is admitted",
    );
  }
  return stringText(digits);
}

const TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(?:(Z|z)|([+-])(\d{2}):?(\d{2}))$/;

/**
 * RFC 3339 in UTC with a literal `Z`, which is `astimezone(UTC).isoformat()` with `+00:00`
 * replaced — including Python's rule that microseconds appear only when they are not zero.
 *
 * The offset is applied to the calendar fields rather than to a `Date`, so that a timestamp
 * carrying microseconds survives: `Date` holds milliseconds and would truncate the last three
 * digits of a value whose digest depends on them.
 */
function dateTimeText(iso: string): string {
  const match = TIMESTAMP.exec(iso.trim());
  if (!match) {
    throw new CanonicalizationError(
      `timestamp ${JSON.stringify(iso)} is naive or unparseable; timestamps must carry an ` +
        "offset so that they name an instant (registry §3.1)",
    );
  }
  const [, year, month, day, hour, minute, second, fraction, , offsetSign, offsetHours, offsetMinutes] =
    match;
  const offsetSeconds = offsetSign
    ? (offsetSign === "-" ? -1 : 1) * (Number(offsetHours) * 3600 + Number(offsetMinutes) * 60)
    : 0;
  const instant = new Date(0);
  instant.setUTCFullYear(Number(year), Number(month) - 1, Number(day));
  instant.setUTCHours(Number(hour), Number(minute), Number(second), 0);
  const utc = new Date(instant.getTime() - offsetSeconds * 1000);
  const micros = (fraction ?? "").slice(1).padEnd(6, "0").slice(0, 6);
  return stringText(`${formatUtc(utc)}${micros === "000000" ? "" : `.${micros}`}Z`);
}

/** `YYYY-MM-DDTHH:MM:SS` from an instant, with the year zero-padded the way Python pads it. */
function formatUtc(value: Date): string {
  const pad = (part: number, width = 2) => String(part).padStart(width, "0");
  return (
    `${pad(value.getUTCFullYear(), 4)}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}` +
    `T${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}:${pad(value.getUTCSeconds())}`
  );
}

/* ---------------------------------------------------------------------------------------- */
/* Objects.                                                                                   */
/* ---------------------------------------------------------------------------------------- */

/**
 * Compare two keys by Unicode code point, which is Python's native string ordering.
 *
 * `"😀" < "ｽ"` is TRUE in JavaScript and false in Python, because the comparison walks UTF-16
 * code units and a surrogate (0xD83D) is below U+FF7D. Iterating with `for...of` walks code
 * points instead, which is the whole fix.
 */
function compareCodePoints(left: string, right: string): number {
  const leftPoints = [...left];
  const rightPoints = [...right];
  const shared = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < shared; index += 1) {
    const difference = leftPoints[index].codePointAt(0)! - rightPoints[index].codePointAt(0)!;
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

/** True for `{...}` and `Object.create(null)`, false for every class instance and exotic. */
function isPlainObject(value: object): value is Record<string, CanonicalValue> {
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function tagged(value: object): CanonicalTagged | null {
  const kind = (value as { canonicalKind?: unknown }).canonicalKind;
  return kind === "float" || kind === "decimal" || kind === "datetime"
    ? (value as CanonicalTagged)
    : null;
}

/** The canonical text of one value. The single place the JSON knobs are set. */
function write(value: CanonicalValue): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return stringText(value);
  if (typeof value === "bigint") return String(value);
  if (typeof value === "number") {
    if (Number.isNaN(value) || !Number.isFinite(value)) {
      throw new CanonicalizationError(
        `non-finite float ${value} has no canonical JSON form; NaN and the infinities cannot ` +
          "be hashed deterministically",
      );
    }
    // An integral `number` is an integer and a non-integral one can only be a float, so the
    // ambiguity `float()` exists for is exactly "an integral value that was always a float".
    return Number.isInteger(value) ? integerText(value) : floatRepr(value);
  }
  if (typeof value === "object") {
    if (value instanceof Date) {
      if (Number.isNaN(value.getTime())) {
        throw new CanonicalizationError("Invalid Date names no instant and cannot be hashed");
      }
      // A `Date` is always an instant, so it can never be the naive value Python refuses.
      return dateTimeText(value.toISOString());
    }
    const tag = tagged(value);
    if (tag) {
      if (tag.canonicalKind === "float") return floatRepr(tag.value);
      if (tag.canonicalKind === "decimal") return decimalText(tag.digits);
      return dateTimeText(tag.iso);
    }
    if (Array.isArray(value)) return `[${value.map(write).join(",")}]`;
    if (isPlainObject(value)) return writeObject(value);
  }
  throw new CanonicalizationError(
    `${describe(value)} has no canonical JSON form; convert it in the contract before hashing`,
  );
}

function writeObject(value: Record<string, CanonicalValue>): string {
  const keys = Object.keys(value).sort(compareCodePoints);
  const parts = keys.map((key) => `${stringText(key)}:${write(value[key])}`);
  return `{${parts.join(",")}}`;
}

function describe(value: unknown): string {
  if (value === undefined) return "undefined";
  if (typeof value !== "object" || value === null) return typeof value;
  return value.constructor?.name ?? "object";
}

/* ---------------------------------------------------------------------------------------- */
/* The public surface.                                                                        */
/* ---------------------------------------------------------------------------------------- */

/** The canonical text for `value`. `canonicalJson` is this, encoded as UTF-8. */
export function canonicalText(value: CanonicalValue): string {
  return write(value);
}

/** The canonical UTF-8 JSON bytes for `value` (ADR-056). */
export function canonicalJson(value: CanonicalValue): Uint8Array {
  return new TextEncoder().encode(write(value));
}

/**
 * The bytes a `content_hash` is computed over: the canonical form minus the excluded keys.
 *
 * Exposed beside {@link contentHash} because it is synchronous and `crypto.subtle` is not, so
 * a caller with its own digest — a test using `node:crypto`, a worker with a streaming hasher —
 * can hash exactly what this module would have hashed rather than reimplementing the exclusion.
 */
export function contentHashInput(
  value: CanonicalValue,
  exclude: readonly string[] = DEFAULT_HASH_EXCLUSIONS,
): Uint8Array {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    !(value instanceof Date) &&
    !tagged(value) &&
    isPlainObject(value) &&
    exclude.length
  ) {
    const dropped = new Set(exclude);
    const reduced = Object.fromEntries(
      Object.entries(value).filter(([key]) => !dropped.has(key)),
    );
    return new TextEncoder().encode(write(reduced));
  }
  return canonicalJson(value);
}

/**
 * The SHA-256 hex digest of `value` in canonical form, through the Web Crypto API.
 *
 * `exclude` names TOP-LEVEL keys dropped before hashing, defaulting to `content_hash` so that a
 * contract can carry the digest of its own body. Nested `content_hash` fields are untouched: a
 * reference's digest is part of what the outer contract commits to.
 */
export async function contentHash(
  value: CanonicalValue,
  options: { readonly exclude?: readonly string[] } = {},
): Promise<string> {
  const input = contentHashInput(value, options.exclude ?? DEFAULT_HASH_EXCLUSIONS);
  const digest = await crypto.subtle.digest("SHA-256", input as unknown as BufferSource);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
