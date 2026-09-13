"""Object-level permission grants without extra dependencies (a django-guardian alternative).

Add ``"ninja_devx.contrib.grants"`` to ``INSTALLED_APPS`` and
``"ninja_devx.contrib.grants.backends.GrantBackend"`` to ``AUTHENTICATION_BACKENDS``, so
``user.has_perm("blog.change_post", post)`` works everywhere (admin, templates, DRF too).
"""
