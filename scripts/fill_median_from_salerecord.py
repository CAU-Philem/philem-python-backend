from __future__ import annotations

import argparse
from typing import Optional, Dict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.settings import settings


# -----------------------------
# CONFIG (필요하면 여기만 수정)
# -----------------------------
DATABASE_URL = settings.database_url

SALE_TABLE = "SaleRecord"         # ✅ 너 말대로 SaleRecord
PRICE_COL = "price"               # 가격 컬럼
DATE_COL = "sold_at"              # 날짜 컬럼 (다르면 바꿔!)
MODEL_COL = "model_id"            # 모델 id
COND_COL = "condition"            # 상태 컬럼 (예약어라 백틱 사용)

SNAPSHOT_TABLES: Dict[Optional[float], str] = {
    None: "model_price_snapshot_month",      # raw(필터 없음)
    1.5: "model_price_snapshot_month_sd_1_5",
    2.0: "model_price_snapshot_month_sd_2",
    2.5: "model_price_snapshot_month_sd_2_5",
    3.0: "model_price_snapshot_month_sd_3",
}

MEDIAN_COL = "median_price"       # snapshot에 저장할 컬럼명
MEDIAN_TYPE = "BIGINT"            # 원하는 타입 (Long)


def get_engine() -> Engine:
    if not DATABASE_URL:
        raise SystemExit("[ERROR] settings.database_url is empty")
    return create_engine(DATABASE_URL, echo=False, future=True)


def column_exists(conn, table_name: str, column_name: str) -> bool:
    q = text("""
        SELECT COUNT(*) AS c
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = :t
          AND column_name = :c
    """)
    r = conn.execute(q, {"t": table_name, "c": column_name}).mappings().one()
    return int(r["c"]) > 0


def ensure_median_column(conn, table_name: str) -> None:
    # 1) 없으면 ADD
    if not column_exists(conn, table_name, MEDIAN_COL):
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {MEDIAN_COL} {MEDIAN_TYPE} NULL"))
        print(f"[OK] Added column {table_name}.{MEDIAN_COL} ({MEDIAN_TYPE})")
    # 2) 있으면 BIGINT로 맞춤
    conn.execute(text(f"ALTER TABLE {table_name} MODIFY COLUMN {MEDIAN_COL} {MEDIAN_TYPE} NULL"))
    print(f"[OK] Ensured type {table_name}.{MEDIAN_COL} = {MEDIAN_TYPE}")


def update_median_for_snapshot(conn, snapshot_table: str, k: Optional[float]) -> int:
    """
    SaleRecord 기반으로 월별 median(price)을 계산해서 snapshot_table.median_price를 채움.
    - k=None: raw(필터 없음)
    - k=2.0/2.5/3.0: (model_id, condition) 그룹 mean±k*std 필터 후 median
    - median은 BIGINT로 저장 (ROUND 적용)
    """
    # 필터링된 SaleRecord 집합을 만드는 FROM 절
    # (CTE 안 쓰고 서브쿼리로만 구성: MySQL에서 안정적으로 동작)
    if k is None:
        from_clause = f"""
            FROM {SALE_TABLE} sr
            WHERE sr.{PRICE_COL} IS NOT NULL
        """
        params = {}
    else:
        from_clause = f"""
            FROM {SALE_TABLE} sr
            JOIN (
              SELECT
                {MODEL_COL} AS model_id,
                `{COND_COL}` AS cond,
                AVG({PRICE_COL}) AS mean_price,
                STDDEV_SAMP({PRICE_COL}) AS std_price
              FROM {SALE_TABLE}
              WHERE {PRICE_COL} IS NOT NULL
              GROUP BY {MODEL_COL}, `{COND_COL}`
            ) st
              ON st.model_id = sr.{MODEL_COL}
             AND st.cond = sr.`{COND_COL}`
            WHERE sr.{PRICE_COL} IS NOT NULL
              AND (
                st.std_price IS NULL OR st.std_price = 0
                OR sr.{PRICE_COL} BETWEEN (st.mean_price - :k * st.std_price)
                                   AND (st.mean_price + :k * st.std_price)
              )
        """
        params = {"k": float(k)}

    # 핵심: 월별 median 계산(윈도우 함수 + 중앙 1~2개 평균)
    # 결과를 snapshot 테이블에 (model_id, condition, year, month)로 매칭해서 업데이트
    q = text(f"""
        UPDATE {snapshot_table} s
        JOIN (
          SELECT
            model_id,
            cond AS `{COND_COL}`,
            sold_year,
            sold_month,
            CAST(ROUND(AVG(price)) AS SIGNED) AS median_price
          FROM (
            SELECT
              sr.{MODEL_COL} AS model_id,
              sr.`{COND_COL}` AS cond,
              YEAR(sr.{DATE_COL}) AS sold_year,
              MONTH(sr.{DATE_COL}) AS sold_month,
              sr.{PRICE_COL} AS price,
              ROW_NUMBER() OVER (
                PARTITION BY sr.{MODEL_COL}, sr.`{COND_COL}`, YEAR(sr.{DATE_COL}), MONTH(sr.{DATE_COL})
                ORDER BY sr.{PRICE_COL}
              ) AS rn,
              COUNT(*) OVER (
                PARTITION BY sr.{MODEL_COL}, sr.`{COND_COL}`, YEAR(sr.{DATE_COL}), MONTH(sr.{DATE_COL})
              ) AS cnt
              {from_clause}
          ) t
          WHERE rn IN (FLOOR((cnt + 1)/2), CEIL((cnt + 1)/2))
          GROUP BY model_id, cond, sold_year, sold_month
        ) m
          ON m.model_id = s.model_id
         AND m.`{COND_COL}` = s.`{COND_COL}`
         AND m.sold_year = s.sold_year
         AND m.sold_month = s.sold_month
        SET s.{MEDIAN_COL} = m.median_price
    """)

    res = conn.execute(q, params)
    return int(res.rowcount or 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        type=str,
        default="all",
        help="all | raw | sd_2 | sd_2_5 | sd_3",
    )
    args = parser.parse_args()

    key_map = {
        "raw": None,
        "sd_1_5": 1.5,
        "sd_2": 2.0,
        "sd_2_5": 2.5,
        "sd_3": 3.0,
    }

    engine = get_engine()

    with engine.begin() as conn:
        targets = []
        if args.only == "all":
            targets = [None, 2.0, 2.5, 3.0]
        else:
            if args.only not in key_map:
                raise SystemExit("[ERROR] --only must be one of: all, raw, sd_2, sd_2_5, sd_3")
            targets = [key_map[args.only]]

        for k in targets:
            table = SNAPSHOT_TABLES[k]
            print(f"\n=== Filling median for {table} (k={k}) ===")
            ensure_median_column(conn, table)
            updated = update_median_for_snapshot(conn, table, k=k)
            print(f"[OK] Updated rows: {updated}")


if __name__ == "__main__":
    main()
