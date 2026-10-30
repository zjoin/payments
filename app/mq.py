from faststream.rabbit import RabbitBroker, RabbitQueue

from app.core import RABBIT_URL

broker = RabbitBroker(RABBIT_URL)

DLQ = RabbitQueue("payments.dlq", durable=True)
NEW = RabbitQueue(
    "payments.new",
    durable=True,
    arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "payments.dlq"},
)
