"""
pricing_pipeline.py

- Reads Listing + ListingItem from the same DB your Spring Boot app uses.
- Inserts missing rows into sale_record (based on SOLD listings).
- Aggregates sale_record into model_price_snapshot_month.

Run:
    python pricing_pipeline.py
"""

from datetime import date, timedelta

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from config.settings import settings

# --------------------------------------------------
# DB connection setup
# --------------------------------------------------

load_dotenv()

DB_USER = settings.db_user
DB_PASSWORD = settings.db_password
DB_HOST = settings.db_host
DB_PORT = settings.db_port  
DB_NAME = settings.db_name

if not all([DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME]):
    raise SystemExit("[ERROR] Missing DB config. Check your .env file.")

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)

engine = create_engine(DATABASE_URL, echo=False, future=True)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def test_connection():
    print(f"[INFO] Connecting to DB: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("[INFO] DB connection OK.\n")


# --------------------------------------------------
# Step A: Fill sale_record from listing_item + listing
# --------------------------------------------------
#
# Uses your JPA entities:
#
# SaleRecord:
#   @OneToOne ListingItem listing_item  -> sale_record.listing_item_id
#   @ManyToOne ItemModel item_model     -> sale_record.model_id
#   Long price                          -> sale_record.price
#   ConditionType condition             -> sale_record.condition (STRING)
#   LocalDateTime sold_at               -> sale_record.sold_at
#   Region region                       -> sale_record.region_id
#   MarketType market_type              -> sale_record.market_type (STRING)
#   PriceType created_from              -> sale_record.created_from (STRING)
#
# ListingItem:
#   Long id                             -> listing_item.id
#   @ManyToOne Listing listing          -> listing_item.listing_id
#   @ManyToOne ItemModel item_model     -> listing_item.model_id
#   Long price                          -> listing_item.price
#   ConditionType condition             -> listing_item.condition
#   PriceType price_type                -> listing_item.price_type
#
# Listing (assumptions):
#   Long id                             -> listing.id
#   Region region                       -> listing.region_id
#   MarketType market_type              -> listing.market_type
#   String status                       -> listing.status ('SOLD' when sold)
#   LocalDateTime updated_at            -> listing.updated_at as sold time
#
# This function:
#   - Inserts into sale_record only when there is no existing sale_record
#     for that listing_item_id.
#   - Sets created_from = listing_item.price_type.
#

def sync_sale_records():
    sql = text("""
        INSERT INTO sale_record (
            listing_item_id,
            model_id,
            price,
            condition,
            sold_at,
            region_id,
            market_type,
            created_from
        )
        SELECT
            li.id            AS listing_item_id,
            li.model_id      AS model_id,
            li.price         AS price,
            li.condition     AS condition,
            l.updated_at     AS sold_at,
            l.region_id      AS region_id,
            l.market_type    AS market_type,
            li.price_type    AS created_from
        FROM listing_item li
        JOIN listing l
          ON li.listing_id = l.id
        LEFT JOIN sale_record sr
          ON sr.listing_item_id = li.id
        WHERE l.status = 'SOLD'
          AND sr.id IS NULL
          AND li.price IS NOT NULL
    """)

    with engine.begin() as conn:
        result = conn.execute(sql)
        # rowcount may be -1 for some drivers but usually OK for INSERT
        print(f"[SaleRecord] Inserted approximately {result.rowcount} new rows.")


# --------------------------------------------------
# Step B: Aggregate into model_price_snapshot_month
# --------------------------------------------------
#
# ModelPriceSnapshotMonth:
#   @ManyToOne ItemModel item_model -> model_id
#   int sold_year
#   int sold_month
#   ConditionType condition         -> STRING
#   Long max_price
#   Long min_price
#   Long avg_price
#   Long sample_count
#
# NOTE: For ON DUPLICATE KEY UPDATE to work correctly, you should have a
# UNIQUE index on (model_id, sold_year, sold_month, condition), e.g.:
#
#   ALTER TABLE model_price_snapshot_month
#   ADD UNIQUE KEY uniq_model_month_condition
#       (model_id, sold_year, sold_month, condition);
#
# This function:
#   - Looks at sale_record.sold_at for the last N months.
#   - Groups by (model_id, year, month, condition).
#   - Upserts aggregated stats into model_price_snapshot_month.
#

def sync_model_price_snapshots(months_back: int = 12):
    cutoff_date = date.today() - timedelta(days=months_back * 30)

    sql = text("""
        INSERT INTO model_price_snapshot_month (
            model_id,
            sold_year,
            sold_month,
            condition,
            max_price,
            min_price,
            avg_price,
            sample_count
        )
        SELECT
            sr.model_id                             AS model_id,
            YEAR(sr.sold_at)                        AS sold_year,
            MONTH(sr.sold_at)                       AS sold_month,
            sr.condition                            AS condition,
            MAX(sr.price)                           AS max_price,
            MIN(sr.price)                           AS min_price,
            AVG(sr.price)                           AS avg_price,
            COUNT(*)                                AS sample_count
        FROM sale_record sr
        WHERE sr.sold_at >= :cutoff
        GROUP BY
            sr.model_id,
            YEAR(sr.sold_at),
            MONTH(sr.sold_at),
            sr.condition
        ON DUPLICATE KEY UPDATE
            max_price    = VALUES(max_price),
            min_price    = VALUES(min_price),
            avg_price    = VALUES(avg_price),
            sample_count = VALUES(sample_count);
    """)

    with engine.begin() as conn:
        result = conn.execute(sql, {"cutoff": cutoff_date})
        print(f"[Snapshot] Upserted approximately {result.rowcount} rows "
              f"for the last {months_back} months.")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    test_connection()
    sync_sale_records()
    sync_model_price_snapshots(months_back=12)


if __name__ == "__main__":
    main()
