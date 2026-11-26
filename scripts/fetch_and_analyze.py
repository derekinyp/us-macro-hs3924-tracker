#!/usr/bin/env python3
"""
美國近十年總經、消費轉向與 HS 3924 塑膠食品容器替代自動化追蹤腳本
- 100% 抓取官方原始序列（FRED API + US Census International Trade API）
- 零內插、零補值：官方缺漏保持 null，避免編造數據
- 變更偵測：僅在有新月份/新季度數據更新時呼叫 Gemini API 產出綜合判讀（支援 --force-analyze）
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 1. 指標定義（所有代碼皆經查核為美國官方長期穩定序列）
# ---------------------------------------------------------------------------
FRED_SERIES_CONFIG = {
    # 1. 所得與緩衝
    "DSPIC96": {
        "name": "實質可支配個人所得",
        "group": "income",
        "freq": "monthly",
        "unit": "十億美元 (2017連鎖美元, SAAR)",
        "calc_yoy": True,
        "scope_badge": "稅後｜含政府補助｜已扣通膨｜不含資本利得",
        "source": "BEA (FRED: DSPIC96)"
    },
    "PSAVERT": {
        "name": "個人儲蓄率",
        "group": "income",
        "freq": "monthly",
        "unit": "% (季調)",
        "calc_yoy": False,
        "scope_badge": "儲蓄佔可支配所得比例",
        "source": "BEA (FRED: PSAVERT)"
    },
    "MEHOINUSA672N": {
        "name": "實質家庭中位數所得",
        "group": "income",
        "freq": "annual",
        "unit": "美元 (已扣通膨)",
        "calc_yoy": True,
        "scope_badge": "稅前｜僅含現金補助｜已扣通膨",
        "source": "US Census (FRED: MEHOINUSA672N)"
    },
    # 2. 財富分化與財務警訊
    "WFRBST01134": {
        "name": "頂層 1% 淨財富佔比",
        "group": "wealth",
        "freq": "quarterly",
        "unit": "%",
        "calc_yoy": False,
        "scope_badge": "淨財富 = 總資產 - 總負債 (不含社安金現值)",
        "source": "Fed DFA (FRED: WFRBST01134)"
    },
    "WFRBSB50215": {
        "name": "底層 50% 淨財富佔比",
        "group": "wealth",
        "freq": "quarterly",
        "unit": "%",
        "calc_yoy": False,
        "scope_badge": "淨財富 = 總資產 - 總負債 (不含社安金現值)",
        "source": "Fed DFA (FRED: WFRBSB50215)"
    },
    "DRCCLACBS": {
        "name": "商業銀行信用卡違約率",
        "group": "wealth",
        "freq": "quarterly",
        "unit": "% (季調)",
        "calc_yoy": False,
        "scope_badge": "逾期30天以上｜底層與青年財務領先指標",
        "source": "Fed (FRED: DRCCLACBS)"
    },
    # 3. 通膨與生活成本
    "PCEPI": {
        "name": "整體 PCE 物價指數",
        "group": "inflation",
        "freq": "monthly",
        "unit": "指數 (計算YoY%)",
        "calc_yoy": True,
        "scope_badge": "聯準會基準｜動態反映消費替代",
        "source": "BEA (FRED: PCEPI)"
    },
    "PCEPILFE": {
        "name": "核心 PCE 物價指數",
        "group": "inflation",
        "freq": "monthly",
        "unit": "指數 (計算YoY%)",
        "calc_yoy": True,
        "scope_badge": "剔除食品與能源",
        "source": "BEA (FRED: PCEPILFE)"
    },
    "CUSR0000SAH1": {
        "name": "CPI：居住類分項",
        "group": "inflation",
        "freq": "monthly",
        "unit": "指數 (計算YoY%)",
        "calc_yoy": True,
        "scope_badge": "無房族與底層剛性痛點",
        "source": "BLS (FRED: CUSR0000SAH1)"
    },
    # 4. 消費轉向（實質與通路）
    "PCEDGC96": {
        "name": "實質耐久財消費",
        "group": "consumption",
        "freq": "monthly",
        "unit": "十億美元 (2017連鎖美元)",
        "calc_yoy": True,
        "scope_badge": "已扣通膨｜汽車、家電、家具",
        "source": "BEA (FRED: PCEDGC96)"
    },
    "PCENDC96": {
        "name": "實質非耐久財消費",
        "group": "consumption",
        "freq": "monthly",
        "unit": "十億美元 (2017連鎖美元)",
        "calc_yoy": True,
        "scope_badge": "已扣通膨｜食品、汽油、日用品",
        "source": "BEA (FRED: PCENDC96)"
    },
    "PCESC96": {
        "name": "實質服務類消費",
        "group": "consumption",
        "freq": "monthly",
        "unit": "十億美元 (2017連鎖美元)",
        "calc_yoy": True,
        "scope_badge": "已扣通膨｜醫療、餐旅、金融等服務",
        "source": "BEA (FRED: PCESC96)"
    },
    "DFSARX1Q020SBEA": {
        "name": "實質餐飲與住宿服務",
        "group": "consumption",
        "freq": "quarterly",
        "unit": "十億美元 (2017連鎖美元)",
        "calc_yoy": True,
        "scope_badge": "已扣通膨｜體驗經濟溫度計",
        "source": "BEA (FRED: DFSARX1Q020SBEA)"
    },
    "RSGCS": {
        "name": "雜貨超市零售額",
        "group": "retail",
        "freq": "monthly",
        "unit": "百萬美元 (名目, 季調)",
        "calc_yoy": True,
        "scope_badge": "名目金額｜在家自煮指標",
        "source": "US Census (FRED: RSGCS)"
    },
    "MRTSSM722USS": {
        "name": "餐飲與飲酒場所零售額",
        "group": "retail",
        "freq": "monthly",
        "unit": "百萬美元 (名目, 季調)",
        "calc_yoy": True,
        "scope_badge": "名目金額｜外食消費指標",
        "source": "US Census (FRED: MRTSSM722USS)"
    },
    "MRTSSM452USS": {
        "name": "綜合百貨與大賣場零售額",
        "group": "retail",
        "freq": "monthly",
        "unit": "百萬美元 (名目, 季調)",
        "calc_yoy": True,
        "scope_badge": "名目金額｜Walmart/Target/Costco 通路",
        "source": "US Census (FRED: MRTSSM452USS)"
    },
    "ECOMPCTSA": {
        "name": "電商佔總零售比例",
        "group": "retail",
        "freq": "quarterly",
        "unit": "% (季調)",
        "calc_yoy": False,
        "scope_badge": "線上通路長期滲透率",
        "source": "US Census (FRED: ECOMPCTSA)"
    },
    # 5. 餐廚與容器品類總盤（BEA）
    "DUTERX1A020NBEA": {
        "name": "餐廚與玻璃器皿：實質消費量",
        "group": "hs3924",
        "freq": "annual",
        "unit": "十億美元 (2017連鎖美元)",
        "calc_yoy": True,
        "scope_badge": "已扣通膨｜美國家庭餐廚容器總實質需求",
        "source": "BEA (FRED: DUTERX1A020NBEA)"
    },
    "DUTERG3A086NBEA": {
        "name": "餐廚與玻璃器皿：物價指數",
        "group": "hs3924",
        "freq": "annual",
        "unit": "指數 (2017=100)",
        "calc_yoy": True,
        "scope_badge": "品類價格戰溫度計（下跌代表通縮促銷）",
        "source": "BEA (FRED: DUTERG3A086NBEA)"
    },
}

CENSUS_HS_CONFIG = {
    "392410": {
        "name": "塑膠製餐具及廚房用具 (HS 3924.10)",
        "comm_lvl": "HS6",
        "role": "目標品類大項"
    },
    "3924104000": {
        "name": "其他塑膠餐廚容器 (HTS 3924.10.4000)",
        "comm_lvl": "HS10",
        "role": "最貼近非一次性乾式食品容器"
    },
    "701349": {
        "name": "餐廚用玻璃器皿，不含水杯 (HS 7013.49)",
        "comm_lvl": "HS6",
        "role": "玻璃替代去向"
    },
    "732393": {
        "name": "不鏽鋼餐廚與家用品 (HS 7323.93)",
        "comm_lvl": "HS6",
        "role": "不鏽鋼替代去向"
    }
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_JSON = DATA_DIR / "data.json"
ANALYSIS_JSON = DATA_DIR / "analysis.json"


# ---------------------------------------------------------------------------
# 2. 數據抓取工具函數
# ---------------------------------------------------------------------------
def fetch_fred_series(series_id: str, api_key: str, start_date: str) -> List[Dict[str, Any]]:
    """從 FRED 官方 API 抓取原始數據，不做任何內插補值。"""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    rows = []
    for o in obs:
        val_str = o.get("value", ".")
        val = None if val_str in (".", "", None) else float(val_str)
        rows.append({"date": o["date"], "value": val})
    return rows


def compute_yoy_and_stats(
    rows: List[Dict[str, Any]], freq: str, calc_yoy: bool, cutoff_date: str
) -> Dict[str, Any]:
    """計算年增率（嚴格對齊去年同期，缺值即為 null）與 10 年歷史分位數。"""
    df = pd.DataFrame(rows)
    if df.empty:
        return {"series": [], "latest": None, "stats_10y": {}}

    df["date_dt"] = pd.to_datetime(df["date"])
    df = df.sort_values("date_dt").reset_index(drop=True)

    # 計算精確 YoY（按實際日期對齊去年同期，避免缺漏月份導致 shift 錯位）
    if calc_yoy:
        lookup = dict(zip(df["date_dt"], df["value"]))
        yoy_vals = []
        for _, r in df.iterrows():
            v = r["value"]
            prev_dt = r["date_dt"] - pd.DateOffset(years=1)
            prev_v = lookup.get(prev_dt)
            if pd.notna(v) and pd.notna(prev_v) and prev_v != 0:
                yoy_vals.append(round((v / prev_v - 1.0) * 100.0, 2))
            else:
                yoy_vals.append(None)
        df["yoy"] = yoy_vals
    else:
        df["yoy"] = None

    # 裁切至真正要呈現的 10 年區間（抓取時多抓 1 年是為了計算第一年的 YoY）
    df_10y = df[df["date_dt"] >= pd.to_datetime(cutoff_date)].copy()

    # 決定用來算歷史定位的主數值（若為物價/消費指數則看 YoY，否則看原值）
    metric_col = "yoy" if calc_yoy else "value"
    valid_series = df_10y[metric_col].dropna()

    stats = {}
    if not valid_series.empty:
        latest_row = df_10y.dropna(subset=[metric_col]).iloc[-1]
        latest_val = float(latest_row[metric_col])
        pct_rank = float((valid_series <= latest_val).mean() * 100.0)
        stats = {
            "metric": metric_col,
            "latest_date": latest_row["date"],
            "latest_val": round(latest_val, 2),
            "min_10y": round(float(valid_series.min()), 2),
            "median_10y": round(float(valid_series.median()), 2),
            "max_10y": round(float(valid_series.max()), 2),
            "percentile_10y": round(pct_rank, 1),
        }

    latest_raw_df = df_10y.dropna(subset=["value"])
    latest_raw = None
    if not latest_raw_df.empty:
        lr = latest_raw_df.iloc[-1]
        latest_raw = {
            "date": lr["date"],
            "value": round(float(lr["value"]), 3),
            "yoy": None if pd.isna(lr["yoy"]) else round(float(lr["yoy"]), 2),
        }

    records = []
    for _, r in df_10y.iterrows():
        records.append({
            "date": r["date"],
            "value": None if pd.isna(r["value"]) else round(float(r["value"]), 3),
            "yoy": None if pd.isna(r["yoy"]) else round(float(r["yoy"]), 2),
        })

    return {"series": records, "latest": latest_raw, "stats_10y": stats}


def fetch_census_hs_imports(
    hs_code: str, comm_lvl: str, start_ym: str, api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """從美國人口普查局國際貿易 API 抓取月度美國總進口金額（美元）。"""
    url = "https://api.census.gov/data/timeseries/intltrade/imports/hs"
    params = {
        "get": "GEN_VAL_MO,I_COMMODITY",
        "I_COMMODITY": hs_code,
        "COMM_LVL": comm_lvl,
        "time": f"from+{start_ym}",
    }
    if api_key:
        params["key"] = api_key

    # requests 會自動 encode '+'，Census API 的 'from+YYYY-MM' 需保留字面 '+'
    query_str = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{query_str}"

    resp = requests.get(full_url, timeout=45)
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    raw = resp.json()
    if len(raw) < 2:
        return []

    headers = raw[0]
    val_idx = headers.index("GEN_VAL_MO")
    time_idx = headers.index("time")

    monthly: Dict[str, float] = {}
    for row in raw[1:]:
        ym = row[time_idx]
        try:
            v = float(row[val_idx])
        except (ValueError, TypeError):
            continue
        monthly[ym] = monthly.get(ym, 0.0) + v

    return [{"date": f"{ym}-01", "ym": ym, "value_usd": round(val, 2)} for ym, val in sorted(monthly.items())]


def build_derived_signals(fred_data: Dict[str, Any], census_data: Dict[str, Any]) -> Dict[str, Any]:
    """計算網站專用的三大衍生交叉訊號與 HS 3924 替代份額。"""
    derived: Dict[str, Any] = {}

    # 1. 外食 ÷ 超市 剪刀差 (MRTSSM722USS / RSGCS)
    din = {r["date"]: r["value"] for r in fred_data.get("MRTSSM722USS", {}).get("series", []) if r["value"]}
    gro = {r["date"]: r["value"] for r in fred_data.get("RSGCS", {}).get("series", []) if r["value"]}
    common_dates = sorted(set(din.keys()) & set(gro.keys()))
    derived["dining_to_grocery_ratio"] = [
        {"date": d, "value": round(din[d] / gro[d], 4)} for d in common_dates
    ]

    # 2. 耐久財 vs 非耐久財 實質增速差 (PCEDGC96 YoY - PCENDC96 YoY)
    dur = {r["date"]: r["yoy"] for r in fred_data.get("PCEDGC96", {}).get("series", []) if r["yoy"] is not None}
    nondur = {r["date"]: r["yoy"] for r in fred_data.get("PCENDC96", {}).get("series", []) if r["yoy"] is not None}
    common_d2 = sorted(set(dur.keys()) & set(nondur.keys()))
    derived["durables_vs_nondurables_spread"] = [
        {"date": d, "value": round(dur[d] - nondur[d], 2)} for d in common_d2
    ]

    # 3. HS 392410 (塑膠) vs 701349 (玻璃) vs 732393 (不鏽鋼) 12個月滾動進口額與相對份額
    p_map = {r["ym"]: r["value_usd"] for r in census_data.get("392410", {}).get("series", [])}
    g_map = {r["ym"]: r["value_usd"] for r in census_data.get("701349", {}).get("series", [])}
    s_map = {r["ym"]: r["value_usd"] for r in census_data.get("732393", {}).get("series", [])}
    all_yms = sorted(set(p_map.keys()) & set(g_map.keys()) & set(s_map.keys()))

    rolling_records = []
    if len(all_yms) >= 12:
        df_c = pd.DataFrame({
            "ym": all_yms,
            "plastic": [p_map[m] for m in all_yms],
            "glass": [g_map[m] for m in all_yms],
            "steel": [s_map[m] for m in all_yms],
        })
        df_c["plastic_12m"] = df_c["plastic"].rolling(12).sum()
        df_c["glass_12m"] = df_c["glass"].rolling(12).sum()
        df_c["steel_12m"] = df_c["steel"].rolling(12).sum()
        df_c["total_12m"] = df_c["plastic_12m"] + df_c["glass_12m"] + df_c["steel_12m"]

        for _, r in df_c.dropna().iterrows():
            tot = r["total_12m"]
            if tot <= 0:
                continue
            rolling_records.append({
                "ym": r["ym"],
                "plastic_12m_musd": round(r["plastic_12m"] / 1e6, 2),
                "glass_12m_musd": round(r["glass_12m"] / 1e6, 2),
                "steel_12m_musd": round(r["steel_12m"] / 1e6, 2),
                "plastic_share_pct": round(r["plastic_12m"] / tot * 100.0, 2),
                "glass_share_pct": round(r["glass_12m"] / tot * 100.0, 2),
                "steel_share_pct": round(r["steel_12m"] / tot * 100.0, 2),
            })
    derived["hs_material_substitution_12m"] = rolling_records
    return derived


# ---------------------------------------------------------------------------
# 3. Gemini 綜合判讀引擎（四條防呆鐵律）
# ---------------------------------------------------------------------------
def run_gemini_analysis(payload: Dict[str, Any], api_key: str, model_name: str) -> Dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    snapshot = {
        "generated_at_utc": payload["meta"]["generated_at_utc"],
        "fred_latest_and_10y_stats": {
            k: {
                "name": v["meta"]["name"],
                "scope_badge": v["meta"]["scope_badge"],
                "latest": v["latest"],
                "stats_10y": v["stats_10y"],
            }
            for k, v in payload["fred"].items()
        },
        "derived_latest": {
            "dining_to_grocery_ratio_last_6": payload["derived"]["dining_to_grocery_ratio"][-6:],
            "durables_vs_nondurables_spread_last_6": payload["derived"]["durables_vs_nondurables_spread"][-6:],
            "hs_material_substitution_12m_first_and_last": (
                [payload["derived"]["hs_material_substitution_12m"][0],
                 payload["derived"]["hs_material_substitution_12m"][-1]]
                if len(payload["derived"]["hs_material_substitution_12m"]) >= 2
                else payload["derived"]["hs_material_substitution_12m"]
            ),
        },
    }

    system_instruction = """你是一位極度嚴謹的美國總體經濟與消費產業研究分析師。
