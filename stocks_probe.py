"""
코스피200(1028) + 코스닥150(2203) 구성종목을 뽑아 collector.py의 STOCKS 형식으로 출력.
GitHub Actions에서 1회 실행 → 로그의 출력 블록을 collector.py STOCKS에 붙여넣기.

KRX 회원제 대응: 로그인 env(KRX_ID/KRX_PW) 필요할 수 있음 (워크플로우에서 주입).
"""
import sys
from pykrx import stock

PRESET_FIRST = [  # 맨 앞 고정(대표종목=폴링 기준). 가나다 정렬에서 제외하고 최상단.
    {"ticker": "087010", "name": "펩트론", "market": "KOSDAQ"},
]

def fetch_index(code, market_label):
    try:
        tickers = stock.get_index_portfolio_deposit_file(code)
    except Exception as e:
        print(f"[ERR] 지수 {code} 구성종목 조회 실패: {e}")
        return []
    out = []
    for t in tickers:
        try:
            name = stock.get_market_ticker_name(t)
        except Exception:
            name = t
        out.append({"ticker": t, "name": name, "market": market_label})
    return out

def find_index_code(market, name_contains):
    """지수명으로 지수코드 탐색 (코드가 버전마다 다를 수 있어 안전하게)."""
    try:
        for code in stock.get_index_ticker_list(market=market):
            nm = stock.get_index_ticker_name(code)
            if name_contains in nm.replace(" ", ""):
                print(f"[INFO] '{nm}' → 코드 {code}")
                return code
    except Exception as e:
        print(f"[WARN] {market} 지수목록 조회 실패: {e}")
    return None

def main():
    # 지수코드 자동 탐색 (실패 시 알려진 기본값)
    code_k200 = find_index_code("KOSPI", "코스피200") or "1028"
    code_kq150 = find_index_code("KOSDAQ", "코스닥150") or "2203"
    print(f"[INFO] 사용 코드 — 코스피200={code_k200}, 코스닥150={code_kq150}")

    kospi200 = fetch_index(code_k200, "KOSPI")
    kosdaq150 = fetch_index(code_kq150, "KOSDAQ")
    print(f"[INFO] 코스피200: {len(kospi200)}종목 / 코스닥150: {len(kosdaq150)}종목")

    # 합치고 중복(펩트론 등 PRESET) 제거 후 가나다 정렬
    preset_tickers = {s["ticker"] for s in PRESET_FIRST}
    merged = {}
    for s in kospi200 + kosdaq150:
        if s["ticker"] in preset_tickers:
            continue
        merged[s["ticker"]] = s          # 티커 기준 중복 제거
    rest = sorted(merged.values(), key=lambda s: s["name"])

    full = PRESET_FIRST + rest
    print(f"[INFO] 최종 {len(full)}종목 (펩트론 최상단 + 가나다)\n")

    # collector.py STOCKS 형식으로 출력
    print("STOCKS = [")
    for s in full:
        nm = s["name"].replace('"', "")
        print(f'    {{"ticker": "{s["ticker"]}", "name": "{nm}", "market": "{s["market"]}"}},')
    print("]")

if __name__ == "__main__":
    main()
