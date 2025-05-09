import requests
import pandas as pd
import xml.etree.ElementTree as ET
import time
import os
from datetime import datetime, timedelta

# ----------------------------
# 모듈 레벨: 함수 정의 & 경로 설정
# ----------------------------

BASE_DIR      = os.path.dirname(__file__)
CSV_DIR       = os.path.join(BASE_DIR, 'csv')
RESULTS_DIR   = os.path.join(BASE_DIR, 'results')

KC_FILE       = os.path.join(CSV_DIR, '작물별_Kc.csv')
STATION_FILE  = os.path.join(CSV_DIR, 'ASOS_station.csv')

v_addr  = 'B4C5E64B-E7B8-3103-9156-289899AE4279'
v_data  = '5EE19860-B1C7-3082-B027-99245C4FC9BE'


def get_coords_from_vworld(address: str, api_key: str) -> tuple[float, float]:
    """주소 → 위경도 변환"""
    url = "https://api.vworld.kr/req/address"
    params = {
        'service': 'address', 'version': '2.0', 'request': 'GetCoord',
        'format': 'json', 'key': api_key, 'type': 'ROAD',
        'address': address, 'crs': 'EPSG:4326'
    }
    res = requests.get(url, params=params, timeout=10)
    res.raise_for_status()
    resp = res.json().get('response', {})
    if resp.get('status') != 'OK':
        raise RuntimeError(f"VWorld 주소 변환 오류: {resp.get('error')}")

    result = resp.get('result', {})
    
    # ✅ 1차: items 혹은 item 존재 여부 확인
    items = result.get('items') or result.get('item')
    if items:
        feat = items[0] if isinstance(items, list) else items
        point = feat.get('point')
    else:
        # ✅ 2차: items가 없을 경우, point 키가 바로 있는 경우 사용
        point = result.get('point')
    
    if not point:
        raise RuntimeError(f"VWorld 좌표 정보 없음: {result}")

    return float(point['x']), float(point['y'])


def fetch_soil_drainage_with_lambda(lon: float, lat: float, data_api_key: str) -> pd.DataFrame:
    """배수등급 조회 → λ 매핑 반환"""
    url = 'https://api.vworld.kr/req/data'
    params = {
        'key': data_api_key, 'service': 'data', 'request': 'GetFeature',
        'data': 'LT_C_ASITSOILDRA', 'format': 'json', 'geometry': 'true',
        'page': '1', 'size': '1000', 'crs': 'EPSG:4326', 'domain': 'localhost',
        'geomFilter': f'POINT({lon} {lat})'
    }
    res = requests.get(url, params=params, timeout=10)
    res.raise_for_status()
    feats = res.json()['response']['result']['featureCollection']['features']
    soil_df = pd.DataFrame([f['properties'] for f in feats])
    drain_map = {
        '매우양호': 0.50, '양호': 0.35, '약간양호': 0.25,
        '약간불량': 0.15, '불량': 0.10, '매우불량': 0.05,
    }
    soil_df['lambda'] = soil_df['label'].map(drain_map)
    return soil_df[['label', 'lambda']]

def calculate_irrigation(df: pd.DataFrame, lam: float, kc: float, init_res: float = 0) -> pd.DataFrame:
    """ETo, ETc, 관개량·잔존수분 계산"""
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    for col in ['avg_temp', 'min_temp', 'max_temp', 'rainfall', 'sunshine']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['rainfall'].fillna(0, inplace=True)

    def _eto(r):
        if pd.notna(r['avg_temp']) and pd.notna(r['min_temp']) \
           and pd.notna(r['max_temp']) and pd.notna(r['sunshine']):
            return 0.0023 * (r['avg_temp'] + 17.8) \
                   * ((r['max_temp'] - r['min_temp']) ** 0.5) * r['sunshine']
        return 0

    df['ETo'] = df.apply(_eto, axis=1)
    df['ETc'] = df['ETo'] * kc
    df['Pe']  = df['rainfall'] * 0.8

    df['irrigation'] = 0.0
    df['residual']   = 0.0
    res_prev = init_res

    for idx, row in df.iterrows():
        P  = row['Pe']
        ET = row['ETc']
        I  = max(0, ET - (P + res_prev) + lam * (P + res_prev))
        res = max(0, res_prev - ET - (1 - lam) * (P + I))
        df.at[idx, 'irrigation'] = I
        df.at[idx, 'residual']   = res
        res_prev = res

    return df[[
        'date','avg_temp','min_temp','max_temp','rainfall',
        'ETo','ETc','Pe','irrigation','residual'
    ]]


