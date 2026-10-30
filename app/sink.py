import logging

from fastapi import FastAPI, Request

app = FastAPI()
log = logging.getLogger("uvicorn.error")


@app.post("/hook")
async def hook(req: Request):
    log.info("WEBHOOK %s", await req.json())
    return {"ok": True}
