"""
개별종목 데일리 브리핑 수집기 — 다종목 · 하이브리드
- 투자자 매매동향 + 가격/거래량 + 특이점 : KIS 통합(UN, KRX+NXT) — kis.py
- 공매도 잔고 + 시가총액 : KRX(pykrx)  ← 합의대로 KRX 기준 유지

환경변수: KIS_APPKEY, KIS_APPSECRET (투자자/시세), KRX_ID, KRX_PW (공매도/시총)
"""
import json
import time
from datetime import datetime, timedelta, timezone

from pykrx import stock
import kis

KST = timezone(timedelta(hours=9))

# ── 대상 종목 목록 ─────────────────────────────────────────
STOCKS = [
    {"ticker": "087010", "name": "펩트론",       "market": "KOSDAQ"},
    {"ticker": "035420", "name": "네이버",       "market": "KOSPI"},
    {"ticker": "454910", "name": "두산로보틱스", "market": "KOSPI"},
    {"ticker": "141080", "name": "리가켐바이오", "market": "KOSDAQ"},
    {"ticker": "011070", "name": "LG이노텍",     "market": "KOSPI"},
    {"ticker": "005930", "name": "삼성전자",     "market": "KOSPI"},
    {"ticker": "047810", "name": "한국항공우주", "market": "KOSPI"},
    {"ticker": "005380", "name": "현대차",       "market": "KOSPI"},
    {"ticker": "000660", "name": "SK하이닉스",   "market": "KOSPI"},
]
MKT_CODE = "UN"        # UN=통합(KRX+NXT)
HIST_DAYS = 10
# ───────────────────────────────────────────────────────────

VOL_SPIKE = 2.0
GAP_PCT   = 5.0
RANGE_PCT = 12.0


def ymd(d):
    return d.strftime("%Y%m%d")


def eok(won):
    return round(won / 1e8)


def get_marketcap(ticker, date_str):
    try:
        df = stock.get_market_cap_by_date(date_str, date_str, ticker)
        if df is not None and not df.empty:
            col = next((c for c in df.columns if "시가총액" in c), df.columns[0])
            return int(df[col].iloc[-1])
    except Exception as e:
        print(f"[시총] 실패: {e}")
    return None


def collect_short(ticker, date_str):
    """KRX 일별 공매도 잔고 수량/비중 (T+2). 최근 10영업일 + 전일대비 수량 변화."""
    start = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=50)
    try:
        df = stock.get_shorting_balance_by_date(ymd(start), date_str, ticker)
    except Exception as e:
        print(f"[공매도] 실패: {e}")
        return None, []
    if df is None or df.empty:
        return None, []
    qty_col   = next((c for c in df.columns if "잔고" in c and "금액" not in c and "비중" not in c), None)
    ratio_col = next((c for c in df.columns if "비중" in c), None)
    if qty_col is None:
        qty_col = df.columns[0]
    tail = df.tail(10)
    trend = [{"date": idx.strftime("%m.%d"), "qty": int(r[qty_col]),
              "ratio": round(float(r[ratio_col]), 2) if ratio_col else 0.0}
             for idx, r in tail.iterrows()]
    last = df.iloc[-1]; prev = df.iloc[-2] if len(df) > 1 else last
    qn = int(last[qty_col]); qp = int(prev[qty_col]); diff = qn - qp
    latest = {
        "date": df.index[-1].strftime("%Y.%m.%d"), "qty": qn,
        "ratio": round(float(last[ratio_col]), 2) if ratio_col else None,
        "qty_change": diff,
        "qty_change_pct": round(diff / qp * 100, 2) if qp else 0.0,
    }
    return latest, trend


