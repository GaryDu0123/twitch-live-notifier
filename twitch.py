import asyncio
import json
import os
import time
import re
from typing import Optional, List, Dict, Any
import base64
import aiohttp
from hoshino import Service, priv, util
from hoshino.typing import CQEvent, HoshinoBot
from .config import (
    TWITCH_APP_ID,
    TWITCH_APP_SECRET,
    TWITCH_CHECK_INTERVAL,
    TWITCH_PROXY_URL,
    TWITCH_SEND_IMAGE,
    TWITCH_DISABLE_SENSITIVE_FILTER
)

sv_help = """
[添加twitch订阅 主播ID] 添加一位主播的Live提醒
[取消twitch订阅 主播ID] 取消一位主播的Live提醒
[twitch订阅列表] 查看本群的Twitch订阅
(指令需要群主/管理员权限)
""".strip()

sv = Service(
    name="twitch直播监控",
    use_priv=priv.ADMIN,  # 默认指令需要管理员权限
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=False,
    bundle="娱乐",
    help_=sv_help
)

# ============================================================================
# 数据持久化处理
# ============================================================================
# 数据文件存放路径
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
# 订阅关系文件: { "gid": ["streamer1", "streamer2"] }
GROUP_SUBS_FILE = os.path.join(DATA_DIR, "group_subs.json")
# 主播到群组的反向映射: { "streamer1": ["gid1", "gid2"] }
STREAMER_SUBS_FILE = os.path.join(DATA_DIR, "streamer_subs.json")
# 在线状态缓存: { "live": ["streamer1", "streamer3"] }
LIVE_STATUS_FILE = os.path.join(DATA_DIR, "live_status.json")

# 确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(file_path: str, default_val: Any) -> Any:
    """读取JSON文件, 文件不存在时返回默认值"""
    if not os.path.exists(file_path):
        return default_val
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default_val


def _save_json(data: Any, file_path: str):
    """保存数据到JSON文件"""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API_BASE_URL = "https://api.twitch.tv/helix"


