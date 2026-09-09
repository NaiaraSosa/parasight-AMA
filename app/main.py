# entrada FASTAPI

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.core.config import ensure_dirs

app = FastAPI(title="Parasight")
templates = Jinja2Templates(directory="app/templates")

app.include_router(api_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def _startup():
    ensure_dirs()


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")
