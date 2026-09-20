// Разбор журнала обращений — тот же анализ, что в loadlens/loadlens.py,
// но выполняемый в браузере.
//
// Перенос сделан ради одного свойства: журнал обращений — чувствительные
// данные, по нему видно, кто что запрашивал. Здесь он никуда не
// отправляется. Файл читается страницей, разбирается на месте, и ни один
// байт не уходит с машины: сервера у этой страницы попросту нет.
//
// Числа обязаны совпадать с версией на Python. Проверка —
// loadlens/web/verify.mjs, она прогоняет тот же журнал и сравнивает.

const ID_SEGMENT = /^(\d+|[0-9a-f]{8,}|[0-9a-f-]{32,})$/i;

const NGINX = /"([A-Z]+)\s+(\S+)\s+HTTP\/[\d.]+"[\s\S]*?(\d+\.\d{3})\s*$/;

export function normalizePath(raw) {
  // Относительный адрес — базовый узел не важен, нужны путь и параметры.
  let url;
  try {
    url = new URL(raw, "http://x");
  } catch {
    return { route: raw, params: {} };
  }
  const route =
    url.pathname
      .split("/")
      .map((seg) => (ID_SEGMENT.test(seg) ? "{id}" : seg))
      .join("/") || "/";

  const params = {};
  for (const [key, value] of url.searchParams) {
    if (value === "") continue;
    const num = Number(value);
    if (value.trim() !== "" && Number.isFinite(num)) {
      params[key] = num;
      continue;
    }
    // Нечисловой параметр тоже влияет на стоимость: сортировка, фильтр,
    // набор запрашиваемых полей. Числом его не выразить, но само его
    // присутствие — уже признак, и прокси его видит.
    params[`есть:${key}`] = 1;
    if (value.includes(",")) {
      params[`число:${key}`] = value.split(",").length;
    }
  }
  return { route, params };
}

export function parseLog(text) {
  const requests = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let url = null;
    let seconds = null;

    const m = NGINX.exec(line);
    if (m) {
      url = m[2];
      seconds = Number(m[3]);
    } else if (line.trimStart().startsWith("{")) {
      try {
        const obj = JSON.parse(line);
        url = obj.request_uri ?? obj.uri ?? obj.path ?? obj.url ?? null;
        const t =
          obj.request_time ?? obj.duration ?? obj.latency ?? obj.elapsed;
        if (t !== undefined) seconds = Number(t);
      } catch {
        continue;
      }
    }
    if (!url || seconds === null || !Number.isFinite(seconds)) continue;
    const { route, params } = normalizePath(url);
    requests.push({ path: route, params, durationMs: seconds * 1000 });
  }
  return requests;
}

export function percentile(values, p) {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(Math.floor(ordered.length * p), ordered.length - 1)];
}

export function fitPowerLaw(xs, ys) {
  const n = xs.length;
  if (n < 8) return { a: 0, b: 0, r2: 0 };
  const lx = xs.map((x) => Math.log(Math.max(x, 1e-9)));
  const ly = ys.map((y) => Math.log(Math.max(y, 1e-9)));
  const mx = lx.reduce((s, v) => s + v, 0) / n;
  const my = ly.reduce((s, v) => s + v, 0) / n;
  const sxx = lx.reduce((s, v) => s + (v - mx) ** 2, 0);
  if (sxx < 1e-12) return { a: 0, b: 0, r2: 0 };
  let sxy = 0;
  for (let i = 0; i < n; i++) sxy += (lx[i] - mx) * (ly[i] - my);
  const b = sxy / sxx;
  const a = my - b * mx;
  const ssTot = ly.reduce((s, v) => s + (v - my) ** 2, 0);
  let ssRes = 0;
  for (let i = 0; i < n; i++) ssRes += (ly[i] - (a + b * lx[i])) ** 2;
  const r2 = ssTot > 1e-12 ? 1 - ssRes / ssTot : 0;
  return { a: Math.exp(a), b, r2: Math.max(0, r2) };
}

