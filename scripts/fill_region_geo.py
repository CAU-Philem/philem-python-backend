import os
import time
import requests
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv()

KAKAO_REST_KEY = os.environ["KAKAO_REST_KEY"]
HEADERS = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

engine = create_engine(os.environ["DATABASE_URL"], future=True)

def search_kakao(query: str):
    r = requests.get(
        KAKAO_URL,
        headers=HEADERS,
        params={"query": query, "size": 1},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()["documents"]

def run():
    with engine.begin() as conn:
        regions = conn.execute(text("""
            SELECT id, name
            FROM regions
            WHERE lat IS NULL OR lng IS NULL
        """)).mappings().all()

    for r in regions:
        region_id = r["id"]
        name = r["name"]

        try:
            docs = search_kakao(f"서울 {name}")
            if not docs:
                print(f"[SKIP] {name} (no result)")
                continue

            doc = docs[0]
            lat = float(doc["y"])
            lng = float(doc["x"])

            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE regions
                    SET lat = :lat, lng = :lng
                    WHERE id = :id
                """), {
                    "id": region_id,
                    "lat": lat,
                    "lng": lng,
                })

            print(f"[OK] {name} -> {lat}, {lng}")

        except Exception as e:
            print(f"[ERROR] {name}: {e}")

        time.sleep(0.15)  # 카카오 rate limit 보호

if __name__ == "__main__":
    run()
