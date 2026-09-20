# LoadLens

> The Russian [README.md](README.md) is the primary document. This is a
> condensed translation for readers who do not read Russian; where the
> two differ, the Russian one is correct.

**Load balancers do not know what a request will cost them. We learned to
predict it.**

A balancer distributes requests round-robin or by connection count.
Weights, where they exist, are set per route. But cost is not determined
by the route:

| request | duration |
|---|---|
| `/api/search?limit=10` | **10 ms** |
| `/api/search?limit=600` | **640 ms** |

Same address, 61× apart. The weight is attached to the route while the
difference lives inside it, so no amount of tuning can express it.

Because of this, rare heavy requests delay many light ones. On
event-loop engines (Node.js, Python asyncio, FastAPI) a single
compute-bound request stops **all** others: the same request delays its
neighbours by 9 ms on a threaded server and by 125 ms in an event loop.

## Try it in a minute

```bash
docker/run.sh            # then open http://127.0.0.1:8400
```

The whole stand comes up in containers; nothing else needs installing.
There is also a browser version of the diagnostic —
[loadlens/web/index.html](loadlens/web/index.html) — which opens with a
double click, accepts a log by drag and drop, and sends nothing
anywhere: the page has no server.

## What the product is

**1. Diagnostic** (`loadlens/`) reads an ordinary nginx, JSON or CSV
access log and answers: how much cost varies inside each route, what
explains the variation, and how far off a per-route estimate is. It
works on any stack because it reads the log, not the code.

**2. Router** — the same policy in three implementations: a Python
gateway for research (`gateway.py`), an OpenResty module (`nginx/`) and
a standalone Go binary (`gorouter/`). All three behave identically,
which is checked by measurement rather than assumed.

## What is measured

Everything is measured on a stand with real work — hashing, image
encoding, database and network calls, not a single `sleep` standing in
for computation.

| claim | measured |
|---|---|
| Engines differ in blocking, not in speed | 9 ms vs 125 ms |
| Cost is not determined by the route | up to 85× inside one address |
| Cost is predictable from visible features | 80 % error → 3.4 % |
| Slow-light-request share, ours vs least_conn | 36.9 % → 0.0 % |
| Capacity at a p95 ≤ 100 ms promise | 37.8 → 59.2 req/s (**+57 %**) |
| Cost of one routing decision (Go) | 58 ns, zero allocations |

## Verified on software we did not write

- **PostgREST over PostgreSQL** (300 000 rows, none of our code in the
  request path): 27× spread inside one route, 177 % per-route estimate
  error against 33 % from request features. From the log alone the tool
  worked out that sorting is the dominant cost — roughly 11× — which was
  never encoded into the stand. See [thirdparty/RESULT.md](thirdparty/RESULT.md).
- **Azure Functions production trace** (Microsoft Research): 32 968
  functions, 1.8 billion invocations. **44.6 %** of handlers vary by at
  least 10× within themselves.

## Where it does not work

Boundaries are measured and stated, not glossed over.

- **On homogeneous traffic routing is harmful**, not merely useless: on
  pure I/O it loses 43 % against the async engine.
- **Against least-connections it is a trade**, not a win: −21 %
  throughput in exchange for removing the slow-request tail.
- **At a loose latency promise the benefit disappears entirely.** At
  p95 ≤ 200 ms the saving is exactly zero rubles, so batch processing
  and job queues are not our market.
- A typical production handler varies by **8×, not 61×**. The 61× and
  85× figures come from deliberately heavy operations on our own stand
  and are not presented as the normal state of affairs.
- **No real company's production traffic has been tested.** This is the
  main gap, and it is why the first product is a free diagnostic.

## Reproduce it yourself

[ПРОВЕРЬ_САМ.md](ПРОВЕРЬ_САМ.md) (Russian) walks through four checks,
each a single command, taking about ten minutes in total.

## License

MIT, see [LICENSE](LICENSE).
