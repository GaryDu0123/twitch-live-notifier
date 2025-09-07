# Hoshino-Twitch-Live-Notifier

一个为 [HoshinoBot](https://github.com/Ice-Cirno/HoshinoBot) 编写的 Twitch 直播监控插件。

可以定时检查群内所订阅的 Twitch 主播是否开播，并在主播上线时向群内发送一条开播通知。

<img width="350" alt="image" src="https://github.com/user-attachments/assets/c952a65f-7c4f-4959-98f0-d0564abf6b95" />


## 📖 使用说明

所有指令默认都需要**群主或管理员**权限才能触发。

* **添加twitch订阅 <主播ID>**
  *   示例: `添加twitch订阅 `
  *   说明: `主播ID` 是其个人频道 URL 中的那部分，例如 `twitch.tv/nacho_dayo` 中的 `nacho_dayo`。

* **取消twitch订阅 <主播ID>**
  *   示例: `取消twitch订阅 nacho_dayo`

* **twitch订阅列表**
  *   该指令会列出当前群组已订阅的所有 Twitch 主播。

## ⚙️ 安装与配置

#### 步骤 1: 获取插件

下载本插件项目，并将解压后的 `twitch-live-notifier` 文件夹放入 HoshinoBot 的 `modules` 目录下。

#### 步骤 2: 注册 Twitch 应用

为了与 Twitch API 交互，您需要一个 Twitch 开发者账号来获取 API 凭证。

1.  访问 [Twitch 开发者控制台](https://dev.twitch.tv/console)。
2.  在 "应用 (Applications)" 页面点击 "注册您的应用 (Register Your Application)"。
3.  填写应用信息：
    *   **名称 (Name)**: 任意填写。
    *   **OAuth Redirect URLs**: 填写 `http://localhost` 即可。
    *   **类别 (Category)**: 选择 "聊天机器人 (Chat Bot)" 或 "其他 (Other)"。
4.  创建成功后，您会得到一个 **客户端ID (Client ID)** 和 **客户端密钥 (Client Secret)**。请妥善保管它们，特别是客户端密钥。

#### 步骤 3: 编辑配置文件

```python
# config.py

# ================================================================
#                       必填配置
# ================================================================
# 填入您在 Twitch 开发者控制台获取的凭证
# Client ID
TWITCH_APP_ID = "这里填你的客户端ID"  # 字符串，例如 "your_client_id_abc123"
# Client Secret
TWITCH_APP_SECRET = "这里填你的客户端密钥" # 字符串，例如 "your_client_secret_xyz789"

# ================================================================
#                       可选配置
# ================================================================
# 检查直播状态的间隔时间（单位：分钟）
# 建议不要设置得太短，以避免达到 Twitch API 的速率限制。推荐值为 2-5 分钟。
TWITCH_CHECK_INTERVAL = 2
# 网络代理地址
TWITCH_PROXY_URL = None  # 例如: "http://127.0.0.1:7890"
# 是否发送直播封面图片
TWITCH_SEND_IMAGE = True
# 是否关闭敏感内容过滤, 只会影响直播标题的内容, 默认True是不启用过滤
TWITCH_DISABLE_SENSITIVE_FILTER = True
```

#### 步骤 4: 启用插件

1.  在 `ENABLE_SERVICES` 列表中，添加 `twitch-live-notifier`。
    ```python
    ENABLE_SERVICES = [
        'your_other_service',
        'twitch-live-notifier', # 添加这一行
    ]
    ```
2.  重启 HoshinoBot 使配置生效。


## ⚠️ 注意事项

*   请确保您的服务器网络可以访问 `https://id.twitch.tv` 和 `https://api.twitch.tv`。如果无法访问，请正确配置 `TWITCH_PROXY_URL`。
*   **客户端密钥 (Client Secret)** 非常重要，请不要泄露给任何人或提交到公开的代码仓库中。
*   该插件会定期检查订阅的主播是否开播，请合理设置 `TWITCH_CHECK_INTERVAL`，避免过于频繁的请求导致达到 Twitch API 的速率限制。