// Оставляет обращения, меньше других пострадавшие от очереди: очередь
// способна время лишь увеличить, поэтому в каждой группе близких значений
// параметра самые быстрые ближе всего к собственной стоимости запроса.
export function unqueued(pairs, keep = 0.25, buckets = 12) {
  if (pairs.length < 16) return pairs;
  const xs = pairs.map((p) => p[0]);
  const lo = Math.log(Math.max(Math.min(...xs), 1e-9));
  const hi = Math.log(Math.max(...xs));
  if (hi - lo < 1e-9) return pairs;
  const grouped = new Map();
  for (const [x, y] of pairs) {
    const idx = Math.min(
      Math.floor(((Math.log(Math.max(x, 1e-9)) - lo) / (hi - lo)) * buckets),
      buckets - 1,
    );
    if (!grouped.has(idx)) grouped.set(idx, []);
    grouped.get(idx).push([x, y]);
  }
  const out = [];
  for (const items of grouped.values()) {
    items.sort((p, q) => p[1] - q[1]);
    out.push(...items.slice(0, Math.max(1, Math.floor(items.length * keep))));
  }
  return out;
}

export function medianApe(predicted, actual) {
  const errors = [];
  for (let i = 0; i < actual.length; i++) {
    if (actual[i] <= 0) continue;
    errors.push((Math.abs(predicted[i] - actual[i]) / actual[i]) * 100);
  }
  if (!errors.length) return NaN;
  errors.sort((a, b) => a - b);
  const mid = errors.length >> 1;
  return errors.length % 2
    ? errors[mid]
    : (errors[mid - 1] + errors[mid]) / 2;
}

function solve(a, b) {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let r = col; r < n; r++) {
      if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    }
    if (Math.abs(m[pivot][col]) < 1e-12) return null;
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return Array.from({ length: n }, (_, i) => m[i][n] / m[i][i]);
}

// Стоимость редко определяется одним числом: на неё влияют сразу
// несколько вещей — сколько записей просят, нужна ли сортировка, сколько
// полей вернуть. Подгонка ведётся в логарифмах, поэтому произведение
// степеней превращается в сумму.
export function fitMulti(rows, names) {
  if (rows.length < Math.max(12, 3 * (names.length + 1))) return null;
  const vec = (params) => {
    const out = [1];
    for (const name of names) {
      const value = params[name] ?? 0;
      out.push(name.startsWith("есть:") ? value : Math.log(Math.max(value, 1)));
    }
    return out;
  };
  const k = names.length + 1;
  const ata = Array.from({ length: k }, () => new Array(k).fill(0));
  const atb = new Array(k).fill(0);
  const ys = [];
  for (const [params, duration] of rows) {
    const x = vec(params);
    const y = Math.log(Math.max(duration, 1e-9));
    ys.push(y);
    for (let i = 0; i < k; i++) {
      atb[i] += x[i] * y;
      for (let j = 0; j < k; j++) ata[i][j] += x[i] * x[j];
    }
  }
  // Признаки бывают почти коллинеарны, без этого система вырождается.
  for (let i = 1; i < k; i++) ata[i][i] += 1e-6;
  const coef = solve(ata, atb);
  if (!coef) return null;

  const mean = ys.reduce((s, v) => s + v, 0) / ys.length;
  const ssTot = ys.reduce((s, v) => s + (v - mean) ** 2, 0);
  let ssRes = 0;
  for (let i = 0; i < rows.length; i++) {
    const x = vec(rows[i][0]);
    let pred = 0;
    for (let j = 0; j < k; j++) pred += coef[j] * x[j];
    ssRes += (ys[i] - pred) ** 2;
  }
  const r2 = ssTot > 1e-12 ? 1 - ssRes / ssTot : 0;
  const model = { "(свободный член)": coef[0] };
  names.forEach((name, i) => (model[name] = coef[i + 1]));
  return { model, r2: Math.max(0, Math.min(1, r2)) };
}

export function predictMulti(model, params) {
  let total = model["(свободный член)"];
  for (const [name, coef] of Object.entries(model)) {
    if (name === "(свободный член)") continue;
    const value = params[name] ?? 0;
    total += coef * (name.startsWith("есть:") ? value : Math.log(Math.max(value, 1)));
  }
  return Math.exp(total);
}

