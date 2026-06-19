# Queues Digest

Очереди, которые слушает `event-booking` (по `event_booking/consumer.py` и `event_schemas.queues`).

## Сводная таблица

| Queue | Routing key | Источник событий | Events |
|---|---|---|---|
| `events.booking.lifecycle.booking` | `events.booking.lifecycle` | event-receiver | lifecycle бронирования (fan-out вместе с event-saver) |
| `events.user.email.booking` | `events.user.email` | event-receiver | `user.email.change_requested` — проброс смены email в cal.com Attendee (fan-out вместе с event-users) |

Каждая очередь объявляется из `event_schemas.queues.QueueSpec` при старте сервиса.
Стандартные аргументы: `x-max-priority=10`, `x-dead-letter-exchange=events.dlx`,
`x-dead-letter-routing-key=<queue>.dlq`. Сопутствующая DLQ (`x-message-ttl=86400000`)
объявляется там же (24h TTL).

---

## events.booking.lifecycle.booking

Fan-out-копия очереди `events.booking.lifecycle` (вместе с `events.booking.lifecycle.saver`
→ event-saver). Все сообщения поступают в CloudEvents binary mode,
обёртке `{original, normalized}`.

### Обрабатываемые события

| Event type | Поведение |
|---|---|
| `booking.created` | Blacklist-check → optional constraints → chat + welcomes → per-participant Jitsi URLs → `metadata.videoCallUrl` → notifications |
| `booking.rescheduled` | Удалить старый чат (`previous_booking_uid`), пересоздать, переместить short URLs, уведомить |
| `booking.reassigned` | Hard-delete канала, пересоздать, регенерировать URLs, уведомить |
| `booking.cancelled` | Уведомить, удалить чат и оба short URL |
| `booking.rejected` | Записать отказ в cal.com (`status='rejected'`, `rejectionReason`), уведомить |
| `booking.reminder_sent` | Зарезервировано (продюсера нет); обрабатывается как no-op |

---

## events.user.email.booking

**Новая очередь** (fan-out с `events.user.email` → event-users на том же routing key).
Добавлена для проброса смены email клиента из admin-панели в cal.com `"Attendee"`.

### Обрабатываемые события

| Event type | Поведение |
|---|---|
| `user.email.change_requested` | Если `booking_uid` задан: находит `Booking` по uid → id, обновляет `Attendee.email` по совпадению `lower(old_email)` в транзакции с `SET LOCAL app.sync_suppress='on'`. Если `booking_uid` отсутствует — событие игнорируется. |

### Детали реализации

- **Payload**: `UserEmailChangeRequestedPayload` из `event_schemas.user`; поле `booking_uid: str | null` — опциональное.
- **Подавление триггера**: `SET LOCAL app.sync_suppress='on'` выставляется в начале транзакции. Триггерная функция на `"Attendee"` проверяет GUC `app.sync_suppress` перед вызовом `pg_notify('user_sync', ...)`, что предотвращает зацикливание синхронизации через `event-db-sync → user.upserted → event-users → ...`.
- **Маршрут по cal.com**: `Booking.uid → Booking.id → Attendee.bookingId`, фильтр `lower("Attendee".email) = lower(old_email)`.
- **Идемпотентность**: повторная обработка одного события не вызывает ошибки (UPDATE с тем же значением — безопасен).
- **Источник истины**: `event_schemas.queues.ALL_QUEUES` / `ROUTING_RULES`.

### Fan-out схема

```
routing key: events.user.email
        │
        ├──► events.user.email         → event-users  (UPDATE users.email; booking_uid игнорируется)
        └──► events.user.email.booking → event-booking (UPDATE Attendee.email, если booking_uid задан)
```

---

Производимые события (follow-up, публикуются через `POST /event/booking` → event-receiver):
см. `docs/API_CONTRACTS.md`.
