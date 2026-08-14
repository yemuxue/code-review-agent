"""
JWT Authentication — 真正的 JWT 认证（签名 + 过期 + 刷新）

面试话术：
  "用 python-jose 实现 HS256 签名 JWT，Access Token 15min 短期 +
   Refresh Token 7d 长期，双 token 轮换机制防止长期泄露。
   用户密码用 bcrypt 哈希存储，验证时使用 constant-time 比较。"

用法:
    auth = JWTAuth(secret_key="...")
    token = auth.create_access_token({"sub": "admin", "role": "admin"})
    payload = auth.verify_token(token)  # 验证签名 + 过期，失败抛异常
"""

from __future__ import annotations
import os
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

import bcrypt as _bcrypt
from jose import jwt, JWTError, ExpiredSignatureError


# ─── Token 配置 ────────────────────────────────

ACCESS_TOKEN_EXPIRE_MINUTES = 15      # 短期 access token
REFRESH_TOKEN_EXPIRE_DAYS = 7         # 长期 refresh token
ALGORITHM = "HS256"

# 仅用于检测开发者显式传入的"默认值"——生产环境禁止使用
_DEFAULT_SECRET = "code-review-agent-secret-change-in-production"


def _get_secret_key() -> str:
    """从环境变量或文件读取 JWT 签名密钥；缺失时立即抛错（fail fast）"""
    key = os.getenv("JWT_SECRET_KEY", "")
    if key:
        return key
    # 尝试从文件读取
    key_file = os.getenv("JWT_SECRET_FILE", "")
    if key_file and Path(key_file).exists():
        return Path(key_file).read_text().strip()
    # 不再回退到可预测的默认密钥：密钥缺失时应立即失败而不是带病运行
    raise RuntimeError(
        "JWT_SECRET_KEY (or JWT_SECRET_FILE) is not set. "
        "Refusing to sign tokens with a predictable default secret. "
        "Set a strong random secret via the JWT_SECRET_KEY environment variable."
    )


# ─── 用户模型 ──────────────────────────────────

class User:
    """认证用户模型"""

    def __init__(self, username: str, role: str = "user", email: str = ""):
        self.username = username
        self.role = role
        self.email = email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return {"username": self.username, "role": self.role, "email": self.email}

    def to_jwt_payload(self) -> dict:
        """转换为 JWT payload（不包含敏感信息）"""
        return {
            "sub": self.username,
            "role": self.role,
            "email": self.email,
        }


# ─── 用户存储（内存 + 文件持久化）─────────────

