"""
신규 데이터 게시를 폴링하다 감지되면 전 종목을 수집해 data/<ticker>.json 기록.
프로브는 대표종목(STOCKS[0])으로 게시 시점만 판단(시점은 종목 공통).

3개 창 (KST):
  - invest (약 15:40): KRX 마감 직후. 통합 데이터 잠정(NXT 미반영).
  - short  (약 18:00): KRX 공매도 잔고 갱신.
  - final  (약 20:10): NXT 마감 후 통합 확정.
  - once             : 폴링 없이 즉시 1회(테스트용).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from pykrx import stock
import collector as C
import kis

KST = timezone(timedelta(hours=9))


def _today():
    return datetime.now(KST).strftime("%Y%m%d")


def _rep():
    """대표종목 (목록 첫 종목)"""
    return C.STOCKS[0]


def probe_invest():
    try:
        rows = kis.fetch_daily(_rep()["ticker"], market="UN")
        if not rows or rows[0].get("date_full") != _today():
            return False
        return any(v != 0 for v in rows[0]["value"].values())
    except Exception:
        return False


def stored_short_date():
    try:
        with open(f"data/{_rep()['ticker']}.json", encoding="utf-8") as f:
            return (json.load(f).get("short_latest") or {}).get("date")
    except Exception:
        return None


def probe_short():
    now = datetime.now(KST)
    start = (now - timedelta(days=20)).strftime("%Y%m%d")
    try:
        df = stock.get_shorting_balance_by_date(start, now.strftime("%Y%m%d"), _rep()["ticker"])
        if df is None or df.empty:
            return False
        return df.index[-1].strftime("%Y.%m.%d") != stored_short_date()
    except Exception:
        return False


def stored_un_volume():
    try:
        with open(f"data/{_rep()['ticker']}.json", encoding="utf-8") as f:
            h = json.load(f).get("investors_hist") or []
        if not h:
            return None
        un = (h[0].get("markets") or {}).get("UN") or {}
        return sum(abs(v) for v in (un.get("volume") or {}).values())
    except Exception:
        return None


def probe_final():
    try:
        tk = _rep()["ticker"]
        un = kis.fetch_daily(tk, market="UN")
        nx = kis.fetch_daily(tk, market="NX")
        if not un or un[0].get("date_full") != _today():
            return False
        if nx and nx[0].get("date_full") == _today():
            if any(v != 0 for v in nx[0]["value"].values()):
                return True
        cur = sum(abs(v) for v in un[0]["volume_inv"].values())
        prev = stored_un_volume()
        return (prev is None) or (cur != prev)
    except Exception:
        return False


PROBES = {"invest": probe_invest, "short": probe_short, "final": probe_final}


def collect_all_stocks(short_pending):
    C.write_index()
    for stk in C.STOCKS:
        try:
            C.collect_one(stk, short_pending=short_pending)
        except Exception as e:
            print(f"  ! {stk['name']}({stk['ticker']}) 실패: {e}")


def run(mode, interval, max_minutes):
    if mode == "once":
        print("[once] 즉시 전 종목 수집")
        collect_all_stocks(short_pending=None)
        print("[once] 완료")
        return 0

    probe = PROBES[mode]
    deadline = datetime.now(KST) + timedelta(minutes=max_minutes)
    n = 0

    def write(detected):
        pend = True if mode == "invest" else (not detected)
        collect_all_stocks(short_pending=pend)

    while datetime.now(KST) < deadline:
        n += 1
        if probe():
            print(f"[{mode}] {n}회차 — 신규 감지, 전 종목 수집")
            write(True)
            print(f"[{mode}] 완료")
            return 0
        print(f"[{mode}] {n}회차 — 미게시, {interval}s 대기")
        time.sleep(interval)

    print(f"[{mode}] 창 만료 — 최종 전 종목 수집")
    write(False)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["invest", "short", "final", "once"], required=True)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--max-minutes", type=int, default=10)
    args = ap.parse_args()
    sys.exit(run(args.mode, args.interval, args.max_minutes))
