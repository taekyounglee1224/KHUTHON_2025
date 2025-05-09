from flask import Flask, render_template,  request, redirect, url_for
import requests
import os
import pandas as pd
from datetime import datetime
VRWORLD_ADDR_KEY = 'B4C5E64B-E7B8-3103-9156-289899AE4279'
VRWORLD_DATA_KEY = '5EE19860-B1C7-3082-B027-99245C4FC9BE'
os.environ['VRWORLD_ADDR_KEY'] = VRWORLD_ADDR_KEY
os.environ['VRWORLD_DATA_KEY'] = VRWORLD_DATA_KEY

# irrigation.py 에 정의된 함수들 import
from irrigation import (
    get_coords_from_vworld,
    fetch_soil_drainage_with_lambda,
    calculate_irrigation
)

app = Flask(__name__)

# 1) GET '/' → 입력 폼(index.html) 렌더링
@app.route('/', methods=['GET'])
def form():
    return render_template('index.html')


# 2) GET '/results' 로 직접 접근하면 다시 폼으로 리다이렉트
@app.route('/results', methods=['GET'])
def result_get():
    return redirect(url_for('form'))


# 3) POST '/results' → 폼에서 넘어온 데이터로 계산 수행 후 결과(result.html) 렌더링
@app.route('/results', methods=['POST'])
def result():
    # (1) 사용자 입력 수집
    start_date = request.form['start_date']
    today      = request.form['today']
    crop       = request.form['crop']
    address    = request.form['address']
    area       = float(request.form['area'])

    # (2) 작물별 Kc 계산
    df_crop   = pd.read_csv('csv/작물별_Kc.csv', encoding='utf-8-sig')
    row       = df_crop[df_crop['작물 종류'] == crop].iloc[0]
    total_days= row['생육일수']
    days_passed = abs((datetime.strptime(today, "%Y-%m-%d")
                      - datetime.strptime(start_date, "%Y-%m-%d")).days)
    ini, mid = int(total_days * 0.2), int(total_days * 0.6)
    if days_passed <= ini:
        kc = row['Kc_ini']
    elif days_passed <= ini + mid:
        kc = row['Kc_mid']
    else:
        kc = row['Kc_end']

    # (3) 주소→위경도→배수등급(λ) 조회
    lon, lat = get_coords_from_vworld(address, os.environ['VRWORLD_ADDR_KEY'])
    soil_df  = fetch_soil_drainage_with_lambda(lon, lat, os.environ['VRWORLD_DATA_KEY'])
    lam      = soil_df['lambda'].iloc[0]

    # (4) 미리 생성된 weather.csv 읽기
    df_weather = pd.read_csv('results/weather.csv')

    # (5) 관개량 계산
    df_irrig = calculate_irrigation(df_weather, lam=lam, kc=kc, init_res=0)

    # (6) 오늘 날짜 관개량 & 전체 물량 계산
    today_dt       = datetime.strptime(today, "%Y-%m-%d")
    irrig_per_m2   = df_irrig.loc[df_irrig['date'] == today_dt, 'irrigation'].iloc[0]
    total_water    = irrig_per_m2 * area

    # (7) 결과 페이지 렌더
    return render_template(
        'results.html',
        irrig     = irrig_per_m2,
        total     = total_water,
        unit_area = area,
        today     = today
    )


if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 3000))   # Replit이 주는 PORT, 기본 3000
    app.run(host='0.0.0.0', port=port, debug=True)