// Проверка собранной страницы: берём её собственный скрипт, подменяем
// браузерное окружение заглушками и смотрим, что отчёт построился.
import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("loadlens/web/index.html", "utf-8");
const m = html.match(/<script type="module">([\s\S]*?)<\/script>/);
if (!m) { console.log("СКРИПТ НЕ НАЙДЕН"); process.exit(1); }

const stubs = new Map();
const makeEl = () => ({
  innerHTML: "", classList: { add(){}, remove(){}, contains:()=>false },
  addEventListener(){}, scrollIntoView(){}, click(){},
  set onclick(v){}, set onchange(v){},
});
globalThis.document = {
  getElementById(id) {
    if (!stubs.has(id)) stubs.set(id, makeEl());
    return stubs.get(id);
  },
};

const code = m[1] + "\n;globalThis.__handle = handle;";
writeFileSync("/tmp/page_module.mjs", code);
await import("/tmp/page_module.mjs");

globalThis.__handle(
  readFileSync("loadlens/demo_access.log", "utf-8"));
const out = stubs.get("report").innerHTML;
console.log("длина отчёта:", out.length);
for (const probe of ["/api/search", "разброс внутри маршрута",
                     "ошибка оценки по маршруту", "Вывод"]) {
  console.log((out.includes(probe) ? "есть  " : "НЕТ   ") + probe);
}
console.log("\nвыдержка:\n" +
  out.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 420));
