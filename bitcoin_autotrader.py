"""
=============================================================
  업비트 비트코인 자동매매 봇
  전략: 듀얼 모멘텀 + 변동성 돌파 복합 전략
  배포: Railway.app (환경변수로 API 키 관리)
=============================================================
"""

import pyupbit
import pandas as pd
import time
import datetime
import schedule
import logging
import os

ACCESS_KEY    = os.environ.get("UPBIT_ACCESS_KEY", "")
SECRET_KEY    = os.environ.get("UPBIT_SECRET_KEY", "")

TICKER        = "KRW-BTC"
INVEST_RATIO  = 0.99
K             = 0.5
STOP_LOSS_PCT = -0.01
RSI_PERIOD    = 14
EMA_SHORT     = 5
EMA_LONG      = 20
RSI_OVERBUY   = 70

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading_log.txt", encoding="utf-8")
    ]
)
log = logging.getLogger()

def get_upbit():
    return pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

def get_rsi(ticker, period=14):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=period + 5)
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        log.error(f"RSI 계산 실패: {e}")
        return 50

def get_ema(ticker, short=5, long=20):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=long + 5)
        ema_s = df["close"].ewm(span=short).mean().iloc[-1]
        ema_l = df["close"].ewm(span=long).mean().iloc[-1]
        return ema_s, ema_l
    except Exception as e:
        log.error(f"EMA 계산 실패: {e}")
        return 0, 1

def get_target_price(ticker, k=0.5):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=3)
        yesterday_range = df["high"].iloc[-2] - df["low"].iloc[-2]
        today_open = df["open"].iloc[-1]
        return round(today_open + yesterday_range * k)
    except Exception as e:
        log.error(f"목표가 계산 실패: {e}")
        return None

def get_current_price(ticker):
    try:
        return pyupbit.get_current_price(ticker)
    except Exception as e:
        log.error(f"현재가 조회 실패: {e}")
        return None

def get_balance(upbit, currency="KRW"):
    try:
        balances = upbit.get_balances()
        for b in balances:
            if b["currency"] == currency:
                return float(b["balance"])
        return 0.0
    except Exception as e:
        log.error(f"잔고 조회 실패: {e}")
        return 0.0

def get_avg_buy_price(upbit):
    try:
        balances = upbit.get_balances()
        for b in balances:
            if b["currency"] == "BTC":
                return float(b["avg_buy_price"])
        return 0.0
    except Exception as e:
        log.error(f"평균매수가 조회 실패: {e}")
        return 0.0

def buy_bitcoin(upbit):
    try:
        krw = get_balance(upbit, "KRW")
        if krw < 5000:
            log.info("KRW 잔고 부족 (5,000원 미만) - 매수 건너뜀")
            return False
        amount = krw * INVEST_RATIO
        result = upbit.buy_market_order(TICKER, amount)
        log.info(f"매수 완료! 금액: {int(amount):,}원 | {result}")
        return True
    except Exception as e:
        log.error(f"매수 실패: {e}")
        return False

def sell_bitcoin(upbit, reason="청산"):
    try:
        btc = get_balance(upbit, "BTC")
        if btc < 0.00001:
            log.info("BTC 잔고 없음 - 매도 건너뜀")
            return False
        result = upbit.sell_market_order(TICKER, btc)
        log.info(f"매도 완료! [{reason}] {btc:.8f} BTC | {result}")
        return True
    except Exception as e:
        log.error(f"매도 실패: {e}")
        return False

def strategy_loop():
    try:
        upbit = get_upbit()
        now = datetime.datetime.now()
        log.info(f"{'='*45}")
        log.info(f"전략 점검: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        if now.hour == 23 and now.minute >= 50:
            btc = get_balance(upbit, "BTC")
            if btc > 0.00001:
                log.info("23:50 강제 청산")
                sell_bitcoin(upbit, reason="일일 마감 청산")
            return

        btc_balance = get_balance(upbit, "BTC")
        current_price = get_current_price(TICKER)

        if current_price is None:
            log.warning("현재가 조회 실패, 건너뜀")
            return

        log.info(f"현재가: {int(current_price):,}원 | BTC: {btc_balance:.6f}")

        if btc_balance > 0.00001:
            avg_price = get_avg_buy_price(upbit)
            if avg_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price
                log.info(f"평균매수가: {int(avg_price):,}원 | 수익률: {pnl_pct*100:.2f}%")
                if pnl_pct <= STOP_LOSS_PCT:
                    log.warning(f"손절 발동! {pnl_pct*100:.2f}%")
                    sell_bitcoin(upbit, reason="손절")
            return

        target_price = get_target_price(TICKER, K)
        rsi = get_rsi(TICKER, RSI_PERIOD)
        ema_short, ema_long = get_ema(TICKER, EMA_SHORT, EMA_LONG)

        if target_price is None:
            return

        log.info(f"목표가: {int(target_price):,}원 | RSI: {rsi} | EMA단기: {int(ema_short):,} | EMA장기: {int(ema_long):,}")

        cond_breakout = current_price >= target_price
        cond_trend    = ema_short > ema_long
        cond_rsi      = rsi < RSI_OVERBUY

        log.info(f"  변동성돌파: {'OK' if cond_breakout else 'NO'} | 상승추세: {'OK' if cond_trend else 'NO'} | RSI정상: {'OK' if cond_rsi else 'NO'}")

        if cond_breakout and cond_trend and cond_rsi:
            log.info("매수 조건 충족! 매수 진행...")
            buy_bitcoin(upbit)
        else:
            log.info("조건 미충족, 대기 중...")

    except Exception as e:
        log.error(f"전략 루프 오류: {e}")

def main():
    log.info("="*45)
    log.info("비트코인 자동매매 봇 시작!")
    log.info(f"종목: {TICKER} | 손절: {STOP_LOSS_PCT*100}% | K값: {K}")
    log.info("="*45)

    if not ACCESS_KEY or not SECRET_KEY:
        log.error("API 키가 없습니다! Railway 환경변수를 확인하세요.")
        log.error("UPBIT_ACCESS_KEY 와 UPBIT_SECRET_KEY 를 설정해주세요.")
        return

    try:
        upbit = get_upbit()
        krw = get_balance(upbit, "KRW")
        log.info(f"업비트 연결 성공! KRW 잔고: {int(krw):,}원")
    except Exception as e:
        log.error(f"업비트 연결 실패: {e}")
        return

    strategy_loop()
    schedule.every(5).minutes.do(strategy_loop)

    log.info("5분마다 전략 점검 시작...")
    while True:
        schedule.run_pending()
        time.sleep(10)

if __name__ == "__main__":
    main()
