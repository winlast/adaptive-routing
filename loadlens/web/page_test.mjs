// Проверка собранной страницы: берём её собственный скрипт, подменяем
// браузерное окружение заглушками и смотрим, что отчёт построился.
import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("loadlens/web/index.html", "utf-8");
const m = html.match(/<script type="module">([\s\S]*?)<\/script>/);
if (!m) { console.log("СКРИПТ НЕ НАЙДЕН"); process.exit(1); }

const stubs = new Map();
const makeEl = () => ({
  innerHTML: "", textContent: "", href: "", classList: { add(){}, remove(){}, contains:()=>false },
  addEventListener(){}, scrollIntoView(){}, click(){},
  set onclick(v){}, set onchange(v){},
});
globalThis.document = {
  getElementById(id) {
    if (!stubs.has(id)) stubs.set(id, makeEl());
    return stubs.get(id);
  },
};

const code = m[1] +
  "\n;globalThis.__handle = handle;globalThis.__renderMoney = renderMoney;";
writeFileSync("/tmp/page_module.mjs", code);
await import("/tmp/page_module.mjs");

globalThis.__handle(
  readFileSync("loadlens/demo_access.log", "utf-8"));
const out = stubs.get("report").innerHTML;
console.log("длина отчёта:", out.length);
if (out.length < 500) { console.log("ОТЧЁТ ПУСТ"); process.exit(1); }

// Обезличенная сводка: проверяем, что она собралась и что в неё не
// попало ничего, кроме чисел.
const share = stubs.get("share-text").textContent;
console.log("длина сводки:", share.length);
if (!share.includes("разбросы_внутри_маршрутов")) {
  console.log("СВОДКА НЕ СОБРАНА"); process.exit(1);
}
for (const leak of ["/api/", "limit", "http"]) {
  if (share.includes(leak)) {
    console.log("В СВОДКУ ПОПАЛО ЛИШНЕЕ: " + leak); process.exit(1);
  }
}
console.log("сводка обезличена: адресов и параметров нет");
for (const probe of ["/api/search", "разброс внутри маршрута",
                     "ошибка оценки по маршруту", "Вывод"]) {
  console.log((out.includes(probe) ? "есть  " : "НЕТ   ") + probe);
}
// Денежный разбор: счёт клиента распределяется по маршрутам. Поле
// пустое — блока быть не должно; заполнено — должны появиться рубли.
if (stubs.get("money").innerHTML !== "") {
  console.log("ДЕНЬГИ ПОКАЗАНЫ БЕЗ УКАЗАННОГО СЧЁТА"); process.exit(1);
}
stubs.get("bill").value = "150000";
globalThis.__renderMoney();
const money = stubs.get("money").innerHTML;
if (!money.includes("\u20bd")) {
  console.log("ДЕНЕЖНЫЙ БЛОК НЕ СОБРАН"); process.exit(1);
}
const plain = money.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
console.log("деньги по счёту 150 000:", plain.slice(0, 120).trim());

console.log("\nвыдержка:\n" +
  out.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 420));
