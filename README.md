# Adaptive Routing — адаптивная маршрутизация между Flask и FastAPI

Прототип и экспериментальный стенд для проверки гипотезы: **даёт ли адаптивная маршрутизация HTTP-запросов между синхронным (Flask) и асинхронным (FastAPI) движками, основанная на обученном классификаторе, прирост пропускной способности и снижение задержки по сравнению с использованием одного движка?**

Коротко о результате: гипотеза (≥25% throughput, задержка в 1,8–2,2× ниже) **не подтвердилась** для операций длительностью ~100 мс — адаптивный шлюз оказался на 7–41% медленнее лучшего одиночного движка. Причина точно локализована экспериментально: не в качестве классификации (accuracy >95%, задержка решения <2 мс), а в архитектурных накладных расходах самого шлюза (доп. сетевой переход + однопоточная обработка решения). Подробности — в разделе [Результаты](#результаты) и в `data/`, `figures/`.

## Содержание

- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Воспроизведение эксперимента](#воспроизведение-эксперимента)
- [Результаты](#результаты)
- [API](#api)
- [Тесты](#тесты)
- [Известные ограничения](#известные-ограничения)
- [Стек технологий](#стек-технологий)
- [Лицензия](#лицензия)

## Архитектура

```
                 ┌──────────────────┐
  клиент  ─────► │  Gateway :9000   │
                 │  (FastAPI +      │
                 │   AdaptiveRouter)│
                 └────────┬─────────┘
                          │  load, io_intensity, cpu_usage
                          │  → PyTorch classifier → P(FastAPI)
              ┌───────────┴────────────┐
              ▼                        ▼
      ┌───────────────┐        ┌───────────────┐
      │  Flask :5000  │        │ FastAPI :8000 │
      │  (sync, sleep)│        │ (async, sleep)│
      └───────────────┘        └───────────────┘
```

Три независимых процесса: два backend-сервиса, эмулирующих I/O-операцию задержкой 100 мс, и шлюз, который на основе трёх признаков запроса (`load`, `io_intensity`, `cpu_usage`) через обученный классификатор решает, к какому backend'у направить конкретный запрос, и логирует каждое решение.

## Структура проекта

```
adaptive_routing/
├── main.py                  # точка входа: поднимает все 3 сервиса
├── router.py                # AdaptiveRouter + RouterClassifier (PyTorch)
├── flask_app.py              # backend #1 (синхронный)
├── fastapi_app.py            # backend #2 (асинхронный)
├── requirements.txt
├── data/                     # обучающие данные, веса модели, логи, CSV-результаты
│   ├── router_data.csv               # синтетический датасет для обучения
│   ├── router_model.pth              # веса обученной модели
│   ├── scaler.pkl                    # StandardScaler для признаков
│   ├── decisions_log.csv             # лог решений роутера (растёт во время работы)
│   ├── benchmark_results.csv         # результаты нагрузочного теста
│   ├── benchmark_mixed_results.csv   # результаты на смешанной нагрузке
│   ├── classification_report.txt     # accuracy / precision / recall модели
│   └── hypothesis_*.csv              # готовые таблицы для проверки гипотезы
├── figures/                  # графики (генерируются скриптами из data/)
│   ├── training_loss.png
│   ├── confusion_matrix.png
│   ├── heatmap_probability.png / heatmap_decision.png
│   ├── benchmark_comparison.png / benchmark_mixed_comparison.png
│   └── architecture_diagram.png
├── scripts/                  # весь вспомогательный тулинг
│   ├── generate_training_data.py     # генерация синтетического датасета
│   ├── train_model.py                # обучение классификатора
│   ├── generate_heatmap_data.py / plot_heatmap.py
│   ├── benchmark.py / plot_benchmark.py            # нагрузочный тест (hey)
│   ├── benchmark_mixed.py / plot_benchmark_mixed.py
│   ├── analyze_logs.py               # анализ decisions_log.csv
│   ├── analyze_routing_quality.py    # проверка корректности маршрутизации
│   ├── generate_architecture_diagram.py
│   ├── quick_populate_log.py         # синтетическое наполнение лога (для отладки)
│   └── test_concurrency.py           # изолированный тест производительности роутера
└── tests/
    └── test_router.py         # pytest: корректность AdaptiveRouter
```

## Установка

Требуется Python 3.12+, Linux/WSL2 (тестировалось на Ubuntu 24.04 под WSL2).

```bash
git clone https://github.com/winlast/adaptive-routing.git
cd adaptive-routing

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

Для нагрузочного тестирования дополнительно нужен независимый инструмент [`hey`](https://github.com/rakyll/hey):

```bash
sudo apt install hey
# либо: go install github.com/rakyll/hey@latest && export PATH=$PATH:~/go/bin
```

## Быстрый старт

Модель уже обучена и её веса лежат в `data/` — обучать заново не нужно для простого запуска.

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python main.py
```

> Переменные `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` обязательны — без них PyTorch создаёт избыточный пул фоновых потоков, который вызывает голодание event loop’а шлюза под нагрузкой (подробности — в `data/decisions_log.csv` и в статье проекта).

В другом терминале:

```bash
curl -X POST http://127.0.0.1:9000/route \
  -H "Content-Type: application/json" \
  -d '{"load": 150, "io_intensity": 0.25, "cpu_usage": 70}'
```

Переменные окружения для управления режимом работы:

| Переменная | Значения | По умолчанию | Назначение |
|---|---|---|---|
| `ROUTER_MODE` | `neural` \| `heuristic` | `neural` | какой алгоритм принимает решение |
| `AB_TEST_ENABLED` | `true` \| `false` | `false` | включить A/B-разбиение между режимами |
| `AB_TEST_HEURISTIC_SHARE` | 0.0–1.0 | `0.5` | доля трафика на эвристику при A/B |

## Воспроизведение эксперимента

Полный цикл от нуля до готовых графиков:

```bash
cd scripts

# 1. Данные и обучение модели (опционально — веса уже в репозитории)
python generate_training_data.py
python train_model.py

# 2. main.py должен быть запущен в отдельном терминале (см. "Быстрый старт")

# 3. Тепловая карта решений
python generate_heatmap_data.py
python plot_heatmap.py

# 4. Нагрузочное тестирование
python benchmark.py
python plot_benchmark.py
python benchmark_mixed.py
python plot_benchmark_mixed.py

# 5. Анализ логов и проверка качества маршрутизации
python analyze_logs.py
python analyze_routing_quality.py

# 6. Схема архитектуры для презентации
python generate_architecture_diagram.py
```

## Результаты

| Конкурентность | adaptive, RPS | лучший одиночный, RPS | Δ, % |
|---|---|---|---|
| 10 | 88,1 | 97,2 | −9,4 |
| 25 | 221,6 | 240,7 | −7,9 |
| 40 | 355,8 | 386,9 | −8,0 |
| 50 | 283,6 | 479,1 | −40,8 |

Полные данные — `data/hypothesis_throughput.csv`, `data/hypothesis_latency.csv`, `data/hypothesis_mixed_throughput.csv`. Графики — `figures/benchmark_comparison.png`, `figures/benchmark_mixed_comparison.png`.

Точность классификатора и матрица ошибок — `data/classification_report.txt`, `figures/confusion_matrix.png`.

## API

Gateway (`http://localhost:9000`):

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/route` | Принимает `{load, io_intensity, cpu_usage, ...}`, возвращает ответ backend'а + метаданные маршрутизации |
| `GET` | `/stats` | Агрегированная статистика по принятым решениям |
| `GET` | `/config` | Текущая конфигурация (режим, A/B) |

Backend'ы (`:5000`, `:8000`): `POST /process`, `GET /health`.

## Тесты

```bash
pytest tests/ -v
```

## Известные ограничения

- Flask/FastAPI backend работают на dev-серверах (Werkzeug, `uvicorn` без production-воркеров) — абсолютные цифры throughput не отражают production-производительность, только относительное сравнение архитектур при равных условиях.
- Gateway использует один процесс для принятия решения — при конкурентности выше ~50 упирается в одно ядро CPU (задокументированный источник расхождения с гипотезой).
- Датасет для обучения — синтетический, не отражает реальный трафик.
- Эксперимент проводился на одной машине (WSL2, 16 ядер) — сетевой оверхед не отделён от локальной конкуренции за CPU.

## Стек технологий

Python 3.12 · Flask 3 · FastAPI 0.111 · PyTorch 2 · scikit-learn · pandas · matplotlib / seaborn · httpx · uvicorn (uvloop + httptools) · pytest · [hey](https://github.com/rakyll/hey) (нагрузочное тестирование)

## Лицензия

MIT — см. [LICENSE](LICENSE).
