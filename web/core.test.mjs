import test from "node:test";
import assert from "node:assert/strict";
import {
  MY_ID, SOURCE_URL, parseCSV, parseSheet, rankStudents, validateAliases,
  validateStudentId, displayName, validateSnapshot, compareSnapshots, fetchGrades,
} from "./core.js";

const headers = ["N° Alumno", "AC1", "AC2", "Nota Cátedra", "I1", "I2", "Examen", "Nota\nE. Escritas", "Nota\nPresentación", "Bonificación\nECA", "Nota\nFinal", "Nota\nBanner"];
const rows = [
  [MY_ID, "5,9", "0", "5,41", "6,2", "0", "0", "1,86", "3,28", "0", "3,28", "3,3"],
  ["2666237J", "0", "0", "1", "3,8", "0", "0", "1,14", "1,2", "0", "1,2", "1,2"],
];
const csv = (data = rows, cols = headers) => [["", "Cátedra"], cols, ...data].map((r) => r.map((v) => '"' + v.replaceAll('"', '""') + '"').join(",")).join("\r\n");
const snapshot = () => ({ ...parseSheet(csv()), fetched_at: "2026-10-29T12:00:00.000Z", changed_at: "2026-10-29T12:00:00.000Z", previous_at: null, stale: false, error: null });

test("CSV handles BOM, escaped quotes, multiline fields and trailing blanks", () => {
  assert.deepEqual(parseCSV('\uFEFF"a,b","x""y"\r\n"a\nb",last\r\n'), [["a,b", 'x"y'], ["a\nb", "last"]]);
  assert.deepEqual(parseCSV("a,,"), [["a", "", ""]]);
  for (const bad of ['"unclosed', 'a"b,c', '"closed"x,c']) assert.throws(() => parseCSV(bad));
});

test("parses comma decimals and preserves alphanumeric IDs", () => {
  const parsed = parseSheet(csv());
  assert.equal(parsed.students[0].grades.catedra, 5.41);
  assert.equal(parsed.students[1].id, "2666237J");
  assert.equal(parsed.source_url, SOURCE_URL);
});

test("inactive all-zero columns are not in mean; individual zeros count", () => {
  const p = parseSheet(csv());
  assert.equal(p.metrics.find((m) => m.key === "ac2").available, false);
  assert.equal(p.students[0].grades.activities, 5.9);
  assert.equal(p.students[1].grades.activities, 0);
  const updated = structuredClone(rows);
  updated[0][2] = "4";
  assert.equal(parseSheet(csv(updated)).students[0].grades.activities, 4.95);
});

test("missing points are not zero and incomplete means stay null", () => {
  const updated = structuredClone(rows);
  updated[0][2] = "";
  updated[1][2] = "2";
  const p = parseSheet(csv(updated));
  assert.equal(p.students[0].grades.ac2, null);
  assert.equal(p.students[0].grades.activities, null);
  assert.equal(p.students[1].grades.activities, 1);
});

test("equivalent decimal sums tie despite binary floating point", () => {
  const data = structuredClone(rows);
  data[0][1] = "0,1"; data[0][2] = "0,2";
  data[1][1] = "0,3"; data[1][2] = "0";
  const ranked = rankStudents(parseSheet(csv(data)).students, "activities");
  assert.deepEqual(ranked.map((r) => r.rank), [1, 1]);
  assert.equal(ranked[0].value, 0.15);
});

test("bad source values or changed structure fail loudly", () => {
  const valid = csv();
  for (const bad of ["<html>login</html>", "nothing", valid.replace('"5,9"', '"NaN"'), valid.replace('"5,9"', '"-1"'), valid.replace('"5,9"', '"#REF!"'), valid.replace("2666237J", MY_ID), valid.replace("Nota Cátedra", "Different"), valid + "\n12345,1"]) {
    assert.throws(() => parseSheet(bad));
  }
});

test("competition ranking preserves ties, zero, order and input", () => {
  const students = [7, 6, 6, 5, 0, null, NaN, "8", true].map((grade, index) => ({ id: String(10000 + index), grades: { final: grade } }));
  const original = students.map((s) => s.id);
  assert.deepEqual(rankStudents(students, "final").map((r) => r.rank), [1, 2, 2, 4, 5]);
  assert.deepEqual(students.map((s) => s.id), original);
  assert.deepEqual(rankStudents(students, "missing"), []);
});

test("profile validation and alias defaults do not follow the selected user", () => {
  assert.equal(validateStudentId(" 2666237j "), "2666237J");
  for (const bad of [null, 12345, "bad!", "a".repeat(21)]) assert.throws(() => validateStudentId(bad));
  assert.equal(displayName(MY_ID, {}), "José Garrillo");
  assert.equal(displayName("12345", {}), "12345");
  assert.equal(displayName("constructor", {}), "constructor");
  assert.deepEqual(validateAliases({ [MY_ID]: " José Garrillo " }), { [MY_ID]: "José Garrillo" });
  for (const bad of [[], null, { bad: "Ana" }, { [MY_ID]: 1 }, { [MY_ID]: " " }, { [MY_ID]: "a".repeat(81) }, { [MY_ID]: "a\nb" }, { [MY_ID]: "a\u202eb" }]) assert.throws(() => validateAliases(bad));
});

test("snapshot validates full schema and rejects malformed caches", () => {
  assert.equal(validateSnapshot(snapshot()), true);
  const bad = [null, [], {}, { ...snapshot(), source_url: "https://evil.example" }];
  for (const key of ["changed_at", "students", "metrics", "previous_at"]) { const p = snapshot(); delete p[key]; bad.push(p); }
  const nonfinite = snapshot(); nonfinite.students[0].grades.final = Infinity; bad.push(nonfinite);
  const duplicate = snapshot(); duplicate.students.push(duplicate.students[0]); bad.push(duplicate);
  for (const p of bad) assert.equal(validateSnapshot(p), false);
});

test("deltas survive unchanged polling; metadata uses actual consultation dates", () => {
  const first = compareSnapshots(snapshot(), null);
  const fresh = snapshot(); fresh.fetched_at = "2026-10-29T13:00:00.000Z"; fresh.students[0].grades.final = 4;
  const changed = compareSnapshots(fresh, first);
  assert.equal(changed.students[0].changes.final, 0.72);
  assert.equal(changed.previous_at, first.fetched_at);
  const unchanged = structuredClone(changed); unchanged.fetched_at = "2026-10-29T14:00:00.000Z";
  const last = compareSnapshots(unchanged, changed);
  assert.equal(last.changed_at, changed.changed_at);
  assert.equal(last.fetched_at, unchanged.fetched_at);
  assert.equal(last.students[0].changes.final, 0.72);
  assert.equal(fresh.students[0].changes.final, undefined);
});

test("fetch uses public CSV only, no credentials, and rejects HTML", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(options.credentials, "omit");
      assert.ok(url.startsWith("https://docs.google.com/spreadsheets/"));
      return { ok: true, text: async () => csv() };
    };
    assert.equal(validateSnapshot(await fetchGrades()), true);
    globalThis.fetch = async () => ({ ok: true, text: async () => "<html>login</html>" });
    await assert.rejects(fetchGrades());
    globalThis.fetch = async () => ({ ok: false, status: 403 });
    await assert.rejects(fetchGrades(), /403/);
  } finally { globalThis.fetch = original; }
});
