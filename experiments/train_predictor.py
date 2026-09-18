"""
Обучение модели предсказания латентности + проверка, нужна ли она вообще.

Вместе с нейросетью обучаются две более простые модели: линейная
регрессия и предсказание по среднему для пары «эндпоинт-воркер». Это не
формальность. Если линейная модель или обычное среднее дают ту же
точность, значит зависимость простая и нейросеть в задаче лишняя — ровно
та ошибка, которая была допущена в первой версии этой работы, когда
обучаемый классификатор воспроизводил единственное пороговое правило.

Сравнение делается на отложенной выборке по MAE в миллисекундах и
дополнительно по доле случаев, где модель правильно угадала лучшего из
трёх воркеров.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from core.policies import WORKERS
from core.predictor import LatencyNet, LatencyPredictor, encode
from core.workload import ENDPOINT_NAMES

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_PATH = DATA_DIR / "requests_log.csv"

EPOCHS = 120
BATCH_SIZE = 64
LR = 0.01
SEED = 42


def load_dataset() -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    df = pd.read_csv(LOG_PATH)
    df = df[(df["error"].isna()) | (df["error"] == "")]
    df = df[df["latency_ms"] > 0]

    rows = [
        encode(r.endpoint, r.payload_bytes,
               {"sync": r.inflight_sync, "async": r.inflight_async,
                "process": r.inflight_process},
               r.worker,
               {"sync": r.work_sync, "async": r.work_async,
                "process": r.work_process})
        for r in df.itertuples()
    ]
    X = np.array(rows, dtype=np.float32)
    y = np.log(df["latency_ms"].to_numpy(dtype=np.float32))
    return X, y, df


def evaluate_ms(y_true_log: np.ndarray, y_pred_log: np.ndarray) -> float:
    """MAE в миллисекундах, а не в логарифмах — так интерпретируемее."""
    return mean_absolute_error(np.exp(y_true_log), np.exp(y_pred_log))


def train_network(X_train, y_train, X_test, y_test) -> tuple[LatencyNet, float]:
    torch.manual_seed(SEED)
    model = LatencyNet(n_features=X_train.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    criterion = nn.MSELoss()

    X_t = torch.FloatTensor(X_train)
    y_t = torch.FloatTensor(y_train)
    dataset = torch.utils.data.TensorDataset(X_t, y_t)
    loader = torch.utils.data.DataLoader(dataset, batch_size=BATCH_SIZE,
                                         shuffle=True)

    for epoch in range(EPOCHS):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()

    model.eval()
    with torch.no_grad():
        pred = model(torch.FloatTensor(X_test)).numpy()
    return model, evaluate_ms(y_test, pred)


def baseline_group_mean(df_train: pd.DataFrame, df_test: pd.DataFrame) -> float:
    """Среднее время по паре «эндпоинт-воркер» — простейшая модель."""
    table = df_train.groupby(["endpoint", "worker"])["latency_ms"].mean()
    global_mean = df_train["latency_ms"].mean()
    preds = [
        table.get((r.endpoint, r.worker), global_mean)
        for r in df_test.itertuples()
    ]
    return mean_absolute_error(df_test["latency_ms"], preds)


def best_worker_accuracy(model: LatencyNet, scaler, df: pd.DataFrame) -> float:
    """
    Доля случаев, где предсказанный лучший воркер совпал с фактически
    лучшим для той же комбинации эндпоинта и состояния очередей.
    """
    truth = df.groupby(["endpoint", "worker"])["latency_ms"].mean().unstack()
    truth = truth.dropna()
    if truth.empty:
        return float("nan")

    hits = 0
    for endpoint, row in truth.iterrows():
        actual_best = row.idxmin()
        median_inflight = {
            w: int(df[f"inflight_{w}"].median()) for w in WORKERS
        }
        median_work = {
            w: float(df[f"work_{w}"].median()) for w in WORKERS
        }
        feats = np.array(
            [encode(endpoint, int(df[df.endpoint == endpoint]["payload_bytes"].iloc[0]),
                    median_inflight, w, median_work) for w in WORKERS],
            dtype=np.float32,
        )
        with torch.no_grad():
            preds = model(torch.FloatTensor(scaler.transform(feats))).numpy()
        predicted_best = WORKERS[int(np.argmin(preds))]
        hits += int(predicted_best == actual_best)
    return hits / len(truth)


def main() -> None:
    X, y, df = load_dataset()
    print(f"Записей в датасете: {len(df)}")
    print(f"Распределение по воркерам: {df['worker'].value_counts().to_dict()}")
    print(f"Латентность: медиана {df['latency_ms'].median():.0f} мс, "
          f"p95 {df['latency_ms'].quantile(0.95):.0f} мс\n")

    idx = np.arange(len(df))
    idx_train, idx_test = train_test_split(idx, test_size=0.2, random_state=SEED)
    X_train, X_test = X[idx_train], X[idx_test]
    y_train, y_test = y[idx_train], y[idx_test]
    df_train, df_test = df.iloc[idx_train], df.iloc[idx_test]

    scaler = StandardScaler().fit(X_train)
    X_train_s = scaler.transform(X_train)
    X_test_s = scaler.transform(X_test)

    mae_group = baseline_group_mean(df_train, df_test)

    linear = LinearRegression().fit(X_train_s, y_train)
    mae_linear = evaluate_ms(y_test, linear.predict(X_test_s))

    model, mae_net = train_network(X_train_s, y_train, X_test_s, y_test)

    print("Точность предсказания латентности (MAE, мс — меньше лучше):")
    print(f"  среднее по паре эндпоинт-воркер : {mae_group:8.1f}")
    print(f"  линейная регрессия              : {mae_linear:8.1f}")
    print(f"  нейросеть                       : {mae_net:8.1f}")

    gain_vs_group = (mae_group - mae_net) / mae_group * 100
    gain_vs_linear = (mae_linear - mae_net) / mae_linear * 100
    print(f"\nВыигрыш нейросети: {gain_vs_group:+.1f}% к среднему по группе, "
          f"{gain_vs_linear:+.1f}% к линейной модели")

    acc = best_worker_accuracy(model, scaler, df_test)
    print(f"Угадывание лучшего воркера на отложенной выборке: {acc*100:.0f}%")

    LatencyPredictor(model, scaler).save()
    print(f"\nМодель сохранена: {DATA_DIR / 'latency_model.pth'}")


if __name__ == "__main__":
    main()