export function analyse(requests, heavyPercentile = 0.9) {
  const byRoute = new Map();
  for (const r of requests) {
    if (!byRoute.has(r.path)) byRoute.set(r.path, []);
    byRoute.get(r.path).push(r);
  }
  const totalMs = requests.reduce((s, r) => s + r.durationMs, 0);
  const routes = [];
  const allRoutePred = [];
  const allParamPred = [];
  const allActual = [];

  for (const [route, group] of byRoute) {
    const durations = group.map((r) => r.durationMs);
    const p50 = percentile(durations, 0.5);
    const report = {
      route,
      count: group.length,
      p50,
      p95: percentile(durations, 0.95),
      p99: percentile(durations, 0.99),
      spread: p50 > 0 ? percentile(durations, 0.99) / p50 : 0,
      shareOfTime: totalMs ? (durations.reduce((s, v) => s + v, 0) / totalMs) * 100 : 0,
      bestParam: null,
      exponent: 0,
      explained: 0,
      multi: null,
      multiExplained: 0,
      holdoutParamError: 0,
      holdoutMultiError: 0,
    };

    const candidates = new Map();
    for (const r of group) {
      for (const [key, value] of Object.entries(r.params)) {
        if (value > 0) {
          if (!candidates.has(key)) candidates.set(key, []);
          candidates.get(key).push([value, r.durationMs]);
        }
      }
    }
    let best = null;
    for (const [key, pairs] of candidates) {
      if (pairs.length < Math.max(8, group.length * 0.3)) continue;
      const clean = unqueued(pairs);
      const fit = fitPowerLaw(clean.map((p) => p[0]), clean.map((p) => p[1]));
      if (Math.abs(fit.b) < 0.05) continue;
      if (!best || fit.r2 > best.fit.r2) best = { key, fit, clean };
    }

    let evalY;
    let routePred;
    let paramPred;
    if (best) {
      report.bestParam = best.key;
      report.exponent = best.fit.b;
      report.explained = best.fit.r2;
      evalY = best.clean.map((p) => p[1]);
      const base = evalY.reduce((s, v) => s + v, 0) / evalY.length;
      routePred = evalY.map(() => base);
      paramPred = best.clean.map((p) => best.fit.a * p[0] ** best.fit.b);
    } else {
      evalY = unqueued(durations.map((d) => [1, d])).map((p) => p[1]);
      const base = evalY.reduce((s, v) => s + v, 0) / evalY.length;
      routePred = evalY.map(() => base);
      paramPred = routePred;
    }
    report.routeError = medianApe(routePred, evalY);
    report.paramError = medianApe(paramPred, evalY);

    const names = [...new Set(group.flatMap((r) => Object.keys(r.params)))].sort();
    if (best && names.length > 1) {
      const rows = group.map((r) => [r.params, r.durationMs]);
      const train = rows.filter((_, i) => i % 2 === 0);
      const test = rows.filter((_, i) => i % 2 === 1);
      const fitted = fitMulti(train, names);
      const single = fitMulti(train, [best.key]);
      if (fitted && single && test.length) {
        report.multi = fitted.model;
        report.multiExplained = fitted.r2;
        const actual = test.map((t) => t[1]);
        report.holdoutMultiError = medianApe(
          test.map((t) => predictMulti(fitted.model, t[0])), actual);
        report.holdoutParamError = medianApe(
          test.map((t) => predictMulti(single.model, t[0])), actual);
      }
    }

    routes.push(report);
    allRoutePred.push(...routePred);
    allParamPred.push(...paramPred);
    allActual.push(...evalY);
  }

  routes.sort((a, b) => b.shareOfTime - a.shareOfTime);
  const threshold = percentile(requests.map((r) => r.durationMs), heavyPercentile);
  const heavy = requests.filter((r) => r.durationMs >= threshold);

  return {
    total: requests.length,
    totalMs,
    routes,
    routeError: medianApe(allRoutePred, allActual),
    paramError: medianApe(allParamPred, allActual),
    heavyShareOfRequests: (heavy.length / requests.length) * 100,
    heavyShareOfTime: totalMs
      ? (heavy.reduce((s, r) => s + r.durationMs, 0) / totalMs) * 100
      : 0,
  };
}
