# Tutorial: a blog API

The finished project is in `examples/blog`.

## 1. The model

```python
class Post(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    tags = models.ManyToManyField(Tag, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
```

## 2. Scaffold

```bash
python manage.py devx_scaffold blog.Post --owner author --output blog/api/posts.py
```

This writes explicit, typed code you own:

```python
class PostOut(Schema):
    id: int
    author_id: int
    title: str
    slug: str
    body: str
    status: Literal["draft", "published"]
    created: datetime
    deleted_at: datetime | None
    tags: list[int]

    @staticmethod
    def resolve_tags(obj: Post) -> list[int]:
        return [related.pk for related in obj.tags.all()]


class PostIn(Schema):
    title: Annotated[str, Field(max_length=200, min_length=1)]
    slug: Annotated[str, Field(max_length=50, min_length=1, pattern="^[-a-zA-Z0-9_]+$")]
    body: str = ""
    status: Literal["draft", "published"] = "draft"
    tags: list[int] = []


class PostController(CRUDController[Post, PostOut, PostIn]):
    owner_field = "author"
    search_fields = ("title", "slug", "body")
    filter_fields = {"author": ("exact",), "status": ("exact",), "created": ("gte", "lte")}
    ordering_fields = ("id", "created", "title")
```

Input constraints come from the model (`max_length`, non-blank strings, the slug
pattern), so invalid input is a 422 before it reaches the database. It also writes
`blog/tests/test_post_api.py`, and those tests pass as generated.

## 3. Mount

```python
# config/urls.py
api = NinjaAPI(title="Blog API", version="1.0.0", auth=django_auth)
mount(api, {"/posts": PostController}, prefix="/v1")
urlpatterns = [path("api/", api.urls)]
```

`owner_field = "author"` adds `IsAuthenticated` and `IsOwner("author")`, and assigns the
author on create. Querying `tags` in `PostOut` makes the list prefetch them automatically,
so the endpoint stays at two queries no matter how many posts there are.

## 4. Extend

```python
class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    options = ControllerOptions(tags=["posts"], hooks=[LoggingHook()])
    owner_field = "author"
    ...

    @post("/{pk}/publish", response=PostOut, decorators=[idempotent()])
    def publish(self, request: HttpRequest, post: Instance[Post]) -> Post:
        post.status = Post.Status.PUBLISHED
        post.save(update_fields=["status"])
        return post
```

- `SoftDeleteMixin` turns `DELETE` into setting `deleted_at`, hides deleted posts and adds
  `POST /{pk}/restore`.
- `Instance[Post]` loads the post from `{pk}`, returning 404 when missing and applying
  object permissions. The type checker sees `post: Post`.
- `idempotent()` replays the first response for a repeated `Idempotency-Key`.

## 5. Nested comments

```python
class CommentController(CRUDController[Comment, CommentOut, CommentIn]):
    options = ControllerOptions(permissions=[IsAuthenticatedOrReadOnly()])
    parent = Parent(Post, field="post")


mount(api, {"/posts/{post_pk}/comments": CommentController}, prefix="/v1")
```

## 6. Test

```python
def test_post_lifecycle(client: TestClient, ada: User) -> None:
    created = client.post("/v1/posts/", json={"title": "Hello", "slug": "hello"}, user=ada)
    assert created.status_code == 201
```

## 7. Generate clients

```bash
python manage.py devx_openapi config.urls.api --format typescript --output clients/blog.ts
python manage.py devx_openapi config.urls.api --format python --output clients/blog_client.py
# in CI:
python manage.py devx_openapi config.urls.api --format typescript --output clients/blog.ts --check
```

## 8. Move rules into a service

When publishing needs rules (only drafts, notify followers after commit), move them out
of the controller:

```python
class PostService(ModelService[Post]):
    def __init__(self, repository: Repository[Post], tasks: TaskQueue) -> None:
        super().__init__(repository)
        self.tasks = tasks

    def publish(self, post: Post) -> Post:
        if post.status == Post.Status.PUBLISHED:
            raise Conflict("Already published")
        with self.repository.transaction():
            post = self.repository.change(post, {"status": Post.Status.PUBLISHED})
            self.tasks.enqueue(notify_followers, post.pk)
        return post


class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
    service_class = PostService

    @post("/{pk}/publish", response=PostOut, raises=(Conflict,))
    def publish(
        self, request: HttpRequest, post: Instance[Post], posts: Inject[PostService]
    ) -> Post:
        return posts.publish(post)
```

`Conflict` becomes a documented 409 without an exception handler. See
[Services, repositories and other layers](../guide/layers.md) and [Errors](../guide/errors.md).

## 9. Check generated contracts in CI

```bash
python manage.py check                 # ninja_devx.* system checks
python manage.py devx_scaffold --check  # schemas still match the models
python manage.py devx_openapi config.urls.api --format python --output clients/blog_client.py --check
```
