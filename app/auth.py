"""
Email + password auth. Session is a JWT stored in an httpOnly cookie
(not localStorage, so it isn't reachable from page JS/XSS).
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiToken, PasswordResetCode, Summary, User
from app.password_reset import EmailSendError, send_reset_code
from app.ratelimit import check_and_record

RESET_CODE_TTL_MINUTES = 15
RESET_REQUEST_LIMIT_PER_DAY = 5

TOKEN_PREFIX = "skim_"

JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGORITHM = "HS256"
SESSION_COOKIE = "session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Railway sets this in every deployed environment; absent locally.
IS_PRODUCTION = bool(os.environ.get("RAILWAY_ENVIRONMENT"))

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_jwt_secret() -> str:
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="Server is missing JWT_SECRET.")
    return JWT_SECRET


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long.")
        return v

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v[:255] or None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    email: str
    full_name: str | None = None


class MobileAuthOut(BaseModel):
    email: str
    full_name: str | None = None
    token: str


class CreateTokenRequest(BaseModel):
    label: str = ""


class TokenCreatedOut(BaseModel):
    id: int
    token: str
    label: str
    created_at: datetime


class TokenOut(BaseModel):
    id: int
    label: str
    created_at: datetime
    last_used_at: datetime | None


class PreferencesOut(BaseModel):
    summary_length: Literal["brief", "standard", "detailed"]
    summary_format: Literal["bullets", "prose", "mixed"]
    ai_provider: Literal["anthropic", "openai", "gemini"]
    digest_email_enabled: bool


class PreferencesUpdate(BaseModel):
    summary_length: Literal["brief", "standard", "detailed"]
    summary_format: Literal["bullets", "prose", "mixed"]
    ai_provider: Literal["anthropic", "openai", "gemini"]
    digest_email_enabled: bool


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v[:255] or None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long.")
        return v


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long.")
        return v


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _issue_token(user_id: int) -> str:
    secret = _require_jwt_secret()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_from_bearer_token(request: Request, db: Session) -> User | None:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    raw_token = header[len("bearer "):].strip()
    if not raw_token:
        return None

    # The mobile app has no cookie jar, so it sends the same session JWT
    # normally kept in the httpOnly cookie via this header instead. Try
    # that first (cheap, no DB hit), then fall back to a personal API
    # token (skim_... prefixed, used by Shortcuts/scripts).
    if JWT_SECRET:
        try:
            payload = jwt.decode(raw_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                user = db.get(User, int(user_id))
                if user:
                    return user
        except jwt.PyJWTError:
            pass

    api_token = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash_token(raw_token)))
    if not api_token:
        return None

    api_token.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return db.get(User, api_token.user_id)


def _set_session_cookie(response: Response, user_id: int) -> str:
    """
    Sets the httpOnly cookie session used by the web frontend, and returns
    the raw token too — the mobile app has no cookie jar, so it stores this
    value itself and sends it back as `Authorization: Bearer <token>`.
    """
    token = _issue_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="lax",
    )
    return token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """
    Optional auth: resolves to a User via session cookie (browser) or a
    personal API token in the Authorization header (e.g. iOS Shortcuts),
    else None.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token and JWT_SECRET:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                user = db.get(User, int(user_id))
                if user:
                    return user
        except jwt.PyJWTError:
            pass

    return _user_from_bearer_token(request, db)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Required auth: raises 401 if not logged in."""
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


@router.post("/signup", response_model=MobileAuthOut)
def signup(body: SignupRequest, response: Response, db: Session = Depends(get_db)):
    _require_jwt_secret()
    existing = db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    user = User(email=body.email, password_hash=_hash_password(body.password), full_name=body.full_name)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = _set_session_cookie(response, user.id)
    return MobileAuthOut(email=user.email, full_name=user.full_name, token=token)


@router.post("/login", response_model=MobileAuthOut)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    _require_jwt_secret()
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = _set_session_cookie(response, user.id)
    return MobileAuthOut(email=user.email, full_name=user.full_name, token=token)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=UserOut | None)
def me(user: User | None = Depends(get_current_user)):
    return UserOut(email=user.email, full_name=user.full_name) if user else None


@router.patch("/me", response_model=UserOut)
def update_profile(
    body: UpdateProfileRequest, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    if body.email is not None and body.email != user.email:
        existing = db.scalar(select(User).where(User.email == body.email))
        if existing:
            raise HTTPException(status_code=409, detail="An account with that email already exists.")
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name
    db.commit()
    return UserOut(email=user.email, full_name=user.full_name)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    if not _verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    user.password_hash = _hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.delete("/account")
def delete_account(response: Response, user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.execute(delete(Summary).where(Summary.user_id == user.id))
    db.execute(delete(ApiToken).where(ApiToken.user_id == user.id))
    db.delete(user)
    db.commit()

    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.post("/tokens", response_model=TokenCreatedOut)
def create_token(body: CreateTokenRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user.id, token_hash=_hash_token(raw_token), label=body.label.strip()[:100])
    db.add(row)
    db.commit()
    db.refresh(row)

    return TokenCreatedOut(id=row.id, token=raw_token, label=row.label, created_at=row.created_at)


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()
    return [
        TokenOut(id=r.id, label=r.label, created_at=r.created_at, last_used_at=r.last_used_at)
        for r in rows
    ]


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Token not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/preferences", response_model=PreferencesOut)
def get_preferences(user: User = Depends(require_user)):
    return PreferencesOut(
        summary_length=user.summary_length or "standard",
        summary_format=user.summary_format or "mixed",
        ai_provider=user.ai_provider or "anthropic",
        digest_email_enabled=user.digest_email_enabled is not False,
    )


@router.put("/preferences", response_model=PreferencesOut)
def update_preferences(
    body: PreferencesUpdate, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    user.summary_length = body.summary_length
    user.summary_format = body.summary_format
    user.ai_provider = body.ai_provider
    user.digest_email_enabled = body.digest_email_enabled
    db.commit()
    return PreferencesOut(
        summary_length=user.summary_length,
        summary_format=user.summary_format,
        ai_provider=user.ai_provider,
        digest_email_enabled=user.digest_email_enabled is not False,
    )


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    generic_response = {
        "message": "If that email has an account, we've sent a password reset code."
    }

    allowed, _ = check_and_record(f"reset:{body.email}", RESET_REQUEST_LIMIT_PER_DAY)
    if not allowed:
        # Still generic — don't reveal whether the email exists via a different error.
        return generic_response

    user = db.scalar(select(User).where(User.email == body.email))
    if not user:
        return generic_response

    code = f"{secrets.randbelow(1_000_000):06d}"
    reset_row = PasswordResetCode(
        user_id=user.id,
        code_hash=_hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_CODE_TTL_MINUTES),
    )
    db.add(reset_row)
    db.commit()

    try:
        send_reset_code(user.email, code)
    except EmailSendError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return generic_response


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    code_hash = _hash_code(body.code)
    now = datetime.now(timezone.utc)
    reset_row = db.scalar(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.code_hash == code_hash,
            PasswordResetCode.used == False,  # noqa: E712
            PasswordResetCode.expires_at > now,
        )
        .order_by(PasswordResetCode.created_at.desc())
    )
    if not reset_row:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    user.password_hash = _hash_password(body.new_password)
    reset_row.used = True
    db.commit()
    return {"ok": True}
