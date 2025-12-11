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

def process_listing(conn, row, result):
    listing_id = int(row["seq"])

    post_url = row["post_url"]
    if post_url and isinstance(post_url, str) and post_url.startswith("/"):
        full_url = "https://www.daangn.com" + post_url
    else:
        full_url = post_url

    row_index = listing_id

    # 이거 반환할거임
    model_ids = []

    try:
        # 1) OpenAI 실패 / 파싱 실패 처리
        if not result:
            print(f"[{row_index}] ❌ OpenAI 응답/파싱 실패 → done 표시")
            mark_listing_done(conn, listing_id)
            conn.commit()
            return model_ids

        items = result.get("items", [])
        if not isinstance(items, list):
            print(f"[{row_index}] ❌ items 형식 오류 → done 표시")
            mark_listing_done(conn, listing_id)
            conn.commit()
            return model_ids

        # 2) 브랜드/역할 필터링
        filtered_items = []
        for it in items:
            role = it.get("role")
            brand_key_val = brand_key(it.get("brand"))
            if role not in VALID_ROLES:
                continue
            if brand_key_val not in VALID_BRANDS:
                continue
            filtered_items.append(it)

        # 타겟 없음 → ListingItem 없이 플래그만 0
        if not filtered_items:
            print(f"[{row_index}] 🚫 제외 (브랜드/역할 조건 미충족)")
            mark_listing_done(conn, listing_id)
            conn.commit()
            return model_ids

        target_items = filtered_items
        is_bundle = len(target_items) > 1 or (
            len(target_items) == 1
            and target_items[0].get("role") == "BUNDLE_UNKNOWN"
        )
        current_bundle_index = 1

        # 🔍 listing 원래 가격 필요
        listing_price = row["price"]

        # 🔥 여기서부터가 핵심: 기존 데이터 삭제 후 새로 쓰기
        conn.execute(
            text("DELETE FROM ListingItem WHERE listing_id = :lid"),
            {"lid": listing_id},
        )
        conn.execute(
            text("DELETE FROM Bundles WHERE listing_id = :lid"),
            {"lid": listing_id},
        )

        # 3) 새 결과 INSERT
        for item in target_items:
            model_id = get_or_create_model_full_spec(conn, item)
            model_ids.append(model_id)
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
                text(
                    """
                    INSERT INTO ListingItem
                    (listing_id, post_url, model_id, unit_type, price_type, price,
                    bundle_index, warranty, `condition`)
                    VALUES (:lid, :url, :mid, :ut, :pt, :pr, :bidx, :wr, :cond)
                    """
                ),
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

        if is_bundle:
            conn.execute(
                text(
                    """
                    INSERT INTO Bundles (listing_id, bundle_index, total_price)
                    VALUES (:lid, :bidx, :tot)
                    """
                ),
                {
                    "lid": listing_id,
                    "bidx": current_bundle_index,
                    "tot": listing_price,
                },
            )
            print(f"[{row_index}] 💾 저장 (Bundle)")
        else:
            print(f"[{row_index}] 💾 저장 (Single)")

        mark_listing_done(conn, listing_id)
        conn.commit()

        return model_ids

    except Exception as e:
        conn.rollback()
        print(f"[{row_index}] ❌ DB 처리 중 예외 발생 → 롤백: {e}")
        return []