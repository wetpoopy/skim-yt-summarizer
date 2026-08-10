"""
Email + password auth. Session is a JWT stored in an httpOnly cookie
(not localStorage, so it isn't reachable from page JS/XSS).
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

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

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long.")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    email: str


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


def _set_session_cookie(response: Response, user_id: int) -> None:
    token = _issue_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="lax",
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Optional auth: returns the User if a valid session cookie is present, else None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not JWT_SECRET:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return db.get(User, int(user_id))


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Required auth: raises 401 if not logged in."""
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


@router.post("/signup", response_model=UserOut)
def signup(body: SignupRequest, response: Response, db: Session = Depends(get_db)):
    _require_jwt_secret()
    existing = db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    user = User(email=body.email, password_hash=_hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    _set_session_cookie(response, user.id)
    return UserOut(email=user.email)


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    _require_jwt_secret()
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    _set_session_cookie(response, user.id)
    return UserOut(email=user.email)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=UserOut | None)
def me(user: User | None = Depends(get_current_user)):
    return UserOut(email=user.email) if user else None