def build_signals(rows, short_latest):
    """rows: KIS 통합 일자별(최신순). 통합 거래량 기준 특이점."""
    sig = []
    if not rows or len(rows) < 2:
        return sig
    today = rows[0]
    o, h, l, c = today["open"], today["high"], today["low"], today["close"]
    vol = today["volume"]; pc = c - today["change"]

    hist_vol = [r["volume"] for r in rows[1:21]]
    if hist_vol:
        avg = sum(hist_vol) / len(hist_vol)
        if avg > 0 and vol / avg >= VOL_SPIKE:
            sig.append({"kind": "warn", "text": f"거래량 급증 — 최근 평균 대비 {vol/avg:.1f}배 (통합)"})

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

    closes = [r["close"] for r in rows[:20]]
    if c >= max(closes):
        sig.append({"kind": "pos", "text": "최근 20일 신고가"})
    elif c <= min(closes):
        sig.append({"kind": "neg", "text": "최근 20일 신저가"})

    f = today["value"].get("외국인", 0); i = today["value"].get("기관", 0)
    if f > 0 and i > 0:
        sig.append({"kind": "pos", "text": f"외국인·기관 동반 순매수 (+{f}억/+{i}억)"})
    elif f < 0 and i < 0:
        sig.append({"kind": "neg", "text": f"외국인·기관 동반 순매도 ({f}억/{i}억)"})

    streak = 0
    for r in rows:
        if r["value"].get("외국인", 0) > 0: streak += 1
        else: break
    if streak >= 3:
        sig.append({"kind": "info", "text": f"외국인 {streak}일 연속 순매수"})

    if short_latest:
        rt = short_latest.get("ratio")
        if rt is not None and rt >= 3.0:
            sig.append({"kind": "warn", "text": f"공매도 잔고비중 {rt:.2f}% (높음, {short_latest['date']} 기준)"})
        if short_latest.get("qty_change_pct", 0) >= 15:
            sig.append({"kind": "warn", "text": f"공매도 잔고 급증 — 전일 대비 +{short_latest['qty_change']:,}주"})
    return sig


