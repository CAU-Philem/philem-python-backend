from sqlalchemy import text
from .listing_processor_context import (
    VALID_ROLES,
    VALID_BRANDS,
)
from .listing_processor_utils import (
    brand_key,
    get_or_create_model_full_spec,
    mark_listing_done
)

def _read_existing_items(conn, listing_id: int, full_url: str):
    """이미 처리된 listing이면 DB에 저장된 결과를 그대로 읽어서 out 생성"""
    out = {
        "listing_id": listing_id,
        "post_url": full_url,
        "is_bundle": False,
        "bundle_index": None,
        "bundle_total_price": None,
        "items": []
    }

    rows = conn.execute(
        text("""
            SELECT model_id, `condition`, price, price_type, unit_type, bundle_index
            FROM ListingItem
            WHERE listing_id = :lid
            ORDER BY id ASC
        """),
        {"lid": listing_id},
    ).mappings().fetchall()

    out["items"] = [
        {
            "model_id": r["model_id"],
            "condition": r["condition"],
            "price": r["price"],
            "price_type": r["price_type"],
            "role": r["unit_type"],
            "bundle_index": r["bundle_index"],
        }
        for r in rows
    ]

    b = conn.execute(
        text("""
            SELECT bundle_index, total_price
            FROM Bundles
            WHERE listing_id = :lid
            LIMIT 1
        """),
        {"lid": listing_id},
    ).mappings().fetchone()

    if b:
        out["is_bundle"] = True
        out["bundle_index"] = b["bundle_index"]
        out["bundle_total_price"] = b["total_price"]
    else:
        out["is_bundle"] = len(out["items"]) > 1

    return out

from sqlalchemy import text
from .listing_processor_context import VALID_ROLES, VALID_BRANDS
from .listing_processor_utils import brand_key, get_or_create_model_full_spec, mark_listing_done

def _read_existing_items(conn, listing_id: int, full_url: str):
    """이미 처리된 listing이면 DB에 저장된 결과를 그대로 읽어서 out 생성"""
    out = {
        "listing_id": listing_id,
        "post_url": full_url,
        "is_bundle": False,
        "bundle_index": None,
        "bundle_total_price": None,
        "items": []
    }

    rows = conn.execute(
        text("""
            SELECT model_id, `condition`, price, price_type, unit_type, bundle_index
            FROM ListingItem
            WHERE listing_id = :lid
            ORDER BY id ASC
        """),
        {"lid": listing_id},
    ).mappings().fetchall()

    out["items"] = [
        {
            "model_id": r["model_id"],
            "condition": r["condition"],
            "price": r["price"],
            "price_type": r["price_type"],
            "role": r["unit_type"],
            "bundle_index": r["bundle_index"],
        }
        for r in rows
    ]

    b = conn.execute(
        text("""
            SELECT bundle_index, total_price
            FROM Bundles
            WHERE listing_id = :lid
            LIMIT 1
        """),
        {"lid": listing_id},
    ).mappings().fetchone()

    if b:
        out["is_bundle"] = True
        out["bundle_index"] = b["bundle_index"]
        out["bundle_total_price"] = b["total_price"]
    else:
        out["is_bundle"] = len(out["items"]) > 1

    return out


