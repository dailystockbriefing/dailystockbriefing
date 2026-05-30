"""
KIS '종목별 투자자매매동향(일별)' 확인용 프로브 (GitHub Actions에서 실행).
시장코드 J / UN / NX 를 차례로 시험해 통합·NXT 동작 여부와 필드 매핑을 한 번에 확인한다.
키는 GitHub Secrets(KIS_APPKEY / KIS_APPSECRET)에서 환경변수로 주입된다.
"""
import os
import sys
import time
import requests

APPKEY    = os.environ["KIS_APPKEY"]
APPSECRET = os.environ["KIS_APPSECRET"]
TR_ID     = os.environ.get("KIS_TR_ID", "FHPTJ04040000")
DATE      = os.environ.get("KIS_DATE", "20260529")
TICKER    = "087010"
BASE      = "https://openapi.koreainvestment.com:9443"

# 1) 접근토큰 발급
tok = requests.post(f"{BASE}/oauth2/tokenP",
                    json={"grant_type": "client_credentials",
                          "appkey": APPKEY, "appsecret": APPSECRET},
                    timeout=15).json()
token = tok.get("access_token")
if not token:
    print("토큰 발급 실패:", tok); sys.exit(1)
print("토큰 발급 OK\n")

headers = {
    "authorization": f"Bearer {token}",
    "appkey": APPKEY, "appsecret": APPSECRET,
    "tr_id": TR_ID, "custtype": "P",
}
URL = f"{BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"

for market in ("J", "UN", "NX"):
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": TICKER,
        "FID_INPUT_DATE_1": DATE,
        "FID_ORG_ADJ_PRC": "1",
        "FID_ETC_CLS_CODE": "0",
    }
    try:
        r = requests.get(URL, headers=headers, params=params, timeout=15).json()
    except Exception as e:
        print(f"== 시장코드 {market}: 호출 예외 {e}\n"); continue

    print(f"== 시장코드 {market}  (TR_ID={TR_ID})")
    print("  rt_cd:", r.get("rt_cd"), "| msg:", r.get("msg1"))
    o2 = r.get("output2", []) or []
    print("  output2 일수:", len(o2))
    if o2:
        row = o2[0]
        keys = ["stck_bsop_date","stck_clpr","acml_vol","acml_tr_pbmn",
                "prsn_ntby_qty","frgn_ntby_qty","orgn_ntby_qty",
                "scrt_ntby_qty","ivtr_ntby_qty","pe_fund_ntby_vol",
                "fund_ntby_qty","insu_ntby_qty","bank_ntby_qty",
                "prsn_ntby_tr_pbmn","frgn_ntby_tr_pbmn","orgn_ntby_tr_pbmn"]
        for k in keys:
            print(f"    {k} = {row.get(k)}")
    print()
    time.sleep(1)
