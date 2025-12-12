import os
import re
import time
import json
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from sqlalchemy import create_engine
from sshtunnel import SSHTunnelForwarder

from config.settings import settings

from core.listing_processor import process_listing

# =========================================================
# 🔧 0. 로컬 환경 설정 (환경변수 X, 직접 입력)
# =========================================================

# .env로 이동, config/settings.py 에서 관리

# =========================================================
# 1. OpenAI 키 설정
# =========================================================

api_keys = settings.openai_api_keys
if not api_keys:
    raise RuntimeError("⛔ OPENAI_API_KEYS 값이 비어 있음!")

current_key_index = 0
client = OpenAI(api_key=api_keys[current_key_index])


# =========================================================
# 2. JSON 파싱 유틸
# =========================================================


def extract_json_from_text(text: str):
    if not text:
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except:
        pass

    try:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group())
    except:
        pass

    return None


# =========================================================
# 3. 브랜드/역할 설정
# =========================================================

# core/listing_processor_context.py 으로 이동

# =========================================================
# 4. 브랜드 정규화
# =========================================================


# core/model_utils.py 로 이동


# =========================================================
# 5. 모델명 정규화 normalize_model_name()
# =========================================================


# core/model_utils.py 로 이동


# =========================================================
# 6. itemModel 캐시 + 생성
# =========================================================

# get_or_create_model_full_spec() 을 core/listing_processor_context.py 로 이동


