import asyncio
import logging
import random
import uuid

import httpx
from faststream import FastStream

from app.core import Session, now
from app.models import Payment
from app.mq import DLQ, NEW, broker

log = logging.getLogger("consumer")
app = FastStream(broker)


@app.after_startup
async def _declare_dlq():
    await broker.declare_queue(DLQ)


async def process(payment_id: str):
    async with Session() as s:
        p = await s.get(Payment, uuid.UUID(payment_id))
        if p is None:
            raise ValueError(f"payment {payment_id} not found")
        if p.status == "pending":  # идемпотентность при повторной доставке
            await asyncio.sleep(random.uniform(2, 5))
            p.status = "succeeded" if random.random() < 0.9 else "failed"
            p.processed_at = now()
            await s.commit()
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(p.webhook_url, json={
            "payment_id": str(p.id), "status": p.status, "amount": str(p.amount),
            "currency": p.currency, "processed_at": p.processed_at.isoformat(),
        })
        r.raise_for_status()


@broker.subscriber(NEW)
async def handle(body: dict):
    for attempt in range(1, 4):
        try:
            return await process(body["payment_id"])
        except Exception as e:
            log.warning("attempt %d/3 failed: %r", attempt, e)
            if attempt < 3:
                await asyncio.sleep(2 ** attempt)  # 2с, 4с
    await broker.publish(body, queue="payments.dlq")
    log.error("sent to DLQ: %s", body)
