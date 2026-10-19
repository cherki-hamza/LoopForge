import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.db.session import init_db
from app.api.routes.auth import router as auth_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.system import router as system_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("loopforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing LoopForge application...")
    # Initialize storage directories
    _ = settings.uploads_dir
    _ = settings.outputs_dir
    _ = settings.temp_dir
    
    # Initialize database tables
    try:
        init_db()
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.warning(f"Database initialization deferred (DB may be starting up): {e}")

    yield
    logger.info("Shutting down LoopForge application...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="LoopForge - Video looping engine for long seamless meditation and ambient videos",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS if isinstance(settings.CORS_ORIGINS, list) else [settings.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Disposition", "Content-Length"],
)

# Include API routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(jobs_router, prefix=settings.API_V1_PREFIX)
app.include_router(system_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root():
    return {
        "service": settings.PROJECT_NAME,
        "status": "online",
        "docs": "/docs",
        "api": settings.API_V1_PREFIX,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}

