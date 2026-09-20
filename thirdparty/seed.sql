-- Данные для проверки: таблица обычного вида, какие бывают в любом
-- сервисе. Объём выбран так, чтобы запросы занимали заметное, но не
-- абсурдное время; содержание значения не имеет.
CREATE ROLE web_anon NOLOGIN;
GRANT USAGE ON SCHEMA public TO web_anon;

CREATE TABLE orders (
    id          serial PRIMARY KEY,
    customer    text NOT NULL,
    region      text NOT NULL,
    status      text NOT NULL,
    amount      numeric(10,2) NOT NULL,
    note        text NOT NULL,
    created_at  timestamptz NOT NULL
);

INSERT INTO orders (customer, region, status, amount, note, created_at)
SELECT
    'customer-' || (i % 5000),
    (ARRAY['north','south','east','west'])[1 + (i % 4)],
    (ARRAY['new','paid','shipped','done','cancelled'])[1 + (i % 5)],
    round((random() * 10000)::numeric, 2),
    repeat(md5(i::text), 3),
    now() - (i || ' minutes')::interval
FROM generate_series(1, 300000) AS i;

CREATE INDEX ON orders (region);
CREATE INDEX ON orders (status);
CREATE INDEX ON orders (created_at);

GRANT SELECT ON orders TO web_anon;
