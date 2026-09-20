// Сверка браузерной версии с версией на Python.
//
// Обе должны давать одни и те же числа на одном журнале. Если разойдутся
// — значит страница показывает посетителю не то, что мы измеряли, и
// верить ей нельзя.
import { readFileSync } from "node:fs";
import { analyse, parseLog } from "./loadlens.js";

const path = process.argv[2];
const a = analyse(parseLog(readFileSync(path, "utf-8")));
console.log(JSON.stringify({
  обращений: a.total,
  ошибка_по_маршруту: +a.routeError.toFixed(1),
  ошибка_по_параметру: +a.paramError.toFixed(1),
  доля_времени_у_тяжёлых: +a.heavyShareOfTime.toFixed(1),
  маршруты: a.routes.map((r) => ({
    маршрут: r.route,
    обращений: r.count,
    p50: +r.p50.toFixed(1),
    p99: +r.p99.toFixed(1),
    разброс: +r.spread.toFixed(1),
    параметр: r.bestParam,
    показатель: +r.exponent.toFixed(2),
    объясняет: +(r.explained * 100).toFixed(1),
    много_признаков_объясняет: +(r.multiExplained * 100).toFixed(1),
    отложенная_один: +r.holdoutParamError.toFixed(1),
    отложенная_все: +r.holdoutMultiError.toFixed(1),
  })),
}, null, 2));
