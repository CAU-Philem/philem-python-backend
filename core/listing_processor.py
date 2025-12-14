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

    print(f"[{row_index}] DEBUG keys={list(row.keys())}")
    print(f"[{row_index}] DEBUG status={row.get('status')}")

    # 이거 반환할거임
    out = {
        "listing_id": listing_id,
        "post_url": full_url,       # for debugging perhaps
        "is_bundle": False,
        "bundle_index": None,
        "bundle_total_price": None,
        "items": []
    }

    try:
        # 1) OpenAI 실패 / 파싱 실패 처리
        if not result:
            print(f"[{row_index}] ❌ OpenAI 응답/파싱 실패 → done 표시")
            mark_listing_done(conn, listing_id)
            conn.commit()
            return out

        items = result.get("items", [])
        if not isinstance(items, list):
            print(f"[{row_index}] ❌ items 형식 오류 → done 표시")
            mark_listing_done(conn, listing_id)
            conn.commit()
            return out

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
            return out

        target_items = filtered_items
        is_bundle = len(target_items) > 1 or (
            len(target_items) == 1
            and target_items[0].get("role") == "BUNDLE_UNKNOWN"
        )
        current_bundle_index = 1

        # 🔍 listing 원래 가격 필요
        listing_price = row["price"]

        out["is_bundle"] = is_bundle
        out["bundle_index"] = current_bundle_index if is_bundle else None
        out["bundle_total_price"] = listing_price if is_bundle else None


        db_status = conn.execute(
            text("SELECT status FROM listing WHERE seq = :lid"),
            {"lid": listing_id},
        ).scalar()
        # ✅ [추가] 판매완료(SoldOut) listing은 절대 수정/삭제/재생성 하지 않음 (freeze)
        if db_status == "SoldOut":
            print(f"[{row_index}] 🧊 SoldOut(DB) → DB 수정/삭제 스킵 + 기존 ListingItem 반환")

            # 1) 기존 ListingItem 읽기
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

            # 2) 번들 정보도 있으면 채우기
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

            # 3) 처리 플래그만 0으로 (DB 내용은 안 바꿈)
            mark_listing_done(conn, listing_id)
            conn.commit()
            return out

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
            # model_ids.append(model_id)
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

            out["items"].append({
                "model_id": model_id,
                "condition": cond_val,
                "price": final_price,          # can be None
                "price_type": price_type,      # tells FE why None happened
                "role": role,
                "bundle_index": current_bundle_index if is_bundle else None,
            })


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

        return out

    except Exception as e:
        conn.rollback()
        print(f"[{row_index}] ❌ DB 처리 중 예외 발생 → 롤백: {e}")
        return {**out, "items": []}