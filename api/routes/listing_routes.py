from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from api.deps import get_db
from sqlalchemy import text, bindparam

from ingestion.single_input_crawler import process_single_url
from ingestion.url_normalizer import normalize_daangn_url
from workers.listing_processor_main import analyze_camera_data_openai
from core.listing_processor import process_listing

router = APIRouter()

class UrlInput(BaseModel):
    url: str

@router.post("/listings/from-url")
def analyze_url(payload: UrlInput, db: Session = Depends(get_db)):
    canonical_url = normalize_daangn_url(payload.url)

    try:
        listing_id = process_single_url(canonical_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Crawl failed: {str(e)}")

    row = db.execute(
        text(
            """
            SELECT seq, id, title, description, price, post_url
            FROM listing
            WHERE id = :id
            """
        ),
        {"id": listing_id},
    ).mappings().fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found after insert")

    ai = analyze_camera_data_openai(row["title"], row["description"], row["price"])

    out = process_listing(db, row, ai)

    items = out.get("items", [])
    if not items:
        # return full envelope so FE can still see bundle info, listing price, etc.
        return out

    # ✅ collect model_ids from out["items"]
    model_ids = sorted({it["model_id"] for it in items if it.get("model_id") is not None})
    if not model_ids:
        return out

    # ✅ correct SQLAlchemy IN usage (expanding)
    stmt = text("""
        SELECT id AS model_id, name AS model_name
        FROM itemModel
        WHERE id IN :ids
    """).bindparams(bindparam("ids", expanding=True))

    models = db.execute(stmt, {"ids": model_ids}).mappings().all()

    # map model_id -> model_name
    model_name_map = {m["model_id"]: m["model_name"] for m in models}

    # ✅ attach names onto each item (keeping condition/price/price_type)
    for it in out["items"]:
        mid = it.get("model_id")
        it["model_name"] = model_name_map.get(mid)

    return out