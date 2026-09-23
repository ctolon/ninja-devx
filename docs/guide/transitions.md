# State transitions

`TransitionsMixin` turns a set of named state moves into API operations, with the same
permissions, object checks and row locking as the rest of a model controller.

```python
from ninja_devx import IsStaff
from ninja_devx.crud import CRUDController
from ninja_devx.crud.transitions import Transition, TransitionsMixin


class ArticleController(
    TransitionsMixin[Article, ArticleOut],
    CRUDController[Article, ArticleOut, ArticleIn],
):
    state_field = "status"
    transitions = {
        "publish": Transition(source=("draft",), target="published", permissions=[IsStaff()]),
        "archive": Transition(source=("published",), target="archived"),
    }
```

- Each entry becomes `POST /{pk}/<name>`, returning the output schema. Permissions are the
  controller's plus the transition's own (`Also` is applied for you); object-level checks
  and row locking (`get_object(..., lock=True)`) run the same way a write does.
- The current state must be one of `source`; otherwise the response is 409
  `invalid_transition`. An optional `Transition.guard(request, instance) -> bool` adds a
  business precondition beyond the state itself; a failing guard also answers 409.
- `Transition.on_transition(request, instance)` runs after the new state is saved, inside
  the write transaction. `on_transition(request, instance, name)` on the controller runs
  after that, for every transition.
- `GET /{pk}/transitions` lists the names allowed from the object's current state for the
  caller: source state, guard and permissions are all evaluated, nothing is performed.
- Rename or disable routes with `routes`, keyed by `transition_<name>` and
  `transitions_list` (`GET /{pk}/transitions`'s operation name).
- Sync and async controllers both work; `TransitionsMixin` follows the controller's `mode`
  like every other CRUD mixin.

## Validation

At startup (when `as_router()` builds the routes):

- `state_field` must name an existing `CharField`. When it declares `choices`, every
  `source` and `target` value must be one of them.
- A transition's name must not collide with an existing route (`/{pk}/<name>` already used
  by another operation, including another transition or the mixin's own
  `/{pk}/transitions`).

## What this is not

This is an API-level state machine: it only knows the states you declare and does not
inspect the model. If a project already uses `django-fsm-2` or a similar model-level FSM,
call its `@transition`-decorated method from `Transition.on_transition` (or from
`on_transition()`) instead of duplicating the rules here.
