import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import API_KEY, Session, now
from app.models import Outbox, Payment
from app.mq import DLQ, NEW, broker

log = logging.getLogger("api")


def auth(x_api_key: str = Header(alias="X-API-Key", default="")):
    if x_api_key != API_KEY:
        raise HTTPException(401, "invalid api key")


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["RUB", "USD", "EUR"]
    description: str
    metadata: dict = {}
    webhook_url: AnyHttpUrl


class Accepted(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    payment_id: uuid.UUID = Field(validation_alias="id")
    status: str
    created_at: datetime


class PaymentOut(Accepted):
    amount: Decimal
    currency: str
    description: str
    metadata: dict = Field(validation_alias="meta")
    idempotency_key: str
    webhook_url: str
    processed_at: datetime | None


router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.post("/payments", status_code=202, response_model=Accepted)
async def create_payment(body: PaymentIn, idempotency_key: str = Header(alias="Idempotency-Key")):
    pid = uuid.uuid4()
    async with Session() as s:
        s.add(Payment(
            id=pid, amount=body.amount, currency=body.currency, description=body.description,
            meta=body.metadata, idempotency_key=idempotency_key, webhook_url=str(body.webhook_url),
        ))
        s.add(Outbox(payload={"payment_id": str(pid)}))  # та же транзакция, что и платёж
        try:
            await s.commit()
        except IntegrityError:  # дубль Idempotency-Key -> отдаём уже созданный платёж
            await s.rollback()
            return (await s.execute(select(Payment).where(Payment.idempotency_key == idempotency_key))).scalar_one()
        return await s.get(Payment, pid)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: uuid.UUID):
    async with Session() as s:
        p = await s.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "not found")
    return p


async def outbox_relay():
    """Публикует неотправленные события из outbox (at-least-once)."""
    while True:
        try:
            await broker.declare_queue(DLQ)
            await broker.declare_queue(NEW)
            async with Session() as s, s.begin():
                rows = (await s.execute(
                    select(Outbox).where(Outbox.published_at.is_(None))
                    .order_by(Outbox.id).limit(100).with_for_update(skip_locked=True)
                )).scalars().all()
                for r in rows:
                    await broker.publish(r.payload, queue=NEW)
                    r.published_at = now()
        except Exception:
            log.exception("outbox relay error")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_):
    await broker.start()
    task = asyncio.create_task(outbox_relay())
    yield
    task.cancel()
    await broker.close()


app = FastAPI(title="Payments", lifespan=lifespan)
app.include_router(router)
