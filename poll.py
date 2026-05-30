"""
신규 데이터 게시를 폴링하다가 감지되면 1회 수집해 data.json 으로 기록.

사용:
  python poll.py --mode invest --interval 30 --max-minutes 9
  python poll.py --mode short  --interval 60 --max-minutes 16

동작:
  - 매 주기마다 "가벼운 단일 프로브" 1콜로 신규 게시 여부만 확인 (KRX 부하 최소화)
  - 신규 감지 → collector.collect_all() 로 전체 수집 후 기록하고 종료
  - 창(max-minutes) 만료 → 마지막으로 1회 수집해 기록 (커밋 단계가 변경분만 반영)
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from pykrx import stock
import collector as C

KST = timezone(timedelta(hours=9))


def probe_invest():
    """당일 투자자 데이터가 게시됐는지 (장 마감 후 ~15:40)."""
    today = datetime.now(KST).strftime("%Y%m%d")
    try:
        df = stock.get_market_trading_value_by_investor(today, today, C.TICKER)
        if df is None or df.empty:
            return False
        col = "순매수" if "순매수" in df.columns else df.columns[-1]
        return float(df[col].abs().sum()) > 0
    except Exception:
        return False


def stored_short_date():
    try:
        with open("data.json", encoding="utf-8") as f:
            return (json.load(f).get("short_latest") or {}).get("date")
    except Exception:
        return None


def probe_short():
    """공매도 잔고의 최신 기준일이 기존 data.json 보다 진전됐는지 (~18:00)."""
    now = datetime.now(KST)
    start = (now - timedelta(days=20)).strftime("%Y%m%d")
    try:
        df = stock.get_shorting_balance_by_date(start, now.strftime("%Y%m%d"), C.TICKER)
        if df is None or df.empty:
            return False
        latest = df.index[-1].strftime("%Y.%m.%d")
        return latest != stored_short_date()   # 다르면(진전됐으면) 신규
    except Exception:
        return False


def run(mode, interval, max_minutes):
    probe = probe_invest if mode == "invest" else probe_short
    deadline = datetime.now(KST) + timedelta(minutes=max_minutes)
    n = 0
    while datetime.now(KST) < deadline:
        n += 1
        if probe():
            print(f"[{mode}] {n}회차 프로브 — 신규 데이터 감지, 수집 시작")
            # short 창에서 감지됐으면 공매도 업데이트 완료(pending=False)
            C.write_json(C.collect_all(short_pending=(mode != "short")))
            print(f"[{mode}] 수집·기록 완료")
            return 0
        print(f"[{mode}] {n}회차 프로브 — 아직 미게시, {interval}s 대기")
        time.sleep(interval)

    # 창 만료: 최선의 현재 데이터로 1회 기록 (감지 못했으면 공매도는 여전히 업데이트 전)
    print(f"[{mode}] 창 만료 — 최종 1회 수집")
    C.write_json(C.collect_all(short_pending=True))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["invest", "short"], required=True)
    ap.add_argument("--interval", type=int, default=30, help="폴링 간격(초)")
    ap.add_argument("--max-minutes", type=int, default=10, help="최대 폴링 시간(분)")
    args = ap.parse_args()
    sys.exit(run(args.mode, args.interval, args.max_minutes))
