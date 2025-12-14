from __future__ import annotations

import csv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.settings import settings  # ← 너가 이미 쓰는 방식

# -----------------------------
# DB connection
# -----------------------------
DATABASE_URL = settings.database_url


def get_engine() -> Engine:
    if not DATABASE_URL:
        raise SystemExit("[ERROR] DATABASE_URL is not set")
    return create_engine(DATABASE_URL, echo=False, future=True)


# -----------------------------
# Snapshot tables
# -----------------------------
RAW_TABLE = "model_price_snapshot_month"

SIG_TABLES = {
    "sd_2": "model_price_snapshot_month_sd_2",
    "sd_2_5": "model_price_snapshot_month_sd_2_5",
    "sd_3": "model_price_snapshot_month_sd_3",
}


# -----------------------------
# Metric SQL (global, weighted)
# -----------------------------
GLOBAL_METRIC_SQL = """
SELECT
  (SELECT SUM(sample_count) FROM {raw}) AS raw_n,
  (SELECT SUM(sample_count) FROM {filt}) AS kept_n,

  1 - (
    (SELECT SUM(sample_count) FROM {filt})
    / NULLIF((SELECT SUM(sample_count) FROM {raw}), 0)
  ) AS removed_ratio,

  (SELECT SUM(avg_price * sample_count) / NULLIF(SUM(sample_count), 0)
   FROM {raw}) AS raw_weighted_avg,

  (SELECT SUM(avg_price * sample_count) / NULLIF(SUM(sample_count), 0)
   FROM {filt}) AS kept_weighted_avg,

  (
    (SELECT SUM(avg_price * sample_count) / NULLIF(SUM(sample_count), 0) FROM {filt})
    -
    (SELECT SUM(avg_price * sample_count) / NULLIF(SUM(sample_count), 0) FROM {raw})
  )
  /
  NULLIF(
    (SELECT SUM(avg_price * sample_count) / NULLIF(SUM(sample_count), 0) FROM {raw}),
    0
  ) AS avg_shift_ratio
"""


# -----------------------------
# Export metrics
# -----------------------------
def export_global_metrics(engine: Engine, out_csv: str = "sigma_metrics.csv") -> None:
    with engine.connect() as conn, open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow([
            "variant",
            "raw_n",
            "kept_n",
            "removed_ratio",
            "raw_weighted_avg",
            "kept_weighted_avg",
            "avg_shift_ratio",
        ])

        for variant, table in SIG_TABLES.items():
            sql = GLOBAL_METRIC_SQL.format(raw=RAW_TABLE, filt=table)
            row = conn.execute(text(sql)).mappings().one()

            writer.writerow([
                variant,
                int(row["raw_n"] or 0),
                int(row["kept_n"] or 0),
                float(row["removed_ratio"] or 0.0),
                float(row["raw_weighted_avg"] or 0.0),
                float(row["kept_weighted_avg"] or 0.0),
                float(row["avg_shift_ratio"] or 0.0),
            ])

    print(f"[OK] Metrics exported to {out_csv}")


# -----------------------------
# main
# -----------------------------
def main():
    engine = get_engine()
    export_global_metrics(engine)


if __name__ == "__main__":
    main()
