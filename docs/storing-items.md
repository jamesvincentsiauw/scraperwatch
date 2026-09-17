# Storing scraped items without duplicates

scraperwatch tells you when a run produced duplicates. It does not remove them, because only your pipeline knows where items end up. This page describes the storage pattern that keeps daily scrapes from piling up copies, with SQL tested on PostgreSQL 17.

Two different problems cause duplicate rows:

- Within one run, pagination can shift while the crawler walks the pages, so the same item shows up twice. `run(key=[...])` reports this as the `duplicates` check.
- Across runs, a target scraped every day returns mostly unchanged items. Inserting them again each day fills the table with copies.

The pattern below handles both: stage the run, then promote it only when the verdict allows, keeping one current row per item and a history row only when the item changed.

## Tables

```sql
CREATE TABLE products (
    platform      text        NOT NULL,
    product_id    text        NOT NULL,
    title         text,
    price         numeric,
    content_hash  bytea       NOT NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL,
    PRIMARY KEY (platform, product_id)
);

CREATE TABLE product_history (
    platform     text        NOT NULL,
    product_id   text        NOT NULL,
    content_hash bytea       NOT NULL,
    title        text,
    price        numeric,
    seen_at      timestamptz NOT NULL
);

CREATE TABLE products_staging (
    run_id       text        NOT NULL,
    platform     text        NOT NULL,
    product_id   text        NOT NULL,
    title        text,
    price        numeric,
    content_hash bytea       NOT NULL,
    seen_at      timestamptz NOT NULL
);
```

The natural key (`platform`, `product_id`) identifies an item. Use the same fields you pass as `key` to scraperwatch. A URL is a poor key: the same product often has several.

## Hashing an item

Hash the fields that matter and leave out the ones that change on every scrape, such as the scrape time:

```python
import hashlib
import json

VOLATILE = {"scraped_at"}


def content_hash(item: dict) -> bytes:
    stable = {k: v for k, v in item.items() if k not in VOLATILE}
    encoded = json.dumps(stable, sort_keys=True, default=str).encode()
    return hashlib.blake2b(encoded, digest_size=16).digest()
```

## The run

```python
with scraperwatch.run(
    "shop-products", required=["title", "price"], key=["platform", "product_id"]
) as run:
    for url in urls:
        items = parse(fetch(url))
        run.record(url, items, status=200)
        stage(db, run.run_id, items)  # INSERT INTO products_staging ...

if run.verdict.quarantined:
    raise scraperwatch.Quarantined(run.verdict)
db.execute("SELECT promote(%s)", (run.run_id,))
```

Nothing reaches `products` before the verdict. A retried task starts a new run with a new `run_id`, so rows staged by a failed attempt are never promoted; delete staging rows older than a day on a schedule to clean them up.

## Promoting a run

```sql
CREATE FUNCTION promote(p_run text) RETURNS void LANGUAGE sql AS $$
    -- A history row for every item that is new or whose content changed.
    INSERT INTO product_history (platform, product_id, content_hash, title, price, seen_at)
    SELECT DISTINCT ON (s.platform, s.product_id)
           s.platform, s.product_id, s.content_hash, s.title, s.price, s.seen_at
    FROM products_staging s
    LEFT JOIN products p USING (platform, product_id)
    WHERE s.run_id = p_run
      AND p.content_hash IS DISTINCT FROM s.content_hash
    ORDER BY s.platform, s.product_id, s.seen_at DESC;

    -- One current row per item. Unchanged items only move last_seen_at.
    INSERT INTO products AS p (platform, product_id, title, price, content_hash, first_seen_at, last_seen_at)
    SELECT DISTINCT ON (platform, product_id)
           platform, product_id, title, price, content_hash, seen_at, seen_at
    FROM products_staging
    WHERE run_id = p_run
    ORDER BY platform, product_id, seen_at DESC
    ON CONFLICT (platform, product_id) DO UPDATE
    SET title        = EXCLUDED.title,
        price        = EXCLUDED.price,
        content_hash = EXCLUDED.content_hash,
        last_seen_at = EXCLUDED.last_seen_at;

    DELETE FROM products_staging WHERE run_id = p_run;
$$;
```

`DISTINCT ON` matters. Without it, a run that contains the same key twice fails with `ON CONFLICT DO UPDATE command cannot affect row a second time`, which is exactly what a pagination shift produces.

## What this gives you

With two products scraped on two days, where one product appeared twice on day 1, the other changed price on day 2, and day 2 was scraped and promoted twice:

| Table | Rows |
| --- | --- |
| `products` | 2, each with `first_seen_at` on day 1 and `last_seen_at` on day 2 |
| `product_history` | 3: both products on day 1, and the price change on day 2 |
| `products_staging` | 0 |

Items that disappear from the site keep their old `last_seen_at`, so "not seen for 7 days" is a simple query when you need to mark products as gone.
