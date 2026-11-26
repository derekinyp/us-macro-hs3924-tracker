# 美國經濟生活、消費轉向與 HS 3924 產業自動追蹤網站

本專案以 **GitHub Actions + Python + FRED API + US Census API + Gemini API + GitHub Pages** 建構，零伺服器成本，每月自動抓取美國官方近 10 年（貿易端近 5 年）原始序列，並在數據更新時自動產出 AI 綜合判讀。

## 核心設計原則
1. **100% 官方原始序列、零內插**：若官方某月缺漏（如政府停擺），圖表直接留白斷開，絕不補值。
2. **口徑強制明示**：
   - `DSPIC96`（實質可支配個人所得）：**稅後｜含政府補助｜已扣通膨｜不含資本利得**
   - `MEHOINUSA672N`（實質家庭中位數所得）：**稅前｜僅含現金補助｜已扣通膨**
   - `HS 392410` / `701349` / `732393`（US Census 海關進口）：**僅含美國進口端，不含美國本土射出廠內銷**
3. **變更偵測省額度**：僅在偵測到新月份／新季度數據時才呼叫 Gemini API。

## 5 分鐘部署到 GitHub Pages
1. 在 GitHub 建立一個新的 **Public Repository**（例如 `us-macro-hs3924-tracker`）。
2. 將本壓縮檔解壓後的所有檔案（含 `.github/workflows/update_data.yml`）推送或上傳至 Repository 根目錄。
3. 前往 **Settings → Secrets and variables → Actions → New repository secret**，新增兩組免費金鑰：
   - `FRED_API_KEY`：至 https://fred.stlouisfed.org/docs/api/api_key.html 免費申請
   - `GEMINI_API_KEY`：至 https://aistudio.google.com/apikey 免費申請
   - *(選填)* `CENSUS_API_KEY`：US Census 貿易 API 不填也能用，若流量大可免費申請
4. 前往 **Settings → Pages**：Source 選擇 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/ (root)`，按 Save。
5. 前往 **Actions** 分頁，點選左側 `Update US Macro & HS3924 Data`，按右側 **Run workflow** 執行首次抓取。約 60 秒完成後，打開 GitHub Pages 網址即可瀏覽。