def process_listing(conn, row, result):
    listing_id = int(row["seq"])

    post_url = row["post_url"]
    if post_url and isinstance(post_url, str) and post_url.startswith("/"):
        full_url = "https://www.daangn.com" + post_url
    else:
        full_url = post_url

    row_index = listing_id
    print(f"[{row_index}] DEBUG keys={list(row.keys())}")

    # ✅ (핵심) result가 None이면:
    # - OpenAI 실패일 수도 있지만,
    # - needs_processing==0 때문에 일부러 ai=None으로 넘긴 경우도 있음
    # 그래서 여기서는 '기존 결과 조회'로 처리
    if result is None:
        print(f"[{row_index}] ℹ️ result=None → 기존 ListingItem/Bundles 반환 모드")
        return _read_existing_items(conn, listing_id, full_url)

    out = {
        "listing_id": listing_id,
        "post_url": full_url,
        "is_bundle": False,
        "bundle_index": None,
        "bundle_total_price": None,
        "items": []
    }

    # -----------------------
    # 1) items 구조 확인
    # -----------------------
    items = result.get("items", [])
    if not isinstance(items, list):
        print(f"[{row_index}] ❌ items 형식 오류 → 처리만 완료 표시")
        mark_listing_done(conn, listing_id)
        return out

    # -----------------------
    # 2) 브랜드/역할 필터링
    # -----------------------
    filtered_items = []
    for it in items:
        role = it.get("role")
        brand_key_val = brand_key(it.get("brand"))
        if role not in VALID_ROLES:
            continue
        if brand_key_val not in VALID_BRANDS:
            continue
        filtered_items.append(it)

    if not filtered_items:
        print(f"[{row_index}] 🚫 제외 (브랜드/역할 조건 미충족) → 처리만 완료 표시")
        mark_listing_done(conn, listing_id)
        return out

    target_items = filtered_items
    is_bundle = len(target_items) > 1 or (
        len(target_items) == 1 and target_items[0].get("role") == "BUNDLE_UNKNOWN"
    )
    current_bundle_index = 1
    listing_price = row["price"]

    out["is_bundle"] = is_bundle
    out["bundle_index"] = current_bundle_index if is_bundle else None
    out["bundle_total_price"] = listing_price if is_bundle else None

    # -----------------------
    # 3) SoldOut 보호
    # -----------------------
    db_status = conn.execute(
        text("SELECT status FROM listing WHERE seq = :lid"),
        {"lid": listing_id},
    ).scalar()

    if db_status == "SoldOut":
        print(f"[{row_index}] 🧊 SoldOut(DB) → 기존 결과만 반환 + 처리 완료 표시")
        mark_listing_done(conn, listing_id)
        return _read_existing_items(conn, listing_id, full_url)

    # -----------------------
    # 4) 기존 결과 삭제 후 재생성
    # -----------------------
    conn.execute(text("DELETE FROM ListingItem WHERE listing_id = :lid"), {"lid": listing_id})
    conn.execute(text("DELETE FROM Bundles WHERE listing_id = :lid"), {"lid": listing_id})

    for item in target_items:
        model_id = get_or_create_model_full_spec(conn, item)
        role = item.get("role")
        ai_price = item.get("price", 0) or 0

        if role == "BUNDLE_UNKNOWN":
            price_type = "PER_ITEM"
            final_price = ai_price if ai_price > 0 else listing_price
        elif is_bundle:
            if ai_price == 0 or ai_price == listing_price:
                price_type = "BUNDLE_SHARED"
                final_price = None
            else:
                price_type = "PER_ITEM"
                final_price = ai_price
        else:
            price_type = "PER_ITEM"
            final_price = ai_price if ai_price > 0 else listing_price

        cond_raw = item.get("condition")
        cond_val = cond_raw if cond_raw in ("A", "B", "C") else "B"

        conn.execute(
            text("""
                INSERT INTO ListingItem
                (listing_id, post_url, model_id, unit_type, price_type, price,
                 bundle_index, warranty, `condition`)
                VALUES (:lid, :url, :mid, :ut, :pt, :pr, :bidx, :wr, :cond)
            """),
            {
                "lid": listing_id,
                "url": full_url,
                "mid": model_id,
                "ut": role,
                "pt": price_type,
                "pr": final_price,
                "bidx": current_bundle_index if is_bundle else None,
                "wr": item.get("warranty"),
                "cond": cond_val,
            },
        )

        out["items"].append({
            "model_id": model_id,
            "condition": cond_val,
            "price": final_price,
            "price_type": price_type,
            "role": role,
            "bundle_index": current_bundle_index if is_bundle else None,
        })

    if is_bundle:
        conn.execute(
            text("""
                INSERT INTO Bundles (listing_id, bundle_index, total_price)
                VALUES (:lid, :bidx, :tot)
            """),
            {"lid": listing_id, "bidx": current_bundle_index, "tot": listing_price},
        )

    mark_listing_done(conn, listing_id)
    return out