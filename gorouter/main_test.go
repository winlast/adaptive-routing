package main

import (
	"encoding/json"
	"os"
	"testing"
)

// Стоимость самого решения о маршруте. Вопрос «не будет ли распределитель
// сам грузить сервер» задают первым, и отвечать на него надо числом.
// Сеть в замер не входит намеренно: измеряется работа маршрутизатора, а
// не скорость движков.
func BenchmarkDecide(b *testing.B) {
	raw, err := os.ReadFile("../data/power_law.json")
	if err != nil {
		b.Skip("нет файла модели")
	}
	var m model
	if err := json.Unmarshal(raw, &m); err != nil {
		b.Fatal(err)
	}
	r := newRouter(m, 30)

	// Набор обращений, а не одно: так замер не вырождается в проверку
	// попадания в кэш процессора на единственной ветке.
	requests := []request{
		{"/api/user/profile", 12},
		{"/api/search", 600},
		{"/api/report/generate", 45},
		{"/api/image/thumbnail", 1200},
		{"/api/auth/verify", 1},
		{"/api/feed", 400},
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		r.decide(requests[i%len(requests)])
	}
}

// Проверка, что политика действительно уводит тяжёлые обращения из
// цикла событий: без этого замер скорости ничего не значит.
func TestHeavyGoesToSafeWorker(t *testing.T) {
	raw, err := os.ReadFile("../data/power_law.json")
	if err != nil {
		t.Skip("нет файла модели")
	}
	var m model
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	r := newRouter(m, 30)

	heavy, _ := r.decide(request{"/api/report/generate", 75})
	if heavy.name != "sync" {
		t.Errorf("тяжёлое обращение ушло в %s, ожидался sync", heavy.name)
	}
	light, _ := r.decide(request{"/api/user/profile", 5})
	if light.name == "" {
		t.Error("лёгкое обращение никуда не направлено")
	}
	blocking := r.estimate(m.Blocking, "/api/report/generate", 75, "async")
	if blocking <= 30 {
		t.Errorf("оценка блокировки %.1f мс не превышает бюджет — "+
			"проверка бессмысленна", blocking)
	}
}
