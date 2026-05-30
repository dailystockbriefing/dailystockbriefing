"""
신규 데이터 게시를 폴링하다가 감지되면 1회 수집해 data.json 으로 기록.

3개 창 (KST):
  - invest (~15:40): KRX 마감 직후. KIS 통합 데이터에 오늘자가 올라오기 시작.
                     이 시점 통합값은 NXT 애프터마켓(~20:00) 미반영 '잠정'.
  - short  (~18:00): KRX 공매도 잔고 갱신 (T+2).
  - final  (~20:10): NXT 장 마감 후. 통합 데이터가 그날 '확정'.

사용:
  python poll.py --mode invest --interval 30 --max-minutes 9
  python poll.py --mode short  --interval 60 --max-minutes 16
  python poll.py --mode final  --interval 60 --max-minutes 20
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


def probe_invest():
    """오늘자 KIS 통합 투자자 데이터가 게시됐는지 (KRX 마감 후 ~15:40)."""
    try:
        rows = kis.fetch_daily(C.TICKER, market="UN")
        if not rows:
            return False
        top = rows[0]
        if top.get("date_full") != _today():
            return False
        return any(v != 0 for v in top["value"].values())
    except Exception:
        return False


def stored_short_date():
    try:
        with open("data.json", encoding="utf-8") as f:
            return (json.load(f).get("short_latest") or {}).get("date")
    except Exception:
        return None


def probe_short():
    """공매도 잔고 최신 기준일이 기존 data.json 보다 진전됐는지 (~18:00)."""
    now = datetime.now(KST)
    start = (now - timedelta(days=20)).strftime("%Y%m%d")
    try:
        df = stock.get_shorting_balance_by_date(start, now.strftime("%Y%m%d"), C.TICKER)
        if df is None or df.empty:
            return False
        return df.index[-1].strftime("%Y.%m.%d") != stored_short_date()
    except Exception:
        return False


def stored_un_volume():
    """기존 data.json 최신행의 통합 거래량 합(투자자 절대값 합)으로 변화 감지용 지문."""
    try:
        with open("data.json", encoding="utf-8") as f:
            h = json.load(f).get("investors_hist") or []
        if not h:
            return None
        un = (h[-1].get("markets") or {}).get("UN") or {}
        vol = un.get("volume") or {}
        return sum(abs(v) for v in vol.values())
    except Exception:
        return None


def probe_final():
    """NXT 마감 후 통합 확정 여부 (~20:10).
    NXT가 0이 아니거나, 통합 거래량 지문이 기존과 달라졌으면 '확정 갱신'으로 간주."""
    try:
        un = kis.fetch_daily(C.TICKER, market="UN")
        nx = kis.fetch_daily(C.TICKER, market="NX")
        if not un or un[0].get("date_full") != _today():
            return False
        # NXT 오늘자 순매수가 잡히면 애프터마켓 반영된 것
        if nx and nx[0].get("date_full") == _today():
            if any(v != 0 for v in nx[0]["value"].values()):
                return True
        # 또는 통합 거래량 지문이 기존 기록과 달라졌으면 갱신된 것
        cur = sum(abs(v) for v in un[0]["volume_inv"].values())
        prev = stored_un_volume()
        return (prev is None) or (cur != prev)
    except Exception:
        return False


PROBES = {"invest": probe_invest, "short": probe_short, "final": probe_final}


def run(mode, interval, max_minutes):
    probe = PROBES[mode]
    deadline = datetime.now(KST) + timedelta(minutes=max_minutes)
    n = 0
    # 공매도 pending: short/final 창에서 감지되면 해제, invest 창에선 유지
    def write(detected):
        pend = True
        if mode in ("short", "final"):
            pend = not detected
        C.write_json(C.collect_all(short_pending=pend))

    while datetime.now(KST) < deadline:
        n += 1
        if probe():
            print(f"[{mode}] {n}회차 프로브 — 신규 감지, 수집 시작")
            write(True)
            print(f"[{mode}] 수집·기록 완료")
            return 0
        print(f"[{mode}] {n}회차 프로브 — 아직 미게시, {interval}s 대기")
        time.sleep(interval)

    print(f"[{mode}] 창 만료 — 최종 1회 수집")
    write(False)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["invest", "short", "final"], required=True)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--max-minutes", type=int, default=10)
    args = ap.parse_args()
    sys.exit(run(args.mode, args.interval, args.max_minutes))
