"""
개별종목 브리핑 수집기 — 펩트론(087010) 기준
- 당일 투자자 순매수 (개인/외국인/기관, 기관은 금투·투신·사모·연기금 등으로 세분)
- 공매도 잔고 수량/비중 최근 10영업일 + 전일 대비 수량 변화 (T+2 지연)
- EOD 기반 매매 특이점 신호

실행:  python collector.py
의존성: pip install pykrx pandas
참고:  pykrx 함수/컬럼명/인덱스명은 버전마다 다를 수 있어 키워드 매칭 + try/except 로 방어함.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from pykrx import stock

KST = timezone(timedelta(hours=9))

# ── 대상 종목 설정 ─────────────────────────────────────────
TICKER = "087010"
NAME   = "펩트론"
MARKET = "KOSDAQ"
# ───────────────────────────────────────────────────────────

VOL_SPIKE = 2.0
GAP_PCT   = 5.0
RANGE_PCT = 12.0


def ymd(d):
    return d.strftime("%Y%m%d")


def eok(won):
    return round(won / 1e8)


def get_history(date_str):
    start = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=90)
    df = stock.get_market_ohlcv(ymd(start), date_str, TICKER)
    return df.dropna()


def get_marketcap(date_str):
    """해당일 시가총액(원)."""
    try:
        df = stock.get_market_cap_by_date(date_str, date_str, TICKER)
        if df is not None and not df.empty:
            col = next((c for c in df.columns if "시가총액" in c), df.columns[0])
            return int(df[col].iloc[-1])
    except Exception as e:
        print(f"[시총] 실패: {e}")
    return None


def _inv_g(date_str, kind):
    """kind='value'(거래대금) 또는 'volume'(거래량) 순매수 조회 → 라벨 게터."""
    fn = (stock.get_market_trading_value_by_investor if kind == "value"
          else stock.get_market_trading_volume_by_investor)
    df = fn(date_str, date_str, TICKER)
    col = "순매수" if "순매수" in df.columns else df.columns[-1]
    def g(label):
        for k in df.index:
            if label in str(k):
                v = float(df.loc[k, col])
                return eok(v) if kind == "value" else int(v)
        return 0
    return g


INV_KEYS = ["개인", "외국인", "기관", "금융투자", "투신", "사모", "연기금", "보험", "은행", "기타금융"]


def _pack(g):
    return {k: (g("기관합계") if k == "기관" else g(k)) for k in INV_KEYS}


def investor_row(date_str):
    """해당일 순매수 — 금액(억) value, 수량(주) volume 두 벌."""
    gv = _inv_g(date_str, "value")
    time.sleep(0.2)
    gq = _inv_g(date_str, "volume")
    return {"value": _pack(gv), "volume": _pack(gq)}


def collect_short(date_str):
    """일별 공매도 잔고 수량/비중 (T+2). 최근 10영업일 + 전일대비 수량 변화."""
    start = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=50)
    try:
        df = stock.get_shorting_balance_by_date(ymd(start), date_str, TICKER)
    except Exception as e:
        print(f"[공매도] 실패: {e}")
        return None, []
    if df is None or df.empty:
        return None, []

    qty_col   = next((c for c in df.columns if "잔고" in c and "금액" not in c and "비중" not in c), None)
    ratio_col = next((c for c in df.columns if "비중" in c), None)
    if qty_col is None:  # 폴백
        qty_col = df.columns[0]

    tail = df.tail(10)
    trend = [{"date": idx.strftime("%m.%d"),
              "qty": int(r[qty_col]),
              "ratio": round(float(r[ratio_col]), 2) if ratio_col else 0.0}
             for idx, r in tail.iterrows()]

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    qty_now  = int(last[qty_col])
    qty_prev = int(prev[qty_col])
    diff = qty_now - qty_prev
    latest = {
        "date": df.index[-1].strftime("%Y.%m.%d"),
        "qty": qty_now,
        "ratio": round(float(last[ratio_col]), 2) if ratio_col else None,
        "qty_change": diff,
        "qty_change_pct": round(diff / qty_prev * 100, 2) if qty_prev else 0.0,
    }
    return latest, trend


def build_signals(hist, inv_today, inv_hist, short_latest):
    sig = []
    if hist is None or len(hist) < 21:
        return sig
    today, prev = hist.iloc[-1], hist.iloc[-2]
    o, h, l, c = (float(today[k]) for k in ("시가", "고가", "저가", "종가"))
    vol = float(today["거래량"]); pc = float(prev["종가"])

    avg20 = float(hist["거래량"].iloc[-21:-1].mean())
    if avg20 > 0 and vol / avg20 >= VOL_SPIKE:
        sig.append({"kind": "warn", "text": f"거래량 급증 — 20일 평균 대비 {vol/avg20:.1f}배"})

    if pc > 0:
        gap = (o - pc) / pc * 100
        if abs(gap) >= GAP_PCT:
            sig.append({"kind": "pos" if gap > 0 else "neg",
                        "text": f"{'갭 상승' if gap>0 else '갭 하락'} 출발 {gap:+.1f}%"})
        rng = (h - l) / pc * 100
        if rng >= RANGE_PCT:
            sig.append({"kind": "warn", "text": f"장중 변동폭 확대 {rng:.1f}%"})

    if h > l:
        pos = (c - l) / (h - l)
        if pos >= 0.8 and c >= o:
            sig.append({"kind": "pos", "text": "고가권 마감 — 매수 우위(짧은 윗꼬리)"})
        elif pos <= 0.25:
            sig.append({"kind": "neg", "text": "저가권 마감 — 윗꼬리 형성(매도 압력)"})

    win = hist["종가"].iloc[-20:]
    if c >= win.max():
        sig.append({"kind": "pos", "text": "20일 신고가 경신"})
    elif c <= win.min():
        sig.append({"kind": "neg", "text": "20일 신저가 경신"})

    val = inv_today.get("value", {})
    f, i = val.get("외국인", 0), val.get("기관", 0)
    if f > 0 and i > 0:
        sig.append({"kind": "pos", "text": f"외국인·기관 동반 순매수 (+{f}억/+{i}억)"})
    elif f < 0 and i < 0:
        sig.append({"kind": "neg", "text": f"외국인·기관 동반 순매도 ({f}억/{i}억)"})

    streak = 0
    for d in reversed(inv_hist):
        if d.get("value", {}).get("외국인", 0) > 0: streak += 1
        else: break
    if streak >= 3:
        sig.append({"kind": "info", "text": f"외국인 {streak}일 연속 순매수"})

    if short_latest:
        r = short_latest.get("ratio")
        if r is not None and r >= 3.0:
            sig.append({"kind": "warn", "text": f"공매도 잔고비중 {r:.2f}% (높음, {short_latest['date']} 기준)"})
        ch = short_latest.get("qty_change", 0)
        if short_latest.get("qty_change_pct", 0) >= 15:
            sig.append({"kind": "warn", "text": f"공매도 잔고 급증 — 전일 대비 +{ch:,}주"})

    return sig


def collect_all(short_pending=None):
    now = datetime.now(KST)
    hist = None
    d = now
    for _ in range(8):
        if d.weekday() < 5:
            try:
                hist = get_history(ymd(d))
                if not hist.empty:
                    break
            except Exception:
                pass
        d -= timedelta(days=1)
    if hist is None or hist.empty:
        raise SystemExit("OHLCV 데이터를 가져오지 못했습니다.")

    trade_date = hist.index[-1]
    date_str = ymd(trade_date)
    today, prev = hist.iloc[-1], hist.iloc[-2]
    close = float(today["종가"]); pc = float(prev["종가"])
    change = round(close - pc); pct = round((change / pc * 100), 2) if pc else 0.0

    inv_hist = []
    for idx in hist.index[-10:]:
        try:
            inv_hist.append({"date": idx.strftime("%m.%d"), **investor_row(ymd(idx))})
            time.sleep(0.25)
        except Exception:
            pass
    inv_today = inv_hist[-1] if inv_hist else {}

    short_latest, short_trend = collect_short(date_str)
    signals = build_signals(hist, inv_today, inv_hist, short_latest)

    # 공매도 '오늘 업데이트 전' 여부: 인자 우선, 없으면 시각으로 추정(평일 18:10 이전)
    if short_pending is None:
        before = (now.hour < 18) or (now.hour == 18 and now.minute < 10)
        short_pending = (now.weekday() < 5) and before

    data = {
        "updated_label": now.strftime("%Y.%m.%d %H:%M"),
        "trade_date": trade_date.strftime("%Y.%m.%d"),
        "stock": {
            "name": NAME, "ticker": TICKER, "market": MARKET,
            "close": close, "change": change, "change_pct": pct,
            "open": float(today["시가"]), "high": float(today["고가"]),
            "low": float(today["저가"]), "volume": int(today["거래량"]),
            "marketcap": get_marketcap(date_str),
        },
        "investors_hist": inv_hist,
        "short_latest": short_latest,
        "short_trend": short_trend,
        "short_pending": bool(short_pending),
        "signals": signals,
    }
    return data


def write_json(data, path="data.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    data = collect_all()
    write_json(data)
    print(f"data.json 생성 완료 — {NAME}({TICKER}) {data['trade_date']}")


if __name__ == "__main__":
    main()
