import os
import sys
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz

# ==========================================
# 1. 시크릿 환경변수 로드
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TOSS_COOKIE = os.getenv("TOSS_COOKIE")
TOSS_XSRF = os.getenv("TOSS_XSRF")

# 감시할 주요 후보군 (삼성전자, SK하이닉스, 에코프로, 에코프로비엠, 한신공영 등)
TARGET_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "086520": "에코프로",
    "247540": "에코프로비엠",
    "004960": "한신공영",
    "005380": "현대차",
    "068270": "셀트리온",
    "034020": "두산에너빌리티"
}

TRADE_BUDGET_KRW = 50000  # 회당 주문 금액 (5만원)

# ==========================================
# 2. 통신 및 주문 함수
# ==========================================
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[텔레그램 스킵] {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=7)
    except Exception as e:
        print(f"[텔레그램 에러] {e}")

def execute_toss_order(ticker: str, current_price: float):
    """토스증권 웹 API 시장가 매수 주문 집행"""
    if not TOSS_COOKIE or not TOSS_XSRF:
        return "토스 세션 미설정 (모의 체결 완료)"
    
    qty = int(TRADE_BUDGET_KRW / current_price)
    if qty < 1:
        return "수량 미달 (주문 최소 금액 부족)"

    headers = {
        "Cookie": TOSS_COOKIE,
        "x-xsrf-token": TOSS_XSRF,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    url = "https://api.tossinvest.com/v1/order/buy"
    payload = {
        "productNumber": ticker,
        "orderQty": qty,
        "orderPrice": int(current_price),
        "orderType": "MARKET"
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=8)
        if res.status_code == 200:
            return f"토스 실전 체결 성공 ({qty}주)"
        elif res.status_code in [401, 403]:
            return "토스 쿠키 만료 (세션 갱신 필요)"
        else:
            return f"토스 응답 거부 ({res.status_code})"
    except Exception as e:
        return f"통신 에러 ({e})"

# ==========================================
# 3. 퀀트 지표 산출 & 스캔 로직
# ==========================================
def analyze_and_trade():
    kst_now = datetime.now(pytz.timezone('Asia/Seoul')).strftime("%Y-%m-%d %H:%M:%S")
    signals = []

    for code, name in TARGET_TICKERS.items():
        try:
            # 야후 파이낸스 데이터 로드
            df = yf.download(f"{code}.KS", period="6mo", interval="1d", progress=False)
            if df.empty:
                df = yf.download(f"{code}.KQ", period="6mo", interval="1d", progress=False)
            if len(df) < 60:
                continue

            # 지표 계산: 120일 기준선, 20일 MA, 엔벨로프(15%), RSI
            high120 = df['High'].rolling(120, min_periods=1).max()
            low120 = df['Low'].rolling(120, min_periods=1).min()
            base120 = (high120 + low120) / 2
            
            ma20 = df['Close'].rolling(window=20).mean()
            env_lower = ma20 * 0.85
            
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rsi = 100 - (100 / (1 + (gain / loss)))
            
            # 수급 데이터 (거래대금)
            txn_amt = df['Volume'] * df['Close']
            avg_txn = txn_amt.rolling(10).mean()

            c_curr = float(df['Close'].iloc[-1])
            c_prev = float(df['Close'].iloc[-2])
            c_prev2 = float(df['Close'].iloc[-3])

            # 매매 조건식
            is_upper_limit = (c_prev >= c_prev2 * 1.29)
            is_vol_burst = float(txn_amt.iloc[-1]) > float(avg_txn.iloc[-1]) * 2
            is_envelope_touch = (c_prev < float(env_lower.iloc[-2])) and (c_curr >= float(env_lower.iloc[-1]))
            is_base120_breakout = (c_curr > float(base120.iloc[-1])) and (float(rsi.iloc[-1]) > 58)

            trigger_reason = ""
            if is_upper_limit and is_vol_burst:
                trigger_reason = "상한가 눌림목 + 거래대금 급증"
            elif is_envelope_touch:
                trigger_reason = "엔벨로프 하단 낙폭과대 반등"
            elif is_base120_breakout:
                trigger_reason = "120일 기준선 상방 돌파 (수급 우위)"

            if trigger_reason:
                order_result = execute_toss_order(code, c_curr)
                signals.append(f"• <b>{name} ({code})</b>: {c_curr:,.0f}원\n  - 시그널: {trigger_reason}\n  - 결과: {order_result}")

        except Exception as err:
            print(f"[{code}] 분석 오류: {err}")
            continue

    if signals:
        report = f"🚨 <b>[PRIME 깃허브 클라우드 자동매매 집행]</b>\n확인 시각: {kst_now}\n\n" + "\n\n".join(signals)
        send_telegram(report)
    else:
        print(f"[{kst_now}] 조건 부합 종목 없음 (패스)")

if __name__ == "__main__":
    analyze_and_trade()