# =========================================================
# 🔥 OpenAI 분석 로직(중략): 이전 대화에서 사용하던 것 그대로 유지
# =========================================================
# (너무 길어 생략… 너가 쓰던 analyze_camera_data_openai 그대로 붙이면 됨)
# =========================================================
def analyze_camera_data_openai(
    title, description, original_price, timeout_sec: int = 25
):
    """
    - 개별 판매글(title, description, original_price)을 분석해서
      JSON(dict) 형태로 반환.
    - 전체 호출 시간 timeout_sec 초를 넘기면 None 반환.
    """
    global client, current_key_index, api_keys

    if not api_keys:
        return None

    full_text = f"""
    [판매글 정보]
    - 제목: {title}
    - 판매자 설정 가격: {original_price}원
    - 본문 내용: {description if description else '내용 없음'}
    """

    system_prompt = """
    당신은 중고 카메라 거래 글을 분석하는 카메라 전문가 AI입니다.

    ## 1. 목표
    - 판매글에서 **카메라 바디 / 렌즈 / 일체형 카메라** 정보를 추출합니다.
    - 결과는 **JSON 객체 1개만** 출력하고, 최상위 키는 항상 "items" 여야 합니다.
    - 각 item의 `standard_name` 은 **제조사 카탈로그에 나오는 것처럼 정규화된 공식 제품명**이어야 합니다.
      - 같은 제품은 언제나 완전히 동일한 `standard_name` 으로 표현되어야 합니다.

    ## 2. 브랜드 제한 (이 브랜드만 처리)
    다음 브랜드에 속하는 제품만 items에 넣으세요.
    - Sony, Nikon, Canon, Fujifilm, Tamron, Sigma, Samyang, Viltrox

    다른 브랜드의 카메라/렌즈는 items에 넣지 말고, 필요하면 skip_reason에만 언급하세요.

    ## 3. standard_name 정규화 규칙 (브랜드 공통)
    - 표기 방식은 "브랜드 + 공식 제품명" 형태로 통일합니다.
      - 예: "Sony A7 III", "Nikon D5100", "Canon EOS 6D Mark II", "Fujifilm X-T30 II"
    - 판매글에 여러 별칭/축약이 나오더라도, 하나의 공식 이름으로 통일합니다.
      - 예시:
        - "a7m3", "A7III", "A7 mk3", "ILCE-7M3" → **Sony A7 III**
        - "6D Mark2", "6D2", "6D mkII" → **Canon EOS 6D Mark II**
        - "D5천백", "디5100", "D5100 바디" → **Nikon D5100**
        - "xt30ii", "X-T30 2세대" → **Fujifilm X-T30 II**
    - 한글 명칭/별명/오타(예: "알파7M3", "알6", "번들줌즈")만 있어도, 당신의 카메라 지식을 사용해 실제 제품명을 추론하고 **영문 공식 이름으로 표기**해야 합니다.

    ## 4. Role 분류
    - "BODY"         : 렌즈 교환식 카메라 바디
    - "LENS"         : 교환식 렌즈
    - "INTEGRATED"   : 고급 일체형(예: Sony RX, Sony ZV-1, Fujifilm X100, Ricoh GR 등)
    - "BUNDLE_UNKNOWN": 바디/렌즈 여러 개가 섞여 있고 개별 가격 분리가 힘든 경우
    - "ACCESSORY"    : 배터리, 가방, 필터, 삼각대, 메모리 카드 등 (가능하면 items에 넣지 말 것)
    - "OTHER"        : 카메라/렌즈가 아닌 경우

    ## 5. 바디(BODY)의 마운트 / 센서 / 타입
    BODY인 경우 반드시 다음을 추론하세요:
    - camera_type : "Mirrorless" / "DSLR" / "Compact" 중 하나
    - mount       : ['E', 'Z', 'F', 'RF', 'EF', 'EF-M', 'X', 'G'] 중 하나 또는 null
        마운트 정보는 무조건 정확하게 기입해야 합니다. 모르면 null 로 두세요.
    - sensor_format: ['Full Frame', 'APS-C', 'Micro Four Thirds', 'Medium Format', '1 inch'] 중 하나 또는 null

    예시(일부):
    - Nikon Z50/Z30/Zfc  → Mirrorless, 'Z', 'APS-C'
    - Nikon Z5/6/7/8/9/Zf → Mirrorless, 'Z', 'Full Frame'
    - Nikon D5100/D7200/D750/D850 → DSLR, 'F', 센서 포맷은 실제 모델에 맞게
    - Sony A7 시리즈 → Mirrorless, 'E', 'Full Frame'
    - Sony A6000~A6700 → Mirrorless, 'E', 'APS-C'
    - Canon EOS 5D/6D/7D/80D 등 DSLR → 'EF', camera_type='DSLR'
    - Canon EOS R3/R5/R6 등 → Mirrorless, 'RF', 'Full Frame'
    - Canon R7/R10/R50/R100 → Mirrorless, 'RF', 'APS-C'
    - Fujifilm X-T/X-S/X-H/X-Pro/X-E/X-A → Mirrorless, 'X', 'APS-C'

    ## 6. 렌즈(LENS)의 마운트 / 센서 포맷 / 타입
    LENS 항목에 대해서도 **반드시** 다음을 채워야 합니다:
    - mount        : 이 렌즈가 사용하는 마운트 ('F', 'EF', 'RF', 'Z', 'E', 'X', 'G' 등)
    - sensor_format: 이 렌즈의 이미지 서클 크기
                     - 풀프레임용: 'Full Frame'
                     - 크롭 전용(DX, DC, Di II, X-mount APS-C 등): 'APS-C'
    - image_circle : sensor_format와 동일하게 설정합니다.
    - camera_type  : 이 렌즈를 사용하는 바디 기준으로 "Mirrorless" 또는 "DSLR"

    예시(일부):
    - "Tamron 17-50mm F2.8 Di II (Nikon용)" → brand="Tamron", role="LENS",
      mount='F', camera_type="DSLR", sensor_format='APS-C', image_circle='APS-C'
    - "Sigma 10-20mm F3.5 DC HSM for Canon" → mount='EF', camera_type="DSLR", sensor_format='APS-C'
    - "Tamron 28-75mm F2.8 Di III RXD (Sony E)" → mount='E', camera_type="Mirrorless", sensor_format='Full Frame'
    - "Samyang AF 85mm F1.4 FE" → mount='E', camera_type="Mirrorless", sensor_format='Full Frame'
    - Tamron, Sigma, Samyang, Viltrox 같은 서드파티 렌즈의 경우 `standard_name` 끝에는 반드시 마운트를 괄호로 표기해야 합니다.
      예: "Tamron 17-70mm F2.8 Di III-A VC RXD (E)", "Sigma 30mm F1.4 DC DN (X)"
    ## 7. 번들렌즈(kit lens) 처리
    판매글에 "번들렌즈", "번들줌", "킷렌즈" 등만 적혀 있고 정확한 모델명이 없어도,
    바디 모델과 내용(초점거리, 조리개, OSS/VR 등)을 보고 가장 일반적인 번들렌즈를 추론하여
    **정규화된 공식 제품명으로 standard_name을 채워야 합니다.**

    예시:
    - Sony A6000 번들 → "Sony E PZ 16-50mm F3.5-5.6 OSS"
    - Sony A6400 번들 → "Sony E PZ 16-50mm F3.5-5.6 OSS" (일반적인 킷 구성)
    - Nikon D5100 번들 → "Nikon AF-S DX 18-55mm F3.5-5.6G VR" 계열
    - Canon 700D 번들 → "Canon EF-S 18-55mm F3.5-5.6 IS" 계열
    - Fujifilm X-T30 번들 → "Fujinon XF 18-55mm F2.8-4 R LM OIS" 또는 X-T30 보급 번들 구성을 추론

    **절대 "번들줌", "번들렌즈" 같은 표현을 standard_name에 그대로 쓰지 마세요.**
    항상 구체적인 초점거리/조리개 정보를 포함한 공식 제품명으로 변환해야 합니다.

    ## 8. 상태 / 보증 / 가격
    -condition: 상태 등급 규칙 (null 금지, 반드시 A/B/C 중 하나 선택)
    condition 값은 절대로 null 로 두지 말고, 항상 'A', 'B', 'C' 중 하나를 선택해야 합니다.

    - 'A' : 거의 새것에 가까운 상태
      - 예: "미개봉", "새상품", "거의 사용 안 함", "사용감 거의 없음", "기스/스크래치 없음",
          "컷수 매우 적음" 등
    - 'B' : 정상 작동하고, 일반적인 사용감/생활기스가 있는 상태
      - 예: "생활 기스 있음", "사용감 조금 있음", "외관 깔끔하지만 기스 약간" 등
      - 설명이 애매해서 등급 판단이 어렵다면 기본적으로 'B' 로 두세요.
    - 'C' : 정상 작동이 어렵거나, 외관/고장이 심각한 상태
      - 예: "고장", "전원 불량", "수리 필요", "부품용", "렌즈 곰팡이 심함",
            "HUD 깨짐", "충격 이력 있음" 등의 표현이 있을 때
    요약:
    - 상태 표현이 아주 좋으면 → 'A'
    - 정상 작동 & 일반적인 중고 느낌 또는 애매함 → 'B'
    - 고장/수리 필요/심각한 손상이 명시 → 'C'

    condition 은 반드시 'A' 또는 'B' 또는 'C' 중 하나여야 하며, null 은 절대 사용하지 마세요.
    - warranty: true / false (남은 보증, 영수증, 공식 리퍼 보증 등이 명시되면 true)
    - price: 정수 (원 단위). 개별 가격이 명시된 경우 그 값 사용.
      - 개별 가격이 없고 묶음으로만 가격이 있는 경우:
        - role을 "BUNDLE_UNKNOWN"으로 설정하고,
        - price는 0으로 두어도 됩니다. (호출하는 쪽 코드에서 전체 가격을 사용)

    ## 9. 출력 형식 (엄격)
    다음 JSON 스키마를 **정확히** 지키며, JSON 외에 다른 텍스트는 절대 출력하지 마세요.

    {
      "items": [
        {
          "standard_name": string,        // 정규화된 제품명 (공식 카탈로그 스타일)
          "brand": string,                // Sony, Nikon, Canon, Fujifilm, Tamron, Sigma, Samyang, Viltrox 중 하나
          "role": string,                 // 'BODY', 'LENS', 'INTEGRATED', 'BUNDLE_UNKNOWN', 'ACCESSORY', 'OTHER'
          "camera_type": string | null,   // 'Mirrorless', 'DSLR', 'Compact' 또는 null
          "mount": string | null,         // 'E', 'Z', 'F', 'RF', 'EF', 'EF-M', 'X', 'G' 중 하나 또는 null
          "sensor_format": string | null, // 'Full Frame', 'APS-C', 'Micro Four Thirds', 'Medium Format', '1 inch' 중 하나
          "image_circle": string | null,  // 렌즈인 경우 sensor_format와 동일, 아니면 null 가능
          "price": integer,               // 원 단위 숫자, 개별 가격 없으면 0
          "warranty": boolean | null,
          "condition": string | null      // 'A','B','C','D' 중 하나
        }
      ],
      "skip_reason": string | null       // 관련 제품이 없거나, 지원 브랜드가 없는 경우 이유 (옵션)
    }

    - 관련 제품이 전혀 없으면: "items": [] 로 출력하세요.
    """

    max_retries = len(api_keys) * 2
    start_time = time.time()

    for attempt in range(max_retries):
        # 전체 타임아웃 체크
        elapsed = time.time() - start_time
        if elapsed > timeout_sec:
            print(f"⏱️ OpenAI 전체 타임아웃 초과 ({timeout_sec}초) → None 반환")
            return None

        # 이번 시도에서 남은 시간(최소 5초는 보장)
        remaining = max(5, timeout_sec - elapsed)

        try:
            response = client.chat.completions.create(
                # model="gpt-4o-mini",
                model="gpt-5.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_text},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=remaining,  # 이 호출에서의 타임아웃
            )
            raw_content = response.choices[0].message.content
            if not raw_content:
                print("⚠️ OpenAI 응답은 왔으나 content가 비어 있음 → 재시도")
                continue

            json_data = extract_json_from_text(raw_content)
            if json_data is not None:
                return json_data

            print("⚠️ OpenAI 응답 파싱 실패 → 재시도")

        except Exception as e:
            err_msg = str(e).lower()

            # rate limit / quota 관련 → 다른 키로 교체 후 재시도
            if "quota" in err_msg or "429" in err_msg or "rate limit" in err_msg:
                print(f"🚧 OpenAI rate limit / quota 문제 → 키 교체 후 재시도: {e}")
                current_key_index = (current_key_index + 1) % len(api_keys)
                client = OpenAI(api_key=api_keys[current_key_index])
                time.sleep(1)
                continue

            # 타임아웃 또는 기타 에러 → 그냥 실패 처리
            print(f"🔥 OpenAI 호출 오류 (attempt={attempt+1}/{max_retries}): {e}")
            return None

    print("⚠️ OpenAI 재시도 한도 도달 → None 반환")
    return None


