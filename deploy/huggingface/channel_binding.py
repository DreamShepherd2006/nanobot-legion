#!/usr/bin/env python3
"""
channel_binding.py — 频道绑定管理（微信扫码、钉钉凭证）

安全模型：
  - 所有端点需要已通过平台 OAuth 认证的 session
  - 绑定操作直接操作实例目录，不经第三方中转
  - WeChat QR 登录流复用 nanobot 官方 weixin channel 的 ilink API 接口

路由（由 gatekeeper.py 挂载）：
  POST /api/bind/wechat/qr       → 获取微信登录二维码
  GET  /api/bind/wechat/status    → 轮询扫码状态
  POST /api/bind/dingtalk         → 写入钉钉凭证到实例 config
"""

import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# ── 直接引用官方 nanobot weixin channel 接口 ──
# pip install nanobot-ai 后即可使用
# 源码: nanobot/channels/weixin.py (HKUDS/nanobot nightly)
from nanobot.channels.weixin import (
    ILINK_APP_ID,               # "bot"
    ILINK_APP_CLIENT_VERSION,   # 0x00020101 → "131329"
    WEIXIN_CHANNEL_VERSION,     # "2.1.1"
    WeixinChannel,              # WeChat HTTP long-poll channel
)

# nanobot weixin channel 中 WeixinChannel._random_wechat_uin() 是 @staticmethod，
# 但 _make_headers() 是实例方法。QR 登录时不需要 auth token（auth=False），
# 所以我们直接构建兼容 headers，逻辑与 _make_headers() 一致。
_RANDOM_UIN = WeixinChannel._random_wechat_uin  # 直接引用官方的随机 UIN 生成

# ── In-memory pending QR sessions ──
_pending_bindings: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════
# ilink API helpers (mirrors WeixinChannel._make_headers / _api_get)
# ═══════════════════════════════════════════════════════════════

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"


def _ilink_headers(*, auth_token: str = "") -> dict[str, str]:
    """Build ilink request headers.

    Mirrors WeixinChannel._make_headers(auth=<bool>) from
    nanobot/channels/weixin.py, lines 349-370.
    QR endpoints use auth=False (no Authorization header).
    """
    headers: dict[str, str] = {
        "X-WECHAT-UIN": _RANDOM_UIN(),           # WeixinChannel._random_wechat_uin()
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "iLink-App-Id": ILINK_APP_ID,            # from nanobot.channels.weixin
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),  # from nanobot
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


async def _ilink_get(endpoint: str, params: dict | None = None) -> dict:
    """GET ilink API — mirrors WeixinChannel._api_get().

    Ref: nanobot/channels/weixin.py, lines 381-394.
    """
    url = f"{ILINK_BASE_URL}/{endpoint}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=_ilink_headers())
        resp.raise_for_status()
        return resp.json()


async def _ilink_get_with_base(base_url: str, endpoint: str, params: dict | None = None) -> dict:
    """GET ilink API with custom base URL — mirrors WeixinChannel._api_get_with_base().

    Ref: nanobot/channels/weixin.py, lines 396-411.
    """
    url = f"{base_url.rstrip('/')}/{endpoint}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=_ilink_headers())
        resp.raise_for_status()
        return resp.json()


# ═══════════════════════════════════════════════════════════════
# State paths (matches nanobot's state storage convention)
# ═══════════════════════════════════════════════════════════════

def _get_instance_home(instance: str = "neo") -> Path:
    data_root = os.environ.get("DATA_ROOT", "/data")
    return Path(data_root) / "instances" / instance / "home" / ".nanobot"


def _get_weixin_state_dir(instance: str = "neo") -> Path:
    d = _get_instance_home(instance) / "weixin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_instance_config(instance: str = "neo") -> Path:
    return _get_instance_home(instance) / "config.json"


def _load_config(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text())
    return {}


# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════

class WechatQRResponse(BaseModel):
    qrcode_id: str
    qrcode_img: str  # base64 QR image (qrcode_img_content from ilink)


class WechatStatusResponse(BaseModel):
    status: str   # "waiting" | "scanned" | "confirmed" | "expired"
    message: str = ""


class BindStatusResponse(BaseModel):
    wechat: dict
    dingtalk: dict


class DingtalkBindBody(BaseModel):
    client_id: str
    client_secret: str
    instance: str = "neo"


