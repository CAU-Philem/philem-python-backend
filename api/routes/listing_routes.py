from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from api.deps import get_db
from sqlalchemy import text, bindparam
from sqlalchemy.exc import SQLAlchemyError

from ingestion.single_input_crawler import process_single_url
from ingestion.url_normalizer import normalize_daangn_url
from core.openai_analyzer import OpenAIAnalyzer
from core.listing_processor import process_listing
from config.settings import settings

router = APIRouter()

class UrlInput(BaseModel):
    url: str

ANALYZER = OpenAIAnalyzer(
    settings.openai_api_keys,
    settings.openai_model
)

@router.post("/listings/from-url")
def analyze_url(payload: UrlInput, db: Session = Depends(get_db)):
    canonical_url = normalize_daangn_url(payload.url)
    
    try:
        listing_id = process_single_url(db, canonical_url)
        db.flush()  # insert 직후 id 확정/세션 반영(필요없어도 안전)

        row = db.execute(
            text(
                """
                SELECT seq, id, title, description, price, post_url, needs_processing
                FROM listing
                WHERE id = :id
                """
            ),
            {"id": listing_id},
        ).mappings().fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Listing not found after insert")

        if row.get("needs_processing") == 0:
            ai = None
        else:
            ai = ANALYZER.analyze_camera_data_openai(row["title"], row["description"], row["price"])

        out = process_listing(db, row, ai)

        items = out.get("items", [])
        if items:
            model_ids = sorted({
                it["model_id"] for it in items
                if it.get("model_id") is not None
            })

            if model_ids:
                stmt = text("""
                    SELECT id AS model_id, name AS model_name
                    FROM itemModel
                    WHERE id IN :ids
                """).bindparams(bindparam("ids", expanding=True))

                models = db.execute(stmt, {"ids": model_ids}).mappings().all()
                model_name_map = {m["model_id"]: m["model_name"] for m in models}

                for it in out["items"]:
                    mid = it.get("model_id")
                    it["model_name"] = model_name_map.get(mid)

        db.commit()
        return out
    
    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")