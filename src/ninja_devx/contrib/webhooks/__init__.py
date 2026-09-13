"""Transactional outbox and signed webhooks.

``INSTALLED_APPS += ["ninja_devx.contrib.webhooks"]``, then::

    with transaction.atomic():
        order = Order.objects.create(...)
        publish("order.created", {"id": order.pk, "total": str(order.total)}, owner=order.owner)

    # a worker (cron, systemd, a container) sends what was committed:
    #   manage.py devx_webhooks deliver --loop

    api.add_router("/webhooks", WebhookEndpointController.as_router())

Events are rows written in the caller's transaction, so a rolled back change never
notifies anyone and a committed one is never lost. Requests follow the Standard Webhooks
format (``webhook-id``, ``webhook-timestamp``, ``webhook-signature: v1,<base64>``);
receivers check them with ``verify_signature``. Failed deliveries are retried with
backoff and endpoints failing for too long are disabled.
"""