# ═══════════════════════════════════════════════════════════════
# WeChat QR login flow
#
# 完全复用 nanobot/channels/weixin.py 的 ilink 接口：
#   - _fetch_qr_code()      → ilink/bot/get_bot_qrcode?bot_type=3
#   - _qr_login() status poll → ilink/bot/get_qrcode_status?qrcode=xxx
#   - 状态: wait → scaned → scaned_but_redirect → confirmed → expired
#   - confirmed 时: bot_token → 写入 account.json
#     (与 WeixinChannel._save_state() 格式兼容)
# ═══════════════════════════════════════════════════════════════

class WechatBinder:
    """微信 QR 登录 — 调用 ilink API（与 nanobot weixin channel 同一接口）"""

    MAX_QR_REFRESH = 3  # matches nanobot/channels/weixin.py MAX_QR_REFRESH_COUNT

    async def fetch_qr(self) -> WechatQRResponse:
        """获取微信登录二维码。

        Mirror: WeixinChannel._fetch_qr_code()
        Source: nanobot/channels/weixin.py, lines 417-426
        """
        try:
            data = await _ilink_get(
                "ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"获取微信二维码失败: {e}")

        qrcode_id = data.get("qrcode", "")
        qrcode_img = data.get("qrcode_img_content", "")

        if not qrcode_id:
            raise HTTPException(status_code=502, detail="微信 API 未返回二维码")

        _pending_bindings[qrcode_id] = {
            "status": "waiting",
            "instance": "neo",
            "token": "",
            "user_id": "",
            "base_url": ILINK_BASE_URL,
            "refresh_count": 0,
        }

        return WechatQRResponse(qrcode_id=qrcode_id, qrcode_img=qrcode_img)

    async def check_status(self, qrcode_id: str) -> WechatStatusResponse:
        """轮询二维码扫描状态。

        Mirror: WeixinChannel._qr_login() 中的 status poll 循环
        Source: nanobot/channels/weixin.py, lines 428-505
        """
        binding = _pending_bindings.get(qrcode_id)
        if not binding:
            return WechatStatusResponse(status="expired", message="二维码已过期，请重新获取")

        poll_base_url = binding.get("base_url", ILINK_BASE_URL)

        try:
            data = await _ilink_get_with_base(
                base_url=poll_base_url,
                endpoint="ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode_id},
            )
        except Exception as e:
            return WechatStatusResponse(status="error", message=str(e))

        if not isinstance(data, dict):
            return WechatStatusResponse(status="waiting", message="等待扫码...")

        status = data.get("status", "wait")

        if status == "confirmed":
            token = data.get("bot_token", "")
            user_id = data.get("ilink_user_id", "")
            base_url = data.get("baseurl", "")  # 官方 _qr_login 也会更新 base_url

            if not token:
                return WechatStatusResponse(status="error", message="微信确认但未返回 token")

            binding["status"] = "confirmed"
            binding["token"] = token
            binding["user_id"] = user_id

            if base_url:
                binding["base_url"] = base_url

            # 写入 account.json（与 WeixinChannel._save_state() 兼容）
            self._save_token(binding["instance"], token, base_url)

            return WechatStatusResponse(status="confirmed", message=f"已绑定: {user_id}")

        elif status == "scaned_but_redirect":
            redirect_host = str(data.get("redirect_host", "") or "").strip()
            if redirect_host:
                if not (redirect_host.startswith("http://") or redirect_host.startswith("https://")):
                    redirect_host = f"https://{redirect_host}"
                if redirect_host != poll_base_url:
                    binding["base_url"] = redirect_host
            binding["status"] = "scanned"
            return WechatStatusResponse(status="scanned", message="已扫码，等待确认...")

        elif status == "expired":
            binding["refresh_count"] = binding.get("refresh_count", 0) + 1
            if binding["refresh_count"] > self.MAX_QR_REFRESH:
                del _pending_bindings[qrcode_id]
                return WechatStatusResponse(status="expired", message="二维码已过期（已超过最大刷新次数）")
            return WechatStatusResponse(status="expired", message="二维码已过期")

        elif status == "scaned":
            binding["status"] = "scanned"
            return WechatStatusResponse(status="scanned", message="已扫码，等待确认...")

        # "wait" or unknown
        return WechatStatusResponse(status="waiting", message="等待扫码...")

    def _save_token(self, instance: str, token: str, base_url: str = "") -> None:
        """保存微信 token 到实例 state 目录。

        State 格式与 WeixinChannel._save_state() (nanobot/channels/weixin.py,
        lines 323-337) 兼容，channel 启动时通过 _load_state() 直接读取。
        """
        state_dir = _get_weixin_state_dir(instance)
        existing = {}
        account_file = state_dir / "account.json"
        if account_file.is_file():
            try:
                existing = json.loads(account_file.read_text())
            except Exception:
                pass

        existing["token"] = token
        if base_url:
            existing["base_url"] = base_url

        account_file.write_text(json.dumps(existing, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# DingTalk credential binding
# ═══════════════════════════════════════════════════════════════

class DingtalkBinder:
    """钉钉凭证绑定 — 写入实例 config.json（与 nanobot dingtalk channel 兼容）"""

    async def bind(self, client_id: str, client_secret: str, instance: str = "neo") -> dict:
        if not client_id or not client_id.strip():
            raise HTTPException(status_code=400, detail="client_id 不能为空")
        if not client_secret or not client_secret.strip():
            raise HTTPException(status_code=400, detail="client_secret 不能为空")

        client_id = client_id.strip()
        client_secret = client_secret.strip()

        # 验证凭证：调钉钉 accessToken API 测试可用性
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                    json={"appKey": client_id, "appSecret": client_secret},
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"钉钉凭证验证失败 ({resp.status_code}): {resp.text[:200]}",
                    )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"无法连接钉钉 API: {e}")

        # 写入实例 config（与 nanobot dingtalk channel 配置格式兼容）
        config_path = _get_instance_config(instance)
        config = _load_config(config_path)

        if "channels" not in config:
            config["channels"] = {}
        config["channels"]["dingtalk"] = {
            "enabled": True,
            "clientId": client_id,
            "clientSecret": client_secret,
            "allowFrom": ["*"],
        }

        tmp = config_path.with_suffix(config_path.suffix + ".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        tmp.rename(config_path)

        return {"ok": True, "message": f"钉钉已绑定到实例 {instance}"}


# ═══════════════════════════════════════════════════════════════
# Router — 挂载到 gatekeeper.py
# ═══════════════════════════════════════════════════════════════

_wechat = WechatBinder()
_dingtalk = DingtalkBinder()

router = APIRouter(prefix="/api/bind", tags=["binding"])


@router.post("/wechat/qr", response_model=WechatQRResponse)
async def bind_wechat_qr(request: Request):
    """获取微信登录二维码。需要已认证的 session。"""
    _check_auth(request)
    instance = request.query_params.get("instance", "neo")
    result = await _wechat.fetch_qr()
    # 绑定 instance 信息到 pending session
    if result.qrcode_id in _pending_bindings:
        _pending_bindings[result.qrcode_id]["instance"] = instance
    return result


@router.get("/wechat/status", response_model=WechatStatusResponse)
async def bind_wechat_status(request: Request, qrcode: str):
    """轮询微信扫码状态。"""
    _check_auth(request)
    return await _wechat.check_status(qrcode)


@router.post("/dingtalk")
async def bind_dingtalk(request: Request, body: DingtalkBindBody):
    """绑定钉钉凭证。"""
    _check_auth(request)
    return await _dingtalk.bind(body.client_id, body.client_secret, body.instance)


@router.get("/status", response_model=BindStatusResponse)
async def bind_status(request: Request, instance: str = "neo"):
    """查询当前实例的绑定状态（供 Neo 检测）。"""
    _check_auth(request)

    wechat_bound = False
    weixin_account = _get_weixin_state_dir(instance) / "account.json"
    if weixin_account.is_file():
        try:
            data = json.loads(weixin_account.read_text())
            wechat_bound = bool(data.get("token"))
        except Exception:
            pass

    dingtalk_bound = False
    config = _load_config(_get_instance_config(instance))
    dt = config.get("channels", {}).get("dingtalk", {})
    dingtalk_bound = dt.get("enabled", False) and bool(dt.get("clientId"))

    return BindStatusResponse(
        wechat={"bound": wechat_bound},
        dingtalk={"bound": dingtalk_bound},
    )


def _check_auth(request: Request) -> None:
    """验证 session 已认证。"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="请先登录平台账号")

    # 平台差异: HF 存 sub/login, MS 存 username
    any_id = user.get("sub") or user.get("login") or user.get("username") or user.get("id")
    if not any_id:
        raise HTTPException(status_code=401, detail="无法识别用户身份")
