// Journey table for the CI job summary ($GITHUB_STEP_SUMMARY), from Playwright's JUnit files.
// Usage: node e2e/summary.mjs TITLE FILE...   (a missing file is reported, never fatal)
import { existsSync, readFileSync } from "node:fs";

const [title = "Parcours de bout en bout", ...files] = process.argv.slice(2);

function unescape(text) {
  return text
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

const cell = (text) => text.replace(/\|/g, "\\|").replace(/\s+/g, " ").trim();
const rows = [];
const missing = [];
for (const file of files) {
  if (!existsSync(file)) {
    missing.push(file);
    continue;
  }
  const xml = readFileSync(file, "utf8");
  for (const match of xml.matchAll(/<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g)) {
    const attributes = Object.fromEntries(
      [...match[1].matchAll(/(\w+)="([^"]*)"/g)].map(([, key, value]) => [key, unescape(value)]),
    );
    const body = match[3] ?? "";
    const status = /<(failure|error)\b/.test(body) ? "❌ échec" : /<skipped\b/.test(body) ? "⏭️ ignoré" : "✅ réussi";
    // The JUnit name is "<file> › <title>" or the title alone, depending on the version.
    const name = attributes.name ?? "";
    const spec = attributes.classname ?? name.split(" › ")[0];
    rows.push({ spec, test: name.split(" › ").at(-1) ?? name, status, time: Number(attributes.time ?? 0) });
  }
}

const lines = [`### ${title}`, ""];
if (rows.length) {
  const passed = rows.filter((row) => row.status.startsWith("✅")).length;
  const failed = rows.filter((row) => row.status.startsWith("❌")).length;
  lines.push(`${passed} réussi(s), ${failed} échec(s), ${rows.length - passed - failed} ignoré(s).`, "");
  lines.push("| Spec | Parcours | Résultat | Durée |", "| --- | --- | --- | --- |");
  for (const row of rows) {
    lines.push(`| ${cell(row.spec)} | ${cell(row.test)} | ${row.status} | ${row.time.toFixed(1)} s |`);
  }
} else {
  lines.push("Aucun résultat JUnit (les tests n'ont pas démarré ?).");
}
for (const file of missing) lines.push("", `Fichier absent : \`${file}\`.`);
console.log(lines.join("\n"));
