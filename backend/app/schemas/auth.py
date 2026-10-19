from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    username: str
    is_authenticated: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
