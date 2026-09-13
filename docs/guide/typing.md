# Typing

- The package passes mypy strict and pyright strict, and a test forbids `typing.Any` in its
  source. Opaque values are `object`.
- Operation decorators keep the method's type and require `(self, request)`.
- Options are `TypedDict`s, so a typo like `summry=` is a type error.
- Container keys are `Callable[..., T]`, so abstract classes and protocols need no ignores.
- `Instance[Post]` is `Post` for type checkers; `AuthedRequest[User].auth` is `User`.
- `tests/typing/check_public_api.py` holds positive and negative checks: an unused
  `# type: ignore` fails CI.

For model field types in your own code, enable the django-stubs mypy plugin (see
`examples/blog/mypy.ini`).
