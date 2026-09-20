--
-- Маршрутизация по оценке стоимости запроса — внутри самого nginx.
--
-- Отдельный шлюз на Python удобен для исследования, но ставить его в
-- путь запроса перед настоящим сервисом мало кто захочет: это ещё одно
-- звено, которое надо разворачивать, следить за ним и объяснять службе
-- эксплуатации. Здесь та же политика живёт там, где балансировщик уже
-- стоит, — в nginx, и не добавляет ни одного сетевого перехода.
--
-- Решение принимается в access-фазе и записывается в переменную, по
-- которой nginx выбирает upstream. Занятость движков хранится в
-- разделяемой памяти, потому что у nginx несколько рабочих процессов и
-- обычная переменная Lua была бы своей у каждого.
--
-- Выбор сделан через переменную в proxy_pass, а не через
-- balancer_by_lua, намеренно. Balancer требует адрес узла числом:
-- доменные имена он не принимает, и пришлось бы тащить в конфигурацию
-- разрешение имён вместе с его кэшем и сроками жизни. Переменная в
-- proxy_pass опирается на обычные upstream-блоки, а значит на весь
-- привычный набор nginx — проверки живости, keepalive, таймауты — и
-- тому, кто будет это ставить у себя, не надо разбираться в новом
-- механизме.
--
local cjson = require "cjson.safe"

local _M = {}

local shared = ngx.shared.loadlens
local model = nil

-- Пороговое значение блокировки. Это не настроечная константа, а
-- параметр компромисса: при нуле весь трафик уходит в потоковый
-- движок, при большом значении политика вырождается в обычную
-- балансировку по занятости.
local BUDGET = tonumber(os.getenv("LOADLENS_BUDGET") or "30")

-- Каждому движку соответствует свой upstream-блок в nginx.conf.
local POOLS = { sync = "sync_pool", async = "async_pool" }

function _M.init(path)
    local fh = io.open(path, "r")
    if not fh then
        ngx.log(ngx.ERR, "не найдена модель стоимости: ", path)
        return
    end
    local raw = fh:read("*a")
    fh:close()
    model = cjson.decode(raw)
    if not model then
        ngx.log(ngx.ERR, "модель стоимости не разобрана: ", path)
    end
end

-- Степенной закон, подобранный по замерам: стоимость растёт как
-- параметр в степени b. В логарифмах это прямая, поэтому подгонка
-- устойчива, а вычисление здесь — один exp и один log.
local function estimate(section, endpoint, param, worker)
    if not model then return 0 end
    local by_endpoint = model[section] and model[section][endpoint]
    if not by_endpoint then return 0 end
    local coef = by_endpoint[worker]
    if not coef then return 0 end
    local p = tonumber(param) or 1
    if p < 1 then p = 1 end
    return math.exp(coef[1] + coef[2] * math.log(p))
end

local function pending(worker)
    return shared:get("pending:" .. worker) or 0
end

function _M.decide()
    ngx.req.read_body()
    local body = ngx.req.get_body_data()
    local req = body and cjson.decode(body) or nil
    local endpoint = req and req.endpoint or nil
    local param = req and req.param or nil

    -- Потоковый движок принимает всё: вычисление в потоке никого не
    -- останавливает целиком. В цикл событий запрос допускается, только
    -- если его блокировка укладывается в бюджет.
    local candidates = { "sync" }
    if estimate("blocking", endpoint, param, "async") <= BUDGET then
        candidates[#candidates + 1] = "async"
    end

    local best, best_value, best_weight
    for _, worker in ipairs(candidates) do
        local weight = estimate("blocking", endpoint, param, worker)
        local value = pending(worker) + weight
        if not best_value or value < best_value then
            best, best_value, best_weight = worker, value, weight
        end
    end

    shared:incr("pending:" .. best, best_weight, 0)
    ngx.ctx.loadlens_worker = best
    ngx.ctx.loadlens_weight = best_weight
    ngx.var.target = POOLS[best]
end

function _M.release()
    local worker = ngx.ctx.loadlens_worker
    if worker then
        shared:incr("pending:" .. worker, -(ngx.ctx.loadlens_weight or 0), 0)
    end
end

-- Для наблюдения: куда уходит трафик и сколько работы висит на каждом
-- движке. Служба эксплуатации увидит это до того, как поверит на слово.
function _M.status()
    ngx.header["Content-Type"] = "application/json"
    ngx.say(cjson.encode({
        budget_ms = BUDGET,
        pending = { sync = pending("sync"), async = pending("async") },
        model_loaded = model ~= nil,
    }))
end

return _M