class UserStore:
    """
    用户存储。开发环境用内存 + JSON 文件持久化。
    生产环境替换为数据库（PostgreSQL / MySQL）。

    面试话术："当前用 bcrypt + 文件持久化做原型验证，
    生产环境切换到数据库，UserStore 接口不变——依赖倒置原则。"
    """

    def __init__(self, data_dir: str = "./data"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._users_file = self._data_dir / "users.json"
        self._users: dict[str, dict] = {}
        self._load()
        # 确保默认 admin 用户存在
        self._ensure_default_admin()

    def _load(self):
        """从 JSON 文件加载用户"""
        if self._users_file.exists():
            try:
                self._users = json.loads(self._users_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._users = {}

    def _save(self):
        """持久化到 JSON 文件"""
        self._users_file.write_text(json.dumps(self._users, indent=2))

    def _ensure_default_admin(self):
        """确保默认管理员存在"""
        if "admin" not in self._users:
            password = os.getenv("ADMIN_PASSWORD")
            if not password:
                raise RuntimeError(
                    "ADMIN_PASSWORD is required when initializing the first administrator."
                )
            self.create_user(
                username="admin",
                password=password,
                role="admin",
                email="admin@code-review.local",
            )
            print("[JWT Auth] Created administrator from ADMIN_PASSWORD")

    def create_user(self, username: str, password: str,
                    role: str = "user", email: str = "") -> User:
        """创建用户（密码用 bcrypt 哈希存储）"""
        if username in self._users:
            raise ValueError(f"User '{username}' already exists")
        self._users[username] = {
            "username": username,
            "password_hash": _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode(),
            "role": role,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        return User(username=username, role=role, email=email)

    def get_user(self, username: str) -> Optional[User]:
        """按用户名查找"""
        data = self._users.get(username)
        if not data:
            return None
        return User(username=data["username"], role=data["role"], email=data["email"])

    def verify_password(self, username: str, password: str) -> Optional[User]:
        """
        验证密码。使用 bcrypt.verify()（内部用 constant-time 比较防止时序攻击）。

        面试话术："密码验证用 bcrypt.verify()，内部是 constant-time 比较，
        防止时序侧信道攻击——即使攻击者测量响应时间也无法推断密码。"
        """
        data = self._users.get(username)
        if not data:
            return None
        if not _bcrypt.checkpw(password.encode(), data["password_hash"].encode()):
            return None
        return User(username=data["username"], role=data["role"], email=data["email"])

    def list_users(self) -> list[dict]:
        """列出所有用户（不含密码哈希）"""
        return [
            {"username": u["username"], "role": u["role"],
             "email": u["email"], "created_at": u.get("created_at", "")}
            for u in self._users.values()
        ]


# ─── JWT 核心 ──────────────────────────────────

class JWTAuth:
    """
    JWT 认证核心：签发 + 验证 + 刷新。

    token_type:
      - "access": 短期，用于 API 鉴权（默认 15 min）
      - "refresh": 长期，仅用于刷新 access token（7 days）

    面试话术：
      "双 token 设计：access token 15 分钟短期，即使泄露窗口也很小；
       refresh token 7 天长期，只发往 /auth/refresh 单一端点，
       降低暴露面。服务端可随时吊销 refresh token 来强制用户重新登录。"
    """

    def __init__(self, secret_key: str | None = None):
        if secret_key is None:
            secret_key = _get_secret_key()
        # fail fast：拒绝使用可预测的默认密钥，而不是仅仅告警
        if secret_key == _DEFAULT_SECRET:
            raise RuntimeError(
                "Refusing to use the default JWT secret key "
                "'code-review-agent-secret-change-in-production'. "
                "Set JWT_SECRET_KEY to a strong random value before starting."
            )
        self.secret_key = secret_key

    def create_access_token(self, user: User,
                            expires_delta: timedelta | None = None) -> str:
        """签发短期 access token（默认 15 min）"""
        return self._create_token(user, token_type="access", expires_delta=expires_delta)

    def create_refresh_token(self, user: User,
                             expires_delta: timedelta | None = None) -> str:
        """签发长期 refresh token（默认 7 days）"""
        if expires_delta is None:
            expires_delta = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        return self._create_token(user, token_type="refresh", expires_delta=expires_delta)

    def create_tokens(self, user: User) -> dict:
        """一次签发 access + refresh 对"""
        return {
            "access_token": self.create_access_token(user),
            "refresh_token": self.create_refresh_token(user),
            "token_type": "bearer",
        }

    def verify_token(self, token: str, token_type: str = "access") -> dict:
        """
        验证 JWT 签名 + 过期时间 + token_type + 吊销状态。

        Raises:
            ExpiredSignatureError: token 已过期
            JWTError: 签名无效 / 篡改 / 已被吊销
            ValueError: token_type 不匹配

        面试话术："验证分四层：① jose.jwt.decode 验证 HMAC-SHA256 签名
        ——任何篡改都会导致签名不匹配被拒绝；② 检查 exp 字段是否过期；
        ③ 检查 token_type 防止 refresh token 被当 access token 用；
        ④ 检查 jti 是否在吊销列表中——已吊销的 refresh token 立即被拒绝。"
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[ALGORITHM],
                options={"verify_exp": True},
            )
        except ExpiredSignatureError:
            raise  # 重新抛出让调用方处理
        except JWTError as e:
            raise JWTError(f"Token verification failed: {e}")

        # 验证 token_type
        if payload.get("type") != token_type:
            raise ValueError(
                f"Wrong token type: expected '{token_type}', got '{payload.get('type')}'"
            )

        # 检查吊销列表：已吊销的 token（含 refresh token）直接拒绝
        jti = payload.get("jti")
        if jti and get_revocation_list().is_revoked(jti):
            raise JWTError(f"Token has been revoked (jti={jti})")

        return payload

    def refresh_access_token(self, refresh_token: str) -> dict:
        """
        用 refresh token 换取新的 access token，并轮换 refresh token
        （旧 refresh token 立即吊销，防止被盗后无限次复用）。

        面试话术："refresh token 只能换新 access token，不能访问业务 API。
        每次刷新都会吊销旧 refresh token 并签发新 refresh token（token rotation）。
        如果攻击者重放旧 refresh token，吊销列表会让验证失败，从而发现泄露。"
        """
        payload = self.verify_token(refresh_token, token_type="refresh")
        # 轮换：立即吊销本次使用的 refresh token
        get_revocation_list().revoke_token(payload)
        user = User(
            username=payload["sub"],
            role=payload.get("role", "user"),
            email=payload.get("email", ""),
        )
        return self.create_tokens(user)

    def _create_token(self, user: User, token_type: str,
                      expires_delta: timedelta | None = None) -> str:
        """内部：签发 JWT"""
        now = datetime.now(timezone.utc)
        if expires_delta is None:
            expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

        payload = user.to_jwt_payload()
        payload.update({
            "type": token_type,
            "iat": now,
            "exp": now + expires_delta,
            "jti": _generate_jti(),  # 唯一 token ID，用于吊销
        })
        return jwt.encode(payload, self.secret_key, algorithm=ALGORITHM)


def _generate_jti() -> str:
    """生成唯一 token ID（用于吊销列表）"""
    import secrets
    return secrets.token_hex(16)


# ─── Token 吊销列表（过期感知）────────────────

class TokenRevocationList:
    """
    过期感知的内存吊销列表（TTL 对齐 token 过期时间，到期自动清理）。

    面试话术："吊销列表用带 TTL 的存储实现，key 是 jti，
    TTL 对齐 token 过期时间——token 过期后自动清理，不占内存、不拖慢查询。
    验证时 O(1) 检查 jti 是否在集合中。生产环境可平滑替换为 Redis。"
    """

    # 距上次清理超过该秒数，或条目数超过阈值时触发清理
    _PURGE_INTERVAL_SECONDS = 60
    _PURGE_TRIGGER_SIZE = 1000

    def __init__(self):
        self._revoked: dict[str, float] = {}  # jti -> 过期时间戳(epoch)
        self._last_purge = time.time()

    def revoke(self, jti: str, ttl_seconds: float | None = None):
        """吊销指定 token；ttl 默认对齐 refresh token 生命周期，到期自动清理"""
        self._maybe_purge()
        if ttl_seconds is None:
            ttl_seconds = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        self._revoked[jti] = time.time() + max(0.0, ttl_seconds)

    def revoke_token(self, payload: dict) -> bool:
        """按 token payload 吊销，TTL 对齐该 token 剩余有效期"""
        jti = payload.get("jti")
        if not jti:
            return False
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            ttl = float(exp) - time.time()
        elif isinstance(exp, datetime):
            ttl = (exp - datetime.now(timezone.utc)).total_seconds()
        else:
            ttl = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        self.revoke(jti, ttl_seconds=ttl)
        return True

    def is_revoked(self, jti: str) -> bool:
        """检查 token 是否已被吊销（同时惰性清理过期条目）"""
        self._maybe_purge()
        exp = self._revoked.get(jti)
        return exp is not None and exp > time.time()

    def count(self) -> int:
        self._maybe_purge()
        return len(self._revoked)

    def purge_expired(self) -> int:
        """主动清理所有过期条目，返回清理数量"""
        before = len(self._revoked)
        self._last_purge = 0.0  # 强制触发清理
        self._maybe_purge()
        return before - len(self._revoked)

    def _maybe_purge(self):
        """按时间/规模触发清理，防止被吊销条目无界累积（内存泄漏）"""
        now = time.time()
        if (now - self._last_purge < self._PURGE_INTERVAL_SECONDS
                and len(self._revoked) < self._PURGE_TRIGGER_SIZE):
            return
        stale = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in stale:
            del self._revoked[jti]
        self._last_purge = now


# ─── 全局单例 ──────────────────────────────────

_auth_instance: Optional[JWTAuth] = None
_user_store_instance: Optional[UserStore] = None
_revocation_list_instance: Optional[TokenRevocationList] = None


def get_auth() -> JWTAuth:
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = JWTAuth()
    return _auth_instance


def get_user_store() -> UserStore:
    global _user_store_instance
    if _user_store_instance is None:
        _user_store_instance = UserStore()
    return _user_store_instance


def get_revocation_list() -> TokenRevocationList:
    global _revocation_list_instance
    if _revocation_list_instance is None:
        _revocation_list_instance = TokenRevocationList()
    return _revocation_list_instance
