# Async Payments Service

FastAPI + SQLAlchemy 2 (async) + PostgreSQL + RabbitMQ (FastStream) + Alembic.

## Запуск
    docker compose up --build

API: http://localhost:8000/docs · RabbitMQ UI: http://localhost:15672 (guest/guest)
Тестовый приёмник webhook встроен в compose (`webhook`, `http://webhook:9000/hook`).

## Проверка
    # 1. создать платёж (202)
    curl -i -X POST localhost:8000/api/v1/payments \
      -H 'X-API-Key: secret' -H 'Idempotency-Key: k1' -H 'Content-Type: application/json' \
      -d '{"amount":"100.50","currency":"RUB","description":"test","metadata":{"order":1},"webhook_url":"http://webhook:9000/hook"}'

    # 2. тот же запрос с тем же Idempotency-Key -> тот же payment_id, дубля нет

    # 3. статус (pending -> succeeded/failed через 2-5 сек)
    curl -H 'X-API-Key: secret' localhost:8000/api/v1/payments/<payment_id>

    # 4. webhook пришёл
    docker compose logs webhook | grep WEBHOOK

    # 5. без ключа -> 401
    curl -i localhost:8000/api/v1/payments/00000000-0000-0000-0000-000000000000

    # 6. retry + DLQ: недоступный webhook
    curl -X POST localhost:8000/api/v1/payments -H 'X-API-Key: secret' -H 'Idempotency-Key: k2' \
      -H 'Content-Type: application/json' \
      -d '{"amount":"1","currency":"USD","description":"dlq","webhook_url":"http://webhook:1/x"}'
    docker compose logs -f consumer      # 3 попытки (паузы 2с, 4с), затем "sent to DLQ"
    # в RabbitMQ UI -> Queues -> payments.dlq: 1 сообщение

## Как устроено
- API в одной транзакции пишет `payments` + `outbox`; фоновый relay публикует outbox в `payments.new` (at-least-once).
- Consumer: эмуляция 2-5 сек (90% успех) -> статус в БД -> webhook. Всё обёрнуто в 3 попытки с задержкой 2^n сек.
- После 3 неудач сообщение уходит в `payments.dlq` (у `payments.new` также настроен dead-letter на неё).
- Повторная доставка безопасна: уже обработанный платёж не пересчитывается, только повторно шлётся webhook.
