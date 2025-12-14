from __future__ import annotations

import argparse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from config.settings import settings

DATABASE_URL = settings.database_url  # TODO: settings에서 가져오기


ALLOWED_TABLES = {
    1.5: "model_price_snapshot_month_sd_1_5",
    2.0: "model_price_snapshot_month_sd_2",
    2.5: "model_price_snapshot_month_sd_2_5",
    3.0: "model_price_snapshot_month_sd_3",
    # 0.0: "model_price_snapshot_month_raw",  # 원하면 추가
}


def get_engine() -> Engine:
    if not DATABASE_URL:
        raise SystemExit("[ERROR] DATABASE_URL is not set")
    return create_engine(DATABASE_URL, echo=False, future=True)


def rebuild_snapshot_to_table(engine: Engine, k: float) -> None:
    if k not in ALLOWED_TABLES:
        raise ValueError(f"k must be one of {list(ALLOWED_TABLES.keys())}")

    target = ALLOWED_TABLES[k]

    # 테이블명은 파라미터 바인딩이 안 되므로, whitelist에서만 가져온 문자열을 직접 포맷
    truncate_q = text(f"TRUNCATE TABLE {target}")

    insert_q = text(
        f"""
        INSERT INTO {target}
          (model_id, `condition`, sold_year, sold_month, min_price, max_price, avg_price, sample_count)
        SELECT
          sr.model_id,
          sr.`condition`,
          YEAR(sr.sold_at) AS sold_year,
          MONTH(sr.sold_at) AS sold_month,
          MIN(sr.price) AS min_price,
          MAX(sr.price) AS max_price,
          AVG(sr.price) AS avg_price,
          COUNT(*) AS sample_count
        FROM SaleRecord sr
        JOIN (
          SELECT
            model_id,
            `condition`,
            AVG(price) AS mean_price,
            STDDEV_SAMP(price) AS std_price
          FROM SaleRecord
          WHERE price IS NOT NULL
          GROUP BY model_id, `condition`
        ) s
          ON s.model_id = sr.model_id
        AND s.`condition` = sr.`condition`
        WHERE sr.price IS NOT NULL
          AND (
            s.std_price IS NULL OR s.std_price = 0
            OR sr.price BETWEEN (s.mean_price - :k * s.std_price)
                          AND (s.mean_price + :k * s.std_price)
          )
        GROUP BY sr.model_id, sr.`condition`, YEAR(sr.sold_at), MONTH(sr.sold_at)
        """
    )


    with engine.begin() as conn:
        conn.execute(truncate_q)
        conn.execute(insert_q, {"k": float(k)})

    print(f"[OK] Rebuilt {target} using ±{k}σ (aggregation-only).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=float, required=True, help="2.0 / 2.5 / 3.0")
    args = parser.parse_args()

    engine = get_engine()
    rebuild_snapshot_to_table(engine, args.k)


if __name__ == "__main__":
    main()
