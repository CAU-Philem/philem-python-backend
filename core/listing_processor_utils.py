from sqlalchemy import text
import re
from .listing_processor_context import (
    VALID_MOUNTS,
    THIRD_PARTY_BRANDS
)

def canonical_brand(brand: str | None) -> str:
    if not brand:
        return ""

    b = brand.strip().lower()
    mapping = {
        "sony": "Sony",
        "nikon": "Nikon",
        "canon": "Canon",
        "fujifilm": "Fujifilm",
        "fuji": "Fujifilm",
        "sigma": "Sigma",
        "tamron": "Tamron",
        "samyang": "Samyang",
        "viltrox": "Viltrox",
    }
    return mapping.get(b, brand.strip().capitalize())

def normalize_model_name(brand, raw_name, role=None, mount=None):
    if not raw_name:
        raw_name = "Unknown Model"

    name = raw_name.strip()
    if not name:
        return "Unknown Model"

    brand_norm = canonical_brand(brand)
    lower_name = name.lower()

    if brand_norm and lower_name.startswith(brand_norm.lower()):
        model_part = name[len(brand_norm) :].strip()
    else:
        model_part = name

    model_part = model_part.replace("α", "alpha")
    model_part = re.sub(r"\s+", " ", model_part).strip()

    # ---------- SONY ----------
    if brand_norm == "Sony":
        # alpha → A 형식 통일
        m = re.search(r"alpha\s*(a?\s*\d{3,4}[ivx]*)", model_part, re.IGNORECASE)
        if m:
            code = re.sub(r"\s+", "", m.group(1))
            code = code.upper()
            if not code.startswith("A"):
                code = "A" + code
            model_part = code

        m = re.search(r"^a\s*(\d{3,4}[ivx]*)", model_part, re.IGNORECASE)
        if m:
            model_part = "A" + m.group(1).upper()

        # 렌즈는 (E)(FE) 같은 거 삭제
        if role == "LENS":
            model_part = re.sub(
                r"\s*\(\s*(E|FE|E-MOUNT|E MOUNT)\s*\)\s*$",
                "",
                model_part,
                flags=re.IGNORECASE,
            ).strip()

    # ---------- Nikon ----------
    if brand_norm == "Nikon":
        model_part = re.sub(
            r"\b([dz])\s+(\d)",
            lambda m: m.group(1).upper() + m.group(2),
            model_part,
            flags=re.IGNORECASE,
        )

    # ---------- Canon ----------
    if brand_norm == "Canon":
        model_part = re.sub(r"\beos\s*", "EOS ", model_part, flags=re.IGNORECASE)
        model_part = re.sub(r"\s+", " ", model_part).strip()

    # ---------- Fujifilm ----------
    if brand_norm == "Fujifilm":

        def fix_x_body(m):
            code = m.group(1).upper()
            return f"X-{code}"

        model_part = re.sub(
            r"\bx[\s\-]*([thse]\d{1,3}ii|\d{1,3})",
            fix_x_body,
            model_part,
            flags=re.IGNORECASE,
        )

        model_part = re.sub(r"\bxf\s*", "XF ", model_part, flags=re.IGNORECASE)
        model_part = re.sub(r"\bxc\s*", "XC ", model_part, flags=re.IGNORECASE)
        model_part = re.sub(r"\s+", " ", model_part).strip()

    # ---------- Third-party 렌즈 ----------
    mount_norm = mount if mount in VALID_MOUNTS else None

    if role == "LENS" and brand_norm in THIRD_PARTY_BRANDS and mount_norm:
        # (Canon용), (for Nikon) 등 괄호 제거
        model_part = re.sub(r"\s*\([^)]*\)\s*$", "", model_part).strip()
        model_part = re.sub(
            r"\s+for\s+(canon|nikon|sony|fujifilm|fuji)\b.*$",
            "",
            model_part,
            flags=re.IGNORECASE,
        ).strip()
        model_part = f"{model_part} ({mount_norm})"

    # 조립
    if brand_norm:
        final_name = f"{brand_norm} {model_part}"
    else:
        final_name = model_part

    return final_name.strip()

MODEL_CACHE = {}

def get_or_create_model_full_spec(conn, item_data):
    raw_name = item_data.get("standard_name", "Unknown Model")
    brand = item_data.get("brand")
    role = item_data.get("role")
    mount = item_data.get("mount")

    std_name = normalize_model_name(brand, raw_name, role=role, mount=mount)[:250]

    # 캐시 확인
    if std_name in MODEL_CACHE:
        return MODEL_CACHE[std_name]

    row = conn.execute(
        text("SELECT id FROM itemModel WHERE name = :name"), {"name": std_name}
    ).fetchone()

    if row:
        model_id = row[0]
        MODEL_CACHE[std_name] = model_id
        return model_id

    # 센서 포맷 보정
    sensor_val = (
        item_data.get("sensor_format")
        or item_data.get("image_circle")
        or item_data.get("sensor")
    )

    brand_c = canonical_brand(brand)

    # 소니 렌즈 FE/E 판정
    if brand_c == "Sony" and role == "LENS" and mount == "E":
        if std_name.upper().startswith("SONY FE "):
            sensor_val = "Full Frame"
        else:
            sensor_val = "APS-C"

    res = conn.execute(
        text(
            """
        INSERT INTO itemModel
        (name, brand, unit_type, camera_type, mount, sensor_format, image_circle)
        VALUES (:nm, :br, :ut, :ct, :mt, :sf, :ic)
    """
        ),
        {
            "nm": std_name,
            "br": brand_c,
            "ut": role,
            "ct": item_data.get("camera_type"),
            "mt": mount,
            "sf": sensor_val,
            "ic": sensor_val,
        },
    )

    model_id = res.lastrowid
    MODEL_CACHE[std_name] = model_id
    return model_id

def brand_key(brand: str | None) -> str | None:
    """브랜드 문자열을 소문자 key로 정규화 (필터용)"""
    if not brand:
        return None
    return brand.strip().lower()

def mark_listing_done(conn, listing_id: int):
    """해당 listing을 처리 완료로 표시 (needs_processing = 0)"""
    conn.execute(
        text("UPDATE listing SET needs_processing = 0 WHERE seq = :lid"),
        {"lid": listing_id},
    )
    # 디버깅할 때 보고 싶으면 아래 주석 해제
    # print(f"   → listing.seq={listing_id} needs_processing=0 으로 업데이트")