"""
scripts/outlier_threshold_sweep.py

- SaleRecord를 직접 수정/삭제하지 않는다.
- model_id + condition 그룹에서 평균/표준편차 기반(±kσ)으로 "집계에서만" outlier를 제외한다.
- k = [2.0, 2.5, 3.0] 비교 리포트 생성
- 선택한 k로 model_price_snapshot_month 재생성 (DELETE + INSERT; idempotent)

Run:
  python -m scripts.outlier_threshold_sweep --report
  python -m scripts.outlier_threshold_sweep --rebuild --k 3.0
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from config.settings import settings

# 프로젝트 설정에 맞게 가져오면 됨
# 예: from config.settings import settings
# DATABASE_URL = settings.database_url
DATABASE_URL = settings.database_url # TODO: 너희 프로젝트 방식으로 채우기


@dataclass
class SweepRow:
    k: float
    groups: int
    total_n: int
    kept_n: int
    removed_ratio: float
    raw_avg: Optional[float]
    kept_avg: Optional[float]
    avg_change_ratio: Optional[float]


def get_engine() -> Engine:
    if not DATABASE_URL:
        raise SystemExit("[ERROR] DATABASE_URL is not set. Please wire it to your config.")
    return create_engine(DATABASE_URL, echo=False, future=True)


def run_threshold_sweep(engine: Engine, ks: Iterable[float] = (2.0, 2.5, 3.0)) -> List[SweepRow]:
    """
    전체 SaleRecord에서 model_id+condition 그룹별로 (mean,std) 구하고,
    각 k에 대해 제거비율/평균변화 같은 지표를 요약한다.

    NOTE:
    - MySQL의 STDDEV_SAMP/STDDEV_POP 중 무엇을 쓸지: 보통 표본이면 STDDEV_SAMP.
    - std가 0이거나 NULL(표본 1개 등)인 그룹은 "필터 적용 불가"로 보고 전부 keep 처리.
    """
    rows: List[SweepRow] = []

    for k in ks:
        q = text(
            """
            WITH stats AS (
              SELECT
                model_id,
                `condition`,
                COUNT(*) AS n,
                AVG(price) AS mean_price,
                STDDEV_SAMP(price) AS std_price
              FROM sale_record
              WHERE price IS NOT NULL
              GROUP BY model_id, `condition`
            ),
            tagged AS (
              SELECT
                sr.price,
                s.n,
                s.mean_price,
                s.std_price,
                CASE
                  WHEN s.std_price IS NULL OR s.std_price = 0 THEN 1
                  WHEN sr.price BETWEEN (s.mean_price - :k * s.std_price)
                                   AND (s.mean_price + :k * s.std_price) THEN 1
                  ELSE 0
                END AS keep_flag
              FROM sale_record sr
              JOIN stats s
                ON s.model_id = sr.model_id
               AND s.`condition` = sr.`condition`
              WHERE sr.price IS NOT NULL
            )
            SELECT
              :k AS k,
              (SELECT COUNT(*) FROM stats) AS groups,
              COUNT(*) AS total_n,
              SUM(keep_flag) AS kept_n,
              AVG(price) AS raw_avg,
              AVG(CASE WHEN keep_flag = 1 THEN price END) AS kept_avg
            FROM tagged
            """
        )

        with engine.connect() as conn:
            r = conn.execute(q, {"k": float(k)}).mappings().one()

        total_n = int(r["total_n"] or 0)
        kept_n = int(r["kept_n"] or 0)
        removed_ratio = 0.0 if total_n == 0 else (total_n - kept_n) / total_n

        raw_avg = float(r["raw_avg"]) if r["raw_avg"] is not None else None
        kept_avg = float(r["kept_avg"]) if r["kept_avg"] is not None else None

        if raw_avg is None or kept_avg is None or raw_avg == 0:
            avg_change_ratio = None
        else:
            avg_change_ratio = (kept_avg - raw_avg) / raw_avg  # 예: -0.012 = -1.2%

        rows.append(
            SweepRow(
                k=float(r["k"]),
                groups=int(r["groups"] or 0),
                total_n=total_n,
                kept_n=kept_n,
                removed_ratio=removed_ratio,
                raw_avg=raw_avg,
                kept_avg=kept_avg,
                avg_change_ratio=avg_change_ratio,
            )
        )

    return rows


def print_sweep_report(rows: List[SweepRow]) -> None:
    print("\n[Outlier Threshold Sweep Report]")
    print("k | groups | total_n | kept_n | removed_ratio | raw_avg | kept_avg | avg_change")
    print("-" * 90)
    for r in rows:
        avg_change = None if r.avg_change_ratio is None else f"{r.avg_change_ratio*100:+.2f}%"
        print(
            f"{r.k:>3.1f} | {r.groups:>6} | {r.total_n:>7} | {r.kept_n:>6} | "
            f"{r.removed_ratio*100:>11.2f}% | "
            f"{(f'{r.raw_avg:.1f}' if r.raw_avg is not None else 'NA'):>7} | "
            f"{(f'{r.kept_avg:.1f}' if r.kept_avg is not None else 'NA'):>8} | "
            f"{(avg_change if avg_change is not None else 'NA'):>9}"
        )
    print()


def rebuild_model_price_snapshot_month(engine: Engine, k: float = 3.0) -> None:
    """
    model_price_snapshot_month를 kσ 기준으로 재생성한다.
    - 원본(sale_record)은 손대지 않음
    - snapshot 테이블은 파생 데이터이므로 통으로 재생성 가능 (idempotent)
    """
    delete_q = text("DELETE FROM model_price_snapshot_month")

    insert_q = text(
        """
        WITH stats AS (
          SELECT
            model_id,
            `condition`,
            AVG(price) AS mean_price,
            STDDEV_SAMP(price) AS std_price
          FROM sale_record
          WHERE price IS NOT NULL
          GROUP BY model_id, `condition`
        ),
        filtered AS (
          SELECT
            sr.model_id,
            sr.`condition`,
            YEAR(sr.sold_at) AS sold_year,
            MONTH(sr.sold_at) AS sold_month,
            sr.price
          FROM sale_record sr
          JOIN stats s
            ON s.model_id = sr.model_id
           AND s.`condition` = sr.`condition`
          WHERE sr.price IS NOT NULL
            AND (
              s.std_price IS NULL OR s.std_price = 0
              OR sr.price BETWEEN (s.mean_price - :k * s.std_price)
                             AND (s.mean_price + :k * s.std_price)
            )
        )
        INSERT INTO model_price_snapshot_month
          (model_id, `condition`, sold_year, sold_month, min_price, max_price, avg_price, sample_count)
        SELECT
          model_id,
          `condition`,
          sold_year,
          sold_month,
          MIN(price) AS min_price,
          MAX(price) AS max_price,
          AVG(price) AS avg_price,
          COUNT(*) AS sample_count
        FROM filtered
        GROUP BY model_id, `condition`, sold_year, sold_month
        """
    )

    with engine.begin() as conn:
        conn.execute(delete_q)
        conn.execute(insert_q, {"k": float(k)})

    print(f"[OK] Rebuilt model_price_snapshot_month with ±{k}σ filtering (aggregation-only).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Print threshold sweep report.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild model_price_snapshot_month.")
    parser.add_argument("--k", type=float, default=3.0, help="Sigma threshold for rebuild (default: 3.0).")
    args = parser.parse_args()

    engine = get_engine()

    if args.report:
        rows = run_threshold_sweep(engine, ks=(2.0, 2.5, 3.0))
        print_sweep_report(rows)

    if args.rebuild:
        rebuild_model_price_snapshot_month(engine, k=args.k)


if __name__ == "__main__":
    main()
