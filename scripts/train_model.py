"""
Обучение RouterClassifier на данных из data/router_data.csv.
Сохраняет веса и scaler в data/, а также confusion matrix и графики
обучения в figures/ — для раздела статьи про качество модели.
"""
from pathlib import Path

import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix, classification_report
import joblib
import matplotlib.pyplot as plt
import seaborn as sns

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"


class RouterClassifier(nn.Module):
    def __init__(self):
        super(RouterClassifier, self).__init__()
        self.layer1 = nn.Linear(3, 16)
        self.relu1 = nn.ReLU()
        self.layer2 = nn.Linear(16, 8)
        self.relu2 = nn.ReLU()
        self.output = nn.Linear(8, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.relu1(self.layer1(x))
        x = self.relu2(self.layer2(x))
        x = self.sigmoid(self.output(x))
        return x


def train_model():
    DATA_DIR.mkdir(exist_ok=True)
    FIGURES_DIR.mkdir(exist_ok=True)

    # 1. Загрузка данных
    df = pd.read_csv(DATA_DIR / "router_data.csv")
    X = df[['current_load', 'io_intensity', 'cpu_usage']].values
    y = df['best_backend'].values

    # 2. Разделение на train и test
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # 3. Масштабирование признаков
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    joblib.dump(scaler, DATA_DIR / "scaler.pkl")

    # 4. Тензоры PyTorch
    X_train_tensor = torch.FloatTensor(X_train)
    y_train_tensor = torch.FloatTensor(y_train).view(-1, 1)
    X_test_tensor = torch.FloatTensor(X_test)
    y_test_tensor = torch.FloatTensor(y_test).view(-1, 1)

    dataset = TensorDataset(X_train_tensor, y_train_tensor)
    dataloader = DataLoader(dataset, batch_size=32, shuffle=True)

    # 5. Модель, функция потерь, оптимизатор
    model = RouterClassifier()
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.01)

    # 6. Обучение — сохраняем историю loss для графика
    epochs = 30
    loss_history = []
    print("Начало обучения...")
    for epoch in range(epochs):
        model.train()
        epoch_loss = 0
        for batch_X, batch_y in dataloader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()

        avg_loss = epoch_loss / len(dataloader)
        loss_history.append(avg_loss)

        if (epoch + 1) % 5 == 0:
            print(f"Эпоха {epoch+1}/{epochs}, Loss: {avg_loss:.4f}")

    # 7. Оценка на тестовой выборке
    model.eval()
    with torch.no_grad():
        test_outputs = model(X_test_tensor)
        predictions = (test_outputs >= 0.5).float()
        accuracy = (predictions == y_test_tensor).float().mean()
        print(f"\nТочность на тестовой выборке (Accuracy): {accuracy.item():.4f}")

    y_pred = predictions.numpy().flatten().astype(int)
    y_true = y_test_tensor.numpy().flatten().astype(int)

    # --- Confusion matrix ---
    cm = confusion_matrix(y_true, y_pred)
    print("\nConfusion matrix (строки — истина, столбцы — предсказание):")
    print(cm)

    report = classification_report(y_true, y_pred, target_names=["Flask", "FastAPI"])
    print("\nClassification report:")
    print(report)

    with open(DATA_DIR / "classification_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Accuracy: {accuracy.item():.4f}\n\n")
        f.write("Confusion matrix:\n")
        f.write(str(cm) + "\n\n")
        f.write(report)
    print(f"\nСохранено: {DATA_DIR / 'classification_report.txt'}")

    plt.figure(figsize=(6, 5))
    sns.heatmap(
        cm, annot=True, fmt="d", cmap="Blues",
        xticklabels=["Flask", "FastAPI"], yticklabels=["Flask", "FastAPI"]
    )
    plt.title("Confusion Matrix — RouterClassifier")
    plt.xlabel("Предсказание")
    plt.ylabel("Истина")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "confusion_matrix.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'confusion_matrix.png'}")
    plt.close()

    # --- График loss по эпохам ---
    plt.figure(figsize=(8, 5))
    plt.plot(range(1, epochs + 1), loss_history, marker="o")
    plt.title("Loss по эпохам обучения")
    plt.xlabel("Эпоха")
    plt.ylabel("BCE Loss")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "training_loss.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'training_loss.png'}")
    plt.close()

    # 8. Сохранение модели
    torch.save(model.state_dict(), DATA_DIR / "router_model.pth")
    print(f"Веса модели сохранены в '{DATA_DIR / 'router_model.pth'}'")


if __name__ == "__main__":
    train_model()