你必須嚴格遵守以下四條防呆鐵律，絕不可違背：
1. 【數據鎖定】只准引用輸入 JSON 中真實存在的數字與日期。若某指標為 null 或未更新，必須明確寫出「官方尚未發布或無數據」，嚴禁自行推算或編造任何數字。
2. 【四維交叉驗證】必須同步交叉檢核：(a) 實質可支配所得 vs 個人儲蓄率、(b) 核心 PCE vs 居住 CPI、(c) 耐久財/非耐久財/服務消費與外食超市比、(d) 頂層1% vs 底層50%財富與信用卡違約率。
3. 【口徑強制聲明】提及實質可支配所得(DSPIC96)時，必須明示其為「稅後、含政府補助、已扣通膨、不含資本利得」；提及家庭中位數所得(MEHOINUSA672N)時，必須明示其為「稅前、僅含現金補助」。
4. 【歷史定位與產業提醒】所有關鍵指標需對照 10 年歷史分位數判讀；判讀 HS 3924 塑膠餐廚容器 vs 玻璃(701349)/不鏽鋼(732393) 時，必須註明美國海關進口數據不包含美國本土射出廠內銷份額。

請輸出嚴格符合 JSON Schema 的繁體中文分析報告。"""

    prompt = f"以下是最新抓取的美國官方統計快照（FRED + US Census），請產出本期綜合判讀：\n{json.dumps(snapshot, ensure_ascii=False)}"

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "headline": {"type": "STRING", "description": "一句話總結本期美國民眾經濟與消費現況（不超過45字）"},
            "executive_summary": {"type": "STRING", "description": "200字內的全局總結，涵蓋所得、通膨、消費轉向與財富分化"},
            "cards": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "status": {"type": "STRING", "enum": ["正向", "中性警戒", "壓力升溫"]},
                        "analysis": {"type": "STRING"},
                    },
                    "required": ["title", "status", "analysis"],
                },
            },
            "hs3924_verdict": {
                "type": "OBJECT",
                "properties": {
                    "summary": {"type": "STRING", "description": "HS 3924 塑膠容器 vs 玻璃/不鏽鋼 5年替代趨勢與終端價格戰判讀"},
                    "strategic_implication": {"type": "STRING", "description": "對非一次性塑膠乾式食品容器製造商的實務啟示"},
                },
                "required": ["summary", "strategic_implication"],
            },
            "methodology_notes": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "本期特別需注意的口徑限制、缺漏或統計時滯提醒"
            }
        },
        "required": ["headline", "executive_summary", "cards", "hs3924_verdict", "methodology_notes"],
    }

    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.2,
        ),
    )
    result = json.loads(resp.text)
    result["analyzed_at_utc"] = datetime.now(timezone.utc).isoformat()
    result["model"] = model_name
    return result


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-analyze", action="store_true", help="強制呼叫 Gemini 產出判讀")
    args = parser.parse_args()

    fred_key = os.getenv("FRED_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    census_key = os.getenv("CENSUS_API_KEY", "").strip() or None
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    if not fred_key:
        print("ERROR: 環境變數 FRED_API_KEY 未設定。請至 https://fred.stlouisfed.org/docs/api/api_key.html 免費申請。", file=sys.stderr)
        sys.exit(1)

    today = date.today()
    cutoff_10y = date(today.year - 10, today.month, 1)
    fetch_start_11y = date(today.year - 11, today.month, 1).isoformat()
    cutoff_10y_str = cutoff_10y.isoformat()
    census_start_ym = f"{today.year - 6}-{today.month:02d}"  # 多抓1年供12個月滾動計算

    print(f"[1/4] 開始抓取 FRED 近 10 年官方數據 (cutoff={cutoff_10y_str})...")
    fred_result: Dict[str, Any] = {}
    latest_dates_signature: Dict[str, Optional[str]] = {}

    for sid, meta in FRED_SERIES_CONFIG.items():
        try:
            raw_rows = fetch_fred_series(sid, fred_key, fetch_start_11y)
            processed = compute_yoy_and_stats(raw_rows, meta["freq"], meta["calc_yoy"], cutoff_10y_str)
            fred_result[sid] = {"meta": meta, **processed}
            latest_dates_signature[f"FRED:{sid}"] = processed["latest"]["date"] if processed["latest"] else None
            print(f"  ✓ {sid} ({meta['name']}): 最新={latest_dates_signature[f'FRED:{sid}']}")
        except Exception as e:
            print(f"  ✗ {sid} 抓取失敗: {e}", file=sys.stderr)
            fred_result[sid] = {"meta": meta, "series": [], "latest": None, "stats_10y": {}, "error": str(e)}

    print(f"[2/4] 開始抓取 US Census 國際貿易近 5 年月度數據 (from {census_start_ym})...")
    census_result: Dict[str, Any] = {}
    for hs_code, meta in CENSUS_HS_CONFIG.items():
        try:
            rows = fetch_census_hs_imports(hs_code, meta["comm_lvl"], census_start_ym, census_key)
            census_result[hs_code] = {
                "meta": meta,
                "series": rows,
                "latest": rows[-1] if rows else None,
            }
            latest_dates_signature[f"CENSUS:{hs_code}"] = rows[-1]["ym"] if rows else None
            print(f"  ✓ HS {hs_code}: 共 {len(rows)} 個月, 最新={latest_dates_signature[f'CENSUS:{hs_code}']}")
        except Exception as e:
            print(f"  ✗ HS {hs_code} 抓取失敗: {e}", file=sys.stderr)
            census_result[hs_code] = {"meta": meta, "series": [], "latest": None, "error": str(e)}

    print("[3/4] 計算衍生交叉訊號與 HS 3924 替代份額...")
    derived = build_derived_signals(fred_result, census_result)

    old_signature = {}
    if DATA_JSON.exists():
        try:
            old_data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            old_signature = old_data.get("meta", {}).get("latest_dates", {})
        except Exception:
            pass

    has_new_data = (latest_dates_signature != old_signature) or (not ANALYSIS_JSON.exists())

    payload = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "window_macro": f"{cutoff_10y_str} ~ {today.isoformat()}",
            "window_trade": f"{census_start_ym} ~ 最新海關發布月",
            "latest_dates": latest_dates_signature,
        },
        "fred": fred_result,
        "census": census_result,
        "derived": derived,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已寫入 {DATA_JSON}")

    if (has_new_data or args.force_analyze) and gemini_key:
        print(f"[4/4] 偵測到數據更新（或強制分析），呼叫 Gemini ({gemini_model}) 產出綜合判讀...")
        try:
            analysis = run_gemini_analysis(payload, gemini_key, gemini_model)
            ANALYSIS_JSON.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ 已寫入 {ANALYSIS_JSON}")
        except Exception as e:
            print(f"  ✗ Gemini 分析失敗: {e}", file=sys.stderr)
    elif not gemini_key:
        print("[4/4] 未設定 GEMINI_API_KEY，略過 AI 綜合判讀。")
    else:
        print("[4/4] 各項官方指標皆無新月份更新，略過 Gemini 呼叫以節省額度。")


if __name__ == "__main__":
    main()
