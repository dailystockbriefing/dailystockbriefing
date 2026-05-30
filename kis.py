"""
KIS(한국투자증권) Open API 모듈 — 통합(KRX+NXT) 투자자매매·시세 수집.
collector.py 에서 import 해서 사용한다.

환경변수: KIS_APPKEY, KIS_APPSECRET  (GitHub Secrets로 주입)
엔드포인트: 종목별 투자자매매동향(일별) FHPTJ04160001
시장코드: J=KRX, NX=NXT, UN=통합  → 통합(UN) 사용
output2: 최근 약 30영업일, 일자별 투자자 순매수(수량/금액) + OHLCV
"""
import os
import time
import requests

BASE = "https://openapi.koreainvestment.com:9443"
TR_ID = "FHPTJ04160001"
URL = f"{BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"

# KIS output2 필드 → 우리 표준 투자자 키 매핑 (수량 qty / 금액 amt)
_QTY = {
    "개인": "prsn_ntby_qty", "외국인": "frgn_ntby_qty", "기관": "orgn_ntby_qty",
    "금융투자": "scrt_ntby_qty", "투신": "ivtr_ntby_qty", "사모": "pe_fund_ntby_vol",
    "연기금": "fund_ntby_qty", "보험": "insu_ntby_qty", "은행": "bank_ntby_qty",
}
_AMT = {  # 금액은 백만원 단위(_tr_pbmn) → 억으로 환산
    "개인": "prsn_ntby_tr_pbmn", "외국인": "frgn_ntby_tr_pbmn", "기관": "orgn_ntby_tr_pbmn",
    "금융투자": "scrt_ntby_tr_pbmn", "투신": "ivtr_ntby_tr_pbmn", "사모": "pe_fund_ntby_tr_pbmn",
    "연기금": "fund_ntby_tr_pbmn", "보험": "insu_ntby_tr_pbmn", "은행": "bank_ntby_tr_pbmn",
}
KEYS = list(_QTY.keys())


def _token():
    appkey = os.environ["KIS_APPKEY"]
    appsecret = os.environ["KIS_APPSECRET"]
    r = requests.post(f"{BASE}/oauth2/tokenP",
                      json={"grant_type": "client_credentials",
                            "appkey": appkey, "appsecret": appsecret},
                      timeout=15).json()
    tok = r.get("access_token")
    if not tok:
        raise RuntimeError(f"KIS 토큰 발급 실패: {r}")
    expires_in = r.get("expires_in", 86400)
    return tok, appkey, appsecret, expires_in


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


_TOKEN_CACHE = {}
_TOKEN_FILE = "kis_token.json"   # 발급 토큰을 디스크에 저장해 24시간 재사용

def _load_token_file():
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            d = json.load(f)
        # 만료 1시간 전까지 유효한 것으로 간주
        if d.get("expires_at", 0) - 3600 > time.time() and d.get("token"):
            return (d["token"], d["appkey"], d["appsecret"])
    except Exception:
        pass
    return None

def _save_token_file(token, appkey, appsecret, expires_in):
    try:
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"token": token, "appkey": appkey, "appsecret": appsecret,
                       "expires_at": time.time() + int(expires_in)}, f)
    except Exception as e:
        print(f"[KIS] 토큰 저장 실패(무시): {e}")

def _get_token_cached():
    # 1) 프로세스 메모리
    if "tok" in _TOKEN_CACHE:
        return _TOKEN_CACHE["tok"]
    # 2) 디스크(24시간 유효) — 폴링/재실행 간 재사용으로 발급 횟수 최소화
    disk = _load_token_file()
    if disk:
        _TOKEN_CACHE["tok"] = disk
        return disk
    # 3) 신규 발급 (하루 1회 수준으로만 발생)
    tok, appkey, appsecret, expires_in = _token()
    _save_token_file(tok, appkey, appsecret, expires_in)
    _TOKEN_CACHE["tok"] = (tok, appkey, appsecret)
    return _TOKEN_CACHE["tok"]


def fetch_daily(ticker, market="UN", base_date=""):
    """통합(UN) 일자별 투자자매매+시세. 최신→과거 정렬된 리스트 반환.
    각 원소: {date, close, change, change_pct, open, high, low, volume, value_won,
              value:{투자자:억}, volume_inv:{투자자:주}}
    """
    token, appkey, appsecret = _get_token_cached()
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": appkey, "appsecret": appsecret,
        "tr_id": TR_ID, "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": base_date,   # 공란이면 최근일 기준
        "FID_ORG_ADJ_PRC": "1",
        "FID_ETC_CLS_CODE": "0",
    }
    r = requests.get(URL, headers=headers, params=params, timeout=15).json()
    if r.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 조회 실패({market}): {r.get('msg1')}")

    rows = []
    prev_close = None
    # output2는 최신순으로 내려오므로, 전일대비 계산 위해 과거→현재로 뒤집어 처리
    for o in reversed(r.get("output2", []) or []):
        close = _to_int(o.get("stck_clpr"))
        date8 = o.get("stck_bsop_date", "")
        row = {
            "date_full": date8,
            "date": f"{date8[4:6]}.{date8[6:8]}" if len(date8) == 8 else date8,
            "close": close,
            "open": _to_int(o.get("stck_oprc")),
            "high": _to_int(o.get("stck_hgpr")),
            "low": _to_int(o.get("stck_lwpr")),
            "volume": _to_int(o.get("acml_vol")),
            "value_won": _to_int(o.get("acml_tr_pbmn")),
            "value": {k: round(_to_int(o.get(_AMT[k])) / 100.0) for k in KEYS},      # 백만→억
            "volume_inv": {k: _to_int(o.get(_QTY[k])) for k in KEYS},
        }
        if prev_close:
            row["change"] = close - prev_close
            row["change_pct"] = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        else:
            row["change"] = 0
            row["change_pct"] = 0.0
        prev_close = close
        rows.append(row)
    rows.reverse()  # 다시 최신순
    return rows


def fetch_all_markets(ticker, token_bundle=None):
    """J(KRX)/UN(통합)/NX(NXT) 세 시장을 모두 수집. 토큰 1개 재사용.
    반환: {"UN":[...], "J":[...], "NX":[...]} (각 fetch_daily 결과)
    """
    out = {}
    for mk in ("UN", "J", "NX"):
        try:
            out[mk] = fetch_daily(ticker, market=mk)
            time.sleep(0.3)
        except Exception as e:
            print(f"[KIS] {mk} 수집 실패: {e}")
            out[mk] = []
    return out