def collect_all(stk, short_pending=None):
    ticker, name, market = stk["ticker"], stk["name"], stk["market"]
    now = datetime.now(KST)

    # 1) KIS: 세 시장(통합/KRX/NXT) 투자자 + 시세
    mkts = kis.fetch_all_markets(ticker)
    base = mkts.get("UN") or mkts.get("J")
    if not base:
        raise SystemExit("KIS 데이터를 가져오지 못했습니다.")
    rows = base[:HIST_DAYS]
    today = rows[0]
    date_str = today["date_full"]

    # 날짜축은 통합 기준. 각 날짜에 대해 시장별 value/volume 매핑.
    def market_row(mk, date_full):
        for r in mkts.get(mk, []):
            if r["date_full"] == date_full:
                return {"value": r["value"], "volume": r["volume_inv"]}
        return None

    investors_hist = []
    for r in rows:
        df = r["date_full"]
        investors_hist.append({
            "date": r["date"],
            "markets": {
                "UN": market_row("UN", df),
                "J":  market_row("J", df),
                "NX": market_row("NX", df),
            }
        })

    # 2) KRX: 공매도 + 시총
    short_latest, short_trend = collect_short(ticker, date_str)
    marketcap = get_marketcap(ticker, date_str)

    # 2-1) KIS: 증권사 투자의견/목표주가
    try:
        opinions = kis.fetch_opinions(ticker)
    except Exception as e:
        print(f"[투자의견] 실패: {e}")
        opinions = []
    # 목표가 컨센서스: 증권사별 '최신' 목표가 1개씩 + 최근 N일 이내만
    CONSENSUS_DAYS = 30
    cutoff = (now - timedelta(days=CONSENSUS_DAYS)).strftime("%Y%m%d")

    def norm_broker(b):
        """증권사명 정규화 — 표기 차이로 중복 집계되는 것 방지."""
        b = (b or "").strip()
        for suf in ("투자증권", "증권", "투자", "금융투자"):
            if b.endswith(suf):
                b = b[:-len(suf)]
        return b.strip()

    latest_by_broker = {}   # 정규화 증권사 → 최신 목표가 (opinions는 최신순)
    for o in opinions:
        gp = o.get("goal_price")
        df = o.get("date_full", "")
        if not gp or gp <= 0:
            continue
        if df and df < cutoff:      # N일보다 오래된 리포트 제외
            continue
        key = norm_broker(o.get("broker"))
        if key and key not in latest_by_broker:   # 증권사별 첫 등장 = 최신
            latest_by_broker[key] = gp
    goals = list(latest_by_broker.values())
    consensus = None
    if goals:
        consensus = {
            "avg": round(sum(goals) / len(goals)),
            "high": max(goals), "low": min(goals), "count": len(goals),
            "days": CONSENSUS_DAYS,
        }
    # 표시용: 컨센서스와 동일 기준(증권사별 최신·기간내) 리포트만, 최신순
    seen = set()
    display_ops = []
    for o in opinions:
        df = o.get("date_full", "")
        if df and df < cutoff:
            continue
        key = norm_broker(o.get("broker"))
        if not key or key in seen:
            continue
        seen.add(key)
        display_ops.append(o)

    print(f"  [컨센서스] {ticker}: {consensus['count'] if consensus else 0}곳 "
          f"평균 {consensus['avg'] if consensus else '—'}")

    # 3) 특이점 (통합 거래량 기준)
    signals = build_signals(base, short_latest)

    # 목표주가 상승여력 신호
    if consensus and today.get("close"):
        upside = (consensus["avg"] - today["close"]) / today["close"] * 100
        if upside >= 20:
            signals.append({"kind": "pos",
                "text": f"목표주가 컨센서스 평균 {consensus['avg']:,}원 — 현재가 대비 +{upside:.0f}% (증권사 {consensus['count']}곳)"})
        elif upside <= -10:
            signals.append({"kind": "neg",
                "text": f"현재가가 목표주가 평균({consensus['avg']:,}원)을 {abs(upside):.0f}% 상회 (증권사 {consensus['count']}곳)"})

    if short_pending is None:
        before = (now.hour < 18) or (now.hour == 18 and now.minute < 10)
        short_pending = (now.weekday() < 5) and before

    data = {
        "updated_label": now.strftime("%Y.%m.%d %H:%M"),
        "trade_date": f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}" if len(date_str) == 8 else date_str,
        "market_basis": "통합(KRX+NXT)",
        "stock": {
            "name": name, "ticker": ticker, "market": market,
            "close": today["close"], "change": today["change"], "change_pct": today["change_pct"],
            "open": today["open"], "high": today["high"], "low": today["low"],
            "volume": today["volume"], "marketcap": marketcap,
        },
        "investors_hist": investors_hist,
        "short_latest": short_latest,
        "short_trend": short_trend,
        "short_pending": bool(short_pending),
        "opinions": display_ops,
        "target_consensus": consensus,
        "signals": signals,
    }
    return data


def write_json(data, path):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def collect_one(stk, short_pending=None):
    """종목 1개 수집 → data/<ticker>.json 기록."""
    data = collect_all(stk, short_pending=short_pending)
    write_json(data, f"data/{stk['ticker']}.json")
    print(f"  · {stk['name']}({stk['ticker']}) 기록 완료")
    return data


def write_index():
    """종목 선택용 인덱스 → data/index.json"""
    idx = [{"ticker": s["ticker"], "name": s["name"], "market": s["market"]} for s in STOCKS]
    write_json({"stocks": idx, "updated_label": datetime.now(KST).strftime("%Y.%m.%d %H:%M")},
               "data/index.json")


def main():
    write_index()
    for stk in STOCKS:
        try:
            collect_one(stk)
        except Exception as e:
            print(f"  ! {stk['name']}({stk['ticker']}) 실패: {e}")
    print(f"data/*.json 생성 완료 — {len(STOCKS)}종목")


if __name__ == "__main__":
    main()
