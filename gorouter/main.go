// Маршрутизатор по оценке стоимости запроса — самостоятельная программа.
//
// Зачем он нужен помимо модуля для nginx. Во-первых, не у всех стоит
// nginx: кому-то проще поставить один двоичный файл без зависимостей.
// Во-вторых, на нём видно, сколько на самом деле стоит сама
// маршрутизация: в Go нет глобальной блокировки интерпретатора, и
// накладные расходы посредника можно измерить отдельно от накладных
// расходов языка, на котором велось исследование.
//
// Политика в точности та же, что в core/policies.py и в
// nginx/loadlens.lua: потоковый движок принимает всё, в цикл событий
// запрос допускается только если его блокировка укладывается в бюджет,
// а среди допустимых выбирается наименее занятый.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"time"
)

// Коэффициенты степенного закона: стоимость растёт как параметр в
// степени b, подгонка велась в логарифмах.
type coefficients [2]float64

type model struct {
	Cost     map[string]map[string]coefficients `json:"cost"`
	Blocking map[string]map[string]coefficients `json:"blocking"`
}

type worker struct {
	name string
	url  string
	// Ожидающая работа хранится в микросекундах целым числом: атомарные
	// операции над числами с плавающей точкой обошлись бы мьютексом, а
	// точность в микросекунду здесь избыточна и так.
	pendingUs atomic.Int64
}

type router struct {
	model    model
	safe     *worker
	guarded  *worker
	budgetMs float64
	client   *http.Client
	decided  atomic.Int64
	nsSpent  atomic.Int64
}

type request struct {
	Endpoint string  `json:"endpoint"`
	Param    float64 `json:"param"`
}

func (r *router) estimate(section map[string]map[string]coefficients,
	endpoint string, param float64, name string) float64 {
	byEndpoint, ok := section[endpoint]
	if !ok {
		return 0
	}
	coef, ok := byEndpoint[name]
	if !ok {
		return 0
	}
	if param < 1 {
		param = 1
	}
	return math.Exp(coef[0] + coef[1]*math.Log(param))
}

// decide выбирает движок. Вынесено отдельной функцией, чтобы её стоимость
// можно было измерить без сети — см. main_test.go.
func (r *router) decide(req request) (*worker, float64) {
	guardedBlocking := r.estimate(r.model.Blocking, req.Endpoint, req.Param,
		r.guarded.name)

	safeWeight := r.estimate(r.model.Blocking, req.Endpoint, req.Param,
		r.safe.name)
	best, bestWeight := r.safe, safeWeight
	bestValue := float64(r.safe.pendingUs.Load())/1000 + safeWeight

	if guardedBlocking <= r.budgetMs {
		value := float64(r.guarded.pendingUs.Load())/1000 + guardedBlocking
		if value < bestValue {
			best, bestWeight = r.guarded, guardedBlocking
		}
	}
	return best, bestWeight
}

func (r *router) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	if req.URL.Path == "/status" {
		r.status(w)
		return
	}
	body, err := io.ReadAll(req.Body)
	if err != nil {
		http.Error(w, "не прочитано тело запроса", http.StatusBadRequest)
		return
	}
	var parsed request
	// Нераспознанное тело — не повод отказывать: неизвестный запрос
	// уходит в потоковый движок, где он никого не блокирует целиком.
	_ = json.Unmarshal(body, &parsed)

	started := time.Now()
	target, weight := r.decide(parsed)
	r.nsSpent.Add(time.Since(started).Nanoseconds())
	r.decided.Add(1)

	target.pendingUs.Add(int64(weight * 1000))
	defer target.pendingUs.Add(-int64(weight * 1000))

	proxied, err := http.NewRequest(http.MethodPost, target.url,
		bytes.NewReader(body))
	if err != nil {
		http.Error(w, "запрос не собран", http.StatusInternalServerError)
		return
	}
	proxied.Header.Set("Content-Type", "application/json")

	resp, err := r.client.Do(proxied)
	if err != nil {
		http.Error(w, "движок недоступен: "+err.Error(),
			http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Worker", target.name)
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)
}

func (r *router) status(w http.ResponseWriter) {
	decided := r.decided.Load()
	var avgNs int64
	if decided > 0 {
		avgNs = r.nsSpent.Load() / decided
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"budget_ms": r.budgetMs,
		"pending_ms": map[string]float64{
			r.safe.name:    float64(r.safe.pendingUs.Load()) / 1000,
			r.guarded.name: float64(r.guarded.pendingUs.Load()) / 1000,
		},
		"decisions":             decided,
		"avg_decision_ns":       avgNs,
		"model_endpoints_known": len(r.model.Blocking),
	})
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func loadModel(path string) (model, error) {
	var m model
	raw, err := os.ReadFile(path)
	if err != nil {
		return m, err
	}
	return m, json.Unmarshal(raw, &m)
}

func newRouter(m model, budgetMs float64) *router {
	return &router{
		model:    m,
		safe:     &worker{name: "sync", url: env("SYNC_URL", "http://127.0.0.1:8201/process")},
		guarded:  &worker{name: "async", url: env("ASYNC_URL", "http://127.0.0.1:8202/process")},
		budgetMs: budgetMs,
		client: &http.Client{
			Timeout: 120 * time.Second,
			Transport: &http.Transport{
				MaxIdleConns:        512,
				MaxIdleConnsPerHost: 512,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}
}

func main() {
	modelPath := env("MODEL", "data/power_law.json")
	m, err := loadModel(modelPath)
	if err != nil {
		log.Fatalf("модель стоимости не загружена (%s): %v", modelPath, err)
	}
	budget, err := strconv.ParseFloat(env("BUDGET_MS", "30"), 64)
	if err != nil {
		log.Fatalf("неверное значение BUDGET_MS: %v", err)
	}

	r := newRouter(m, budget)
	listen := env("LISTEN", "127.0.0.1:8700")
	fmt.Printf("маршрутизатор слушает %s, бюджет %.0f мс, маршрутов в модели %d\n",
		listen, budget, len(m.Blocking))
	log.Fatal(http.ListenAndServe(listen, r))
}