# =========================================================
# 7. 메인 실행부
# =========================================================

# =========================================================
# 0-1. 배치 크기 설정
# =========================================================
BATCH_SIZE = 30  # 원하면 50~100 정도로 올려도 됨


# =========================================================
# 0-2. needs_processing 플래그 관리용 헬퍼
# =========================================================

# mark_listing_done() 을 core/listing_processor_context.py 로 이동


def main():
    print("⏳ SSH 터널 연결 중...")

    server = SSHTunnelForwarder(
        (settings.ssh_host, 22),
        ssh_username=settings.ssh_user,
        ssh_pkey=settings.ssh_key_path,
        remote_bind_address=(settings.db_host, 3306),
    )
    server.start()

    local_port = server.local_bind_port
    print(f"🔌 로컬 포트 연결됨: {local_port}")

    engine = create_engine(
        f"mysql+pymysql://{settings.db_host}:{settings.db_password}"
        f"@127.0.0.1:{local_port}/{settings.db_name}"
    )

    with engine.connect() as conn:
        while True:
            print("📥 미분석 데이터 조회 중...")

            sql = f"""
            SELECT seq, id AS original_id, title, description, price, post_url
            FROM listing
            WHERE needs_processing = 1
            ORDER BY seq
            LIMIT {BATCH_SIZE};
            """

            df = pd.read_sql(sql, conn)

            if df.empty:
                print("🎉 더 이상 미분석 데이터 없음 → 전체 처리 완료!")
                break

            print(f"⚡ {len(df)}건 병렬 분석 시작!")

            # 1단계: OpenAI만 병렬 호출
            def process_row(row):
                try:
                    res = analyze_camera_data_openai(
                        row["title"],
                        row["description"],
                        row["price"],
                    )
                    return {"row": row, "res": res}
                except Exception as e:
                    print(f"❌ row 처리 오류 (seq={row['seq']}): {e}")
                    return {"row": row, "res": None}

            results = []
            with ThreadPoolExecutor(max_workers=3) as exe:
                futures = [exe.submit(process_row, r) for _, r in df.iterrows()]
                for f in as_completed(futures):
                    results.append(f.result())

            # 2단계: DB 작업은 메인 쓰레드에서 순차 처리
            for data in results:
                row = data["row"]
                result = data["res"]

                process_listing(conn, row, result)

        print("💾 전체 배치 처리 완료!")

    server.stop()
    print("🔒 SSH 터널 종료")


if __name__ == "__main__":
    main()