class TwitchAPIClient:
    def __init__(self, app_id: str, app_secret: str, proxy: Optional[str] = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.proxy = proxy
        self._session: Optional[aiohttp.ClientSession] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_expiry_safety_margin: int = 120  # 提前2分钟刷新

    async def _create_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _renew_token(self):
        sv.logger.info("Twitch令牌已过期或不存在，正在获取新的应用访问令牌...")
        session = await self._create_session()
        params = {"client_id": self.app_id, "client_secret": self.app_secret, "grant_type": "client_credentials"}
        try:
            async with session.post(TWITCH_AUTH_URL, params=params, proxy=self.proxy) as response:
                response.raise_for_status()
                data = await response.json()
                self._access_token = data["access_token"]
                expires_in = data["expires_in"]
                self._token_expires_at = time.time() + expires_in - self._token_expiry_safety_margin
                sv.logger.info(f"成功获取新的Twitch访问令牌，将在约 {(expires_in / 3600):.1f} 小时后过期。")
        except aiohttp.ClientError as e:
            sv.logger.error(f"获取Twitch令牌失败: {e}")
            raise

    async def _ensure_token_valid(self):
        if self._access_token is None or time.time() >= self._token_expires_at:
            await self._renew_token()

    async def get_streams(self, user_logins: List[str]) -> Optional[List[Dict[str, Any]]]:
        if not user_logins:
            return []

        # Twitch API一次最多查询100个用户
        chunk_size = 100
        all_streams_data = []

        for i in range(0, len(user_logins), chunk_size):
            chunk = user_logins[i:i + chunk_size]
            try:
                await self._ensure_token_valid()
                session = await self._create_session()
                headers = {"Client-ID": self.app_id, "Authorization": f"Bearer {self._access_token}"}
                params = [("user_login", login) for login in chunk]
                # https://dev.twitch.tv/docs/api/reference/#get-streams
                async with session.get(f"{TWITCH_API_BASE_URL}/streams", headers=headers, params=params,
                                       proxy=self.proxy) as response:
                    if response.status == 401:
                        sv.logger.warning("Twitch API返回401，将强制刷新令牌后重试...")
                        await self._renew_token()
                        return await self.get_streams(user_logins)  # 重试整个请求
                    response.raise_for_status()
                    data = await response.json()
                    all_streams_data.extend(data.get("data", []))
            except aiohttp.ClientError as e:
                sv.logger.error(f"调用 Twitch API '/streams' 失败: {e}")
                return None
            except Exception as e:
                sv.logger.error(f"处理Twitch API请求时发生未知错误: {e}")
                return None
        return all_streams_data

    async def get_users(self, user_logins: List[str]) -> Optional[List[Dict[str, Any]]]:
        """
        根据登录名获取用户信息，用于验证用户是否存在。
        """
        if not user_logins:
            return []
        try:
            await self._ensure_token_valid()
            session = await self._create_session()
            headers = {"Client-ID": self.app_id, "Authorization": f"Bearer {self._access_token}"}
            params = [("login", login) for login in user_logins]
            # https://dev.twitch.tv/docs/api/reference/#get-users
            async with session.get(f"{TWITCH_API_BASE_URL}/users", headers=headers, params=params,
                                   proxy=self.proxy) as response:
                if response.status == 401:
                    sv.logger.warning("Twitch API (users) 返回401，将强制刷新令牌后重试...")
                    await self._renew_token()
                    return await self.get_users(user_logins)  # 重试
                response.raise_for_status()
                data = await response.json()
                return data.get("data", [])
        except aiohttp.ClientError as e:
            sv.logger.error(f"调用 Twitch API '/users' 失败: {e}")
            return None
        except Exception as e:
            sv.logger.error(f"处理Twitch API /users 请求时发生未知错误: {e}")
            return None


# 创建全局客户端实例
twitch_client = TwitchAPIClient(TWITCH_APP_ID, TWITCH_APP_SECRET, proxy=TWITCH_PROXY_URL)


# ============================================================================
# Hoshino 指令处理
# ============================================================================
@sv.on_prefix(('添加twitch订阅', '关注twitch主播'))
async def add_twitch_sub(bot: HoshinoBot, ev: CQEvent):
    gid = str(ev.group_id)
    streamer_id = ev.message.extract_plain_text().strip().lower()

    if not re.fullmatch(r"^[a-zA-Z0-9][a-zA-Z0-9_]{3,24}$", streamer_id):
        await bot.send(ev, "请输入有效的 Twitch 主播ID！")
        return

    group_subs = _load_json(GROUP_SUBS_FILE, {})
    streamer_subs = _load_json(STREAMER_SUBS_FILE, {})

    if streamer_id in group_subs.get(gid, []):
        await bot.send(ev, f"本群已经订阅了主播: {streamer_id}")
        return
    # --- 验证逻辑 ---
    try:
        user_data_list = await twitch_client.get_users([streamer_id])

        if user_data_list is None:
            await bot.send(ev, "验证失败，无法连接到 Twitch API，请稍后再试。")
            return
        if not user_data_list:
            await bot.send(ev, f"未找到名为 '{streamer_id}' 的Twitch主播，请检查ID是否拼写正确。")
            return

        # 从返回结果中获取规范的ID和显示名称，避免大小写等问题
        actual_user = user_data_list[0]
        actual_id = actual_user['login']
        actual_display_name = actual_user['display_name']

    except Exception as e:
        sv.logger.error(f"验证Twitch用户 {streamer_id} 时发生错误: {e}")
        await bot.send(ev, "验证过程中发生未知错误，请稍后再试。")
        return

    # 更新订阅关系
    group_subs.setdefault(gid, []).append(actual_id)
    streamer_subs.setdefault(actual_id, []).append(gid)

    _save_json(group_subs, GROUP_SUBS_FILE)
    _save_json(streamer_subs, STREAMER_SUBS_FILE)

    await bot.send(ev, f"✅ 订阅成功！\n将接收 {actual_display_name} ({actual_id}) 的开播通知。")


@sv.on_prefix(('取消twitch订阅', '取关twitch主播'))
async def remove_twitch_sub(bot: HoshinoBot, ev: CQEvent):
    gid = str(ev.group_id)
    streamer_id = ev.message.extract_plain_text().strip().lower()

    if not streamer_id:
        await bot.send(ev, "请输入要取关的主播ID。")
        return

    group_subs = _load_json(GROUP_SUBS_FILE, {})
    streamer_subs = _load_json(STREAMER_SUBS_FILE, {})

    if streamer_id not in group_subs.get(gid, []):
        await bot.send(ev, f"本群没有订阅主播: {streamer_id}")
        return

    # 更新订阅关系
    group_subs[gid].remove(streamer_id)
    if not group_subs[gid]:
        del group_subs[gid]

    streamer_subs[streamer_id].remove(gid)
    if not streamer_subs[streamer_id]:
        del streamer_subs[streamer_id]

    _save_json(group_subs, GROUP_SUBS_FILE)
    _save_json(streamer_subs, STREAMER_SUBS_FILE)

    await bot.send(ev, f"成功为本群取消对 {streamer_id} 的订阅。")


@sv.on_fullmatch(('twitch订阅列表', '查看twitch订阅'))
async def list_twitch_subs(bot: HoshinoBot, ev: CQEvent):
    gid = str(ev.group_id)
    group_subs = _load_json(GROUP_SUBS_FILE, {})
    subs = group_subs.get(gid, [])

    if not subs:
        await bot.send(ev, "本群还没有任何 Twitch 订阅。")
        return

    msg = "本群的 Twitch 订阅列表：\n- " + "\n- ".join(subs)
    await bot.send(ev, msg)


# ============================================================================
# 定时检查任务
# ============================================================================
async def _get_thumbnail_as_cq_image_text(
        session: aiohttp.ClientSession,
        stream_data: Dict[str, Any]
) -> str:
    """
    下载直播封面图，转换为Base64并返回CQ码字符串。
    如果失败，则返回空字符串。
    """
    thumbnail_url = stream_data.get('thumbnail_url', '').replace('{width}', '320').replace('{height}', '180')
    if not thumbnail_url:
        return ""

    try:
        # 使用传入的 session 和配置的代理来下载图片
        async with session.get(thumbnail_url, proxy=TWITCH_PROXY_URL, timeout=10) as response:
            if response.status == 200:
                image_bytes = await response.read()
                base64_str = base64.b64encode(image_bytes).decode('utf-8')
                return f"[CQ:image,file=base64://{base64_str}]"
            else:
                sv.logger.warning(
                    f"下载直播封面图失败 ({stream_data.get('user_login', 'N/A')}), HTTP状态码: {response.status}")
                return ""
    except Exception as e:
        sv.logger.error(f"下载直播封面图时发生网络错误 ({stream_data.get('user_login', 'N/A')}): {e}")
        return ""


@sv.scheduled_job('interval', minutes=TWITCH_CHECK_INTERVAL)
async def twitch_monitor_task():
    bot = sv.bot
    streamer_subs = _load_json(STREAMER_SUBS_FILE, {})
    if not streamer_subs:
        return

    all_streamers_to_check = list(streamer_subs.keys())
    sv.logger.info(f"Twitch监控：开始检查 {len(all_streamers_to_check)} 位主播的状态...")

    streams_data = await twitch_client.get_streams(all_streamers_to_check)
    if streams_data is None:
        sv.logger.warning("Twitch监控：获取直播列表失败，将在下一周期重试。")
        return

    live_status = _load_json(LIVE_STATUS_FILE, {"live": []})
    previously_online = set(live_status.get("live", []))
    currently_online = {stream['user_login'].lower() for stream in streams_data}
    newly_started = currently_online - previously_online

    if not newly_started:
        sv.logger.info("Twitch监控：没有新开播的主播。")
    else:
        sv.logger.info(f"Twitch监控：检测到 {len(newly_started)} 位新开播的主播: {', '.join(newly_started)}")
        session = await twitch_client._create_session()

        for stream in streams_data:
            streamer_login = stream['user_login'].lower()
            if streamer_login in newly_started:
                # 构建基础文本消息
                final_msg = (
                    f"【Twitch 开播提醒】🎉\n"
                    f"主播: {stream['user_name']} ({stream['user_login']})\n"
                    f"标题: {stream['title'] if TWITCH_DISABLE_SENSITIVE_FILTER else util.filt_message(stream['title'])}\n"
                    f"游戏: {stream['game_name']}\n"
                    # f"链接: https://www.twitch.tv/{streamer_login}"
                )

                # 根据配置决定是否获取图片
                if TWITCH_SEND_IMAGE:
                    final_msg += await _get_thumbnail_as_cq_image_text(session, stream)

                # 向所有订阅了该主播的群组发送通知
                subscribed_groups = streamer_subs.get(streamer_login, [])
                for gid in subscribed_groups:
                    try:
                        await bot.send_group_msg(group_id=int(gid), message=final_msg)
                        sv.logger.info(f"成功向群 {gid} 推送了 {streamer_login} 的开播通知。")
                        await asyncio.sleep(1)  # 简单的防风控
                    except Exception as e:
                        sv.logger.error(f"向群 {gid} 推送失败: {e}")

    # 更新在线状态缓存
    _save_json({"live": list(currently_online)}, LIVE_STATUS_FILE)