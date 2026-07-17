# 特戰英豪組隊機器人

一個 Discord bot,用房間制幫你組隊 — 房主自訂人數與房號,大家點按鈕加入,不用打指令。

## 功能

| 指令 | 說明 |
|---|---|
| `/開房` | 建立新房間,可設定需要的人數(2-10人)與自訂房號 |
| `/房間列表` | 查看目前所有開放中的房間、房主、人數 |
| `/解散房間` | 解散指定房號的房間(房主或「管理伺服器」權限者可用) |

開房後會出現一個訊息框,附有「加入房間」「離開房間」按鈕,大家直接點按鈕即可,不用打指令。

人數湊滿時,機器人會自動 @ 所有成員、建立一個臨時語音頻道並附上連結。等大家都離開語音頻道後,該頻道會自動被刪除。

房主如果離開房間,房間會自動解散。

## 安裝步驟

### 1. 建立 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 點選 **New Application**,幫你的 bot 取個名字
3. 左側選單點 **Bot** → **Add Bot**
4. 點 **Reset Token** 複製你的 bot token(等等會用到)
5. 左側選單點 **OAuth2 → URL Generator**
   - Scopes 勾選 `bot` 和 `applications.commands`
   - Bot Permissions 勾選 `Send Messages`、`Embed Links`、`Manage Channels`(自動建立語音頻道要用)
   - 複製產生的網址,在瀏覽器打開,把 bot 邀請進你的伺服器

### 2. 安裝環境

```bash
cd valorant-team-bot
python -m venv venv
source venv/bin/activate   # Windows 用 venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 設定 Token

把 `.env.example` 改名成 `.env`,把裡面的內容換成你自己的 token:

```
DISCORD_TOKEN=你的真實token
```

### 4. 執行機器人

```bash
python bot.py
```

看到終端機顯示「已上線,準備組隊!」就代表成功了。回到 Discord 打 `/開房` 試試看!

## 之後可以擴充的方向

- 依段位(艦隊/皓月等)分類房間
- 記錄組隊歷史 / 常玩夥伴統計
- 用資料庫(如 SQLite)取代目前的記憶體儲存,重啟不遺失房間資料
- 房間逾時(例如 30 分鐘沒滿)自動解散

