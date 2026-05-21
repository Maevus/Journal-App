from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel

load_dotenv()

# Import models so SQLModel registers their tables before create_all
import models.journal  # noqa: F401, E402
from models.journal import engine
from routers import ingest, search, synthesis, trends


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(title="Journal Analytics API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(synthesis.router, prefix="/stream", tags=["synthesis"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(trends.router, prefix="/trends", tags=["trends"])
