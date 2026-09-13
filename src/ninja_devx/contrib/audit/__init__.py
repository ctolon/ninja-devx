"""Audit log: who changed what, when, and from which request.

``INSTALLED_APPS += ["ninja_devx.contrib.audit"]``, then::

    class InvoiceController(AuditMixin[Invoice], CRUDController[Invoice, InvoiceOut, InvoiceIn]):
        audit_redact = ("iban",)

    api.add_router("/audit", AuditLogController.as_router())   # staff-only, read-only

Creates, updates (with field-level diffs) and deletes made through the controller,
including bulk operations, are written in the same transaction as the change. Record
anything else with ``record(request, "export", obj, metadata={...})``.
"""
