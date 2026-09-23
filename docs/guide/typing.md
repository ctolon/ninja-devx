# Typing

The package is typed end to end: mypy strict and pyright strict pass, and a test forbids
`typing.Any` in the source. Opaque values are `object`, so nothing is silently untyped.

## What you get

- **Options are `TypedDict`s.** A typo like `summry=` is a type error, not a silent no-op.
  `OperationOptions`, `ControllerOptions` and `RouteOptions` are the three layers.
- **Generic arguments are the configuration.** `CRUDController[Post, PostOut, PostIn]` is
  checked, and `Instance[Post]` is `Post` at type-check time.
- **Typed users.** `AuthedRequest[User]` has `.auth: User`; `current_user(request, User)`
  returns `User`. `request.auth` without the helper is `object`.
- **Container keys are `Callable[..., T]`.** Abstract classes and protocols work without
  `# type: ignore`; implementations are checked against the port.
- **Injection is transparent.** `Inject[T]` and `Annotated[T, Resolve(fn)]` expose the
  resolved type to the operation signature.
- **Negative checks are enforced.** `tests/typing/check_public_api.py` has positive and
  negative cases; an unused `# type: ignore` fails the suite.

## Check your project

```bash
uv run mypy --strict your_app
uv run pyright your_app
```

For model field types in your own code, enable the django-stubs mypy plugin (see
`examples/blog/mypy.ini`):

```ini
[mypy]
plugins = mypy_django_plugin.main
strict = true

[mypy.plugins.django-stubs]
django_settings_module = config.settings
```

## Pitfalls

- `request` is a parameter, not `self.request`. A missing `request` annotation is an error.
- Class attributes are `ClassVar`; an operation method is an instance method.
- Returning `Status[Model]` from a custom action needs the status annotation, e.g.
  `-> Status[Post]`; the declared `response={201: PostOut}` is not inferred from the body.
- Under strict mode, passing `object` where a model is expected is an error; narrow with
  `isinstance` or annotate the source, rather than casting.
