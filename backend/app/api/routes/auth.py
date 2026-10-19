from fastapi import APIRouter, Response, HTTPException, status, Depends
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.api.deps import get_current_admin

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(login_req: LoginRequest, response: Response):
    """Authenticate single-admin and set httpOnly cookie."""
    if (
        login_req.username != settings.ADMIN_USERNAME
        or login_req.password != settings.ADMIN_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_access_token(data={"sub": login_req.username})

    # Set httpOnly cookie for seamless browser auth
    response.set_cookie(
        key="loopforge_access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True in production HTTPS
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/logout")
def logout(response: Response):
    """Log out admin by clearing the httpOnly cookie."""
    response.delete_cookie(key="loopforge_access_token", path="/")
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
def get_me(username: str = Depends(get_current_admin)):
    """Return currently authenticated user."""
    return UserResponse(username=username, is_authenticated=True)
