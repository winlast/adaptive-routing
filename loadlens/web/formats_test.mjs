// Те же образцы форматов, что проверяет loadlens/check_formats.py.
// Браузерная версия обязана читать их так же: расхождение означает, что
// посетитель страницы увидит не то, что показывает консольная версия.
import { readFileSync, readdirSync } from "node:fs";
import { parseLog, diagnoseLog } from "./loadlens.js";

const DIR = "loadlens/formats";
const EXPECTED = [
  { route: "/api/search", limit: 10, ms: 12 },
  { route: "/api/search", limit: 900, ms: 740 },
];
const REFUSALS = { "nginx_no_time.log": "no-time" };
const TOL = 0.5;

let problems = 0;
for (const name of readdirSync(DIR).sort()) {
  if (name.endsWith(".md")) continue;
  const text = readFileSync(`${DIR}/${name}`, "utf-8");
  const got = parseLog(text);

  if (name in REFUSALS) {
    const why = diagnoseLog(text);
    if (got.length || why !== REFUSALS[name]) {
      console.log(`  ✗ ${name}: ожидался отказ «${REFUSALS[name]}», вышло `
        + `${got.length} обращений / «${why}»`);
      problems++;
    } else {
      console.log(`  ${name.padEnd(24)} отказ с объяснением — как и нужно`);
    }
    continue;
  }

  if (got.length !== EXPECTED.length) {
    console.log(`  ✗ ${name}: разобрано ${got.length}, ожидалось `
      + EXPECTED.length);
    problems++;
    continue;
  }
  const errs = [];
  got.forEach((r, i) => {
    const e = EXPECTED[i];
    if (r.path !== e.route) errs.push(`маршрут ${r.path}`);
    if (r.params.limit !== e.limit) errs.push(`limit ${r.params.limit}`);
    if (Math.abs(r.durationMs - e.ms) > TOL) errs.push(`${r.durationMs} мс`);
  });
  if (errs.length) {
    console.log(`  ✗ ${name}: ${errs.join("; ")}`);
    problems++;
  } else {
    console.log(`  ${name.padEnd(24)} разобран верно`);
  }
}
if (problems) {
  console.log(`ФОРМАТЫ В БРАУЗЕРЕ: ${problems} расхождений`);
  process.exit(1);
}
console.log("браузерная версия читает все форматы так же");