# ----------------------------
# 스크립트 실행용 블록
# ----------------------------
if __name__ == '__main__':
    # 1) 사용자 입력: 날짜·작물
    start_date = input("재배 시작 날짜 (예: 2024-04-15): ")
    today      = input("오늘 날짜 입력 (예: 2024-05-20): ")
    crop       = input("작물명 입력 (예: 당근): ")

    # 2) 작물별 Kc 계산
    kc_df      = pd.read_csv(KC_FILE, encoding='utf-8-sig')
    row        = kc_df[kc_df['작물 종류'] == crop].iloc[0]
    total_days = row['생육일수']
    days_passed= abs((datetime.strptime(today, "%Y-%m-%d")
                   - datetime.strptime(start_date, "%Y-%m-%d")).days)
    ini, mid = int(total_days * 0.2), int(total_days * 0.6)
    if days_passed <= ini:
        kc = row['Kc_ini']
    elif days_passed <= ini + mid:
        kc = row['Kc_mid']
    else:
        kc = row['Kc_end']

    # 3) 주소 → 위경도 → λ 조회
    address = input("📍 조회할 주소: ")
    # v_addr  = os.environ.get('VRWORLD_ADDR_KEY', '')
    # v_data  = os.environ.get('VRWORLD_DATA_KEY', '')
    lon, lat = get_coords_from_vworld(address, v_addr)
    soil_df  = fetch_soil_drainage_with_lambda(lon, lat, v_data)

    # 4) 지점 코드 매핑
    station_df = pd.read_csv(STATION_FILE, encoding='utf-8-sig')
    station_map= dict(zip(station_df['지점명'], station_df['지점']))
    region     = input("📍 지점명 입력 (예: 서울): ").strip()
    stn_id     = station_map.get(region, '108')

    # 5) 일별 기상 데이터 수집 & CSV 저장
    service_key= 'OaE7WFXPyKXCPtSvtE9HuQdSwbzhl/C9FhjkxVzyOfKLRZxqAMChtLhArevfCux2XuluPYLtgDuMUEPXvGaoNQ=='
    asos_url   = 'http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList'
    params = {
        'serviceKey': service_key, 'pageNo': '1', 'numOfRows': '100',
        'dataType': 'XML', 'dataCd': 'ASOS', 'dateCd': 'DAY',
        'startDt': start_date.replace('-', ''), 'endDt': today.replace('-', ''),
        'stnIds': stn_id
    }
    all_data = []; page = 1
    while True:
        p = params.copy(); p['pageNo'] = str(page)
        resp = requests.get(asos_url, params=p)
        root = ET.fromstring(resp.content)
        items= root.findall('.//item')
        if not items: break
        for it in items:
            all_data.append({
                'date': it.findtext('tm'),
                'avg_temp': it.findtext('avgTa'),
                'min_temp': it.findtext('minTa'),
                'max_temp': it.findtext('maxTa'),
                'rainfall': it.findtext('sumRn'),
                'humidity': it.findtext('avgRhm'),
                'sunshine': it.findtext('sumGsr'),
                'wind': it.findtext('avgWs'),
                'air_pressure': it.findtext('avgPa')
            })
        total = int(root.findtext('.//totalCount'))
        per   = int(root.findtext('.//numOfRows'))
        if page * per >= total: break
        page += 1
        time.sleep(0.3)
        
    if not all_data:
        raise RuntimeError("❌ 기상 데이터가 한 건도 수집되지 않았습니다. 날짜 범위나 지점 코드를 확인하세요.")

    df_w = pd.DataFrame(all_data)
    df_w['date'] = pd.to_datetime(df_w['date'])
    for c in ['avg_temp','min_temp','max_temp','rainfall','humidity','sunshine','wind','air_pressure']:
        df_w[c] = pd.to_numeric(df_w[c], errors='coerce')
    df_w['rainfall'].fillna(0, inplace=True)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    wfile = os.path.join(RESULTS_DIR, 'weather.csv')
    df_w.to_csv(wfile, index=False, encoding='utf-8-sig')
    print(f"✔ Saved weather results to {wfile}")

    # 6) 관개량 계산
    df_weather= pd.read_csv(wfile)
    df_irrig  = calculate_irrigation(df_weather, lam=soil_df['lambda'].iloc[0], kc=kc)

    # 7) 결과 출력
    print(df_irrig.head())
    area = float(input("밭 면적을 입력 (m²): "))
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    irrig_per= df_irrig.loc[df_irrig['date']==today_dt, 'irrigation'].iloc[0]
    total    = irrig_per * area
    print(f"\n▶ 오늘 1 m²당 관개량: {irrig_per:.2f} L")
    print(f"▶ 밭 전체(면적 {area:.1f} m²) 필요 물량: {total:.2f} L")