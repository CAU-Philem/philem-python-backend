from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from api.deps import get_db
from sqlalchemy import text

from ingestion.single_input_crawler import process_single_url
from workers.listing_processor_main import analyze_camera_data_openai
from core.listing_processor import process_listing

router = APIRouter()

class UrlInput(BaseModel):
    url: str

@router.post("/listings/from-url")
def analyze_url(payload: UrlInput, db: Session = Depends(get_db)):
    try:
        listing_id = process_single_url(payload.url)
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
    model_ids = process_listing(db, row, ai)

    if not model_ids:
        return {
            "models": [],
        }

    # 5) Fetch model names for those ids
    models = db.execute(
        text(
            """
            SELECT id AS model_id, name AS model_name
            FROM itemModel
            WHERE id IN :ids
            """
        ),
        {"ids": tuple(set(model_ids))},
    ).mappings().all()

    models_list = [dict(m) for m in models]
    
    return {"models": models_list}
