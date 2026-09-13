import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from ninja.testing import TestClient

from tracker.models import Issue, Membership, Project, Workspace
from tracker.tests.conftest import as_user

pytestmark = pytest.mark.django_db


def create_project(client: TestClient, name: str = "Rocket", user: str = "alice") -> int:
    response = client.post("/v1/projects/", json={"name": name}, headers=as_user(user))
    assert response.status_code == 201, response.json()
    project_id: int = response.json()["id"]
    return project_id


def test_workspaces_are_isolated(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    globex = Workspace.objects.create(slug="globex", name="Globex")
    eve = User.objects.create(username="eve")
    Membership.objects.create(workspace=globex, user=eve)

    assert client.get("/v1/projects/", headers=as_user("eve", "globex")).json() == []
    assert (
        client.get(f"/v1/projects/{project_id}", headers=as_user("eve", "globex")).status_code
        == 404
    )
    # eve is not a member of acme: no tenant, 403
    forbidden = client.get("/v1/projects/", headers=as_user("eve", "acme"))
    assert (forbidden.status_code, forbidden.json()["code"]) == (403, "tenant_required")
    assert forbidden["Content-Type"] == "application/problem+json"


def test_archiving_and_admin_only_unarchive(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    assert client.delete(f"/v1/projects/{project_id}", headers=as_user("bob")).status_code == 403
    assert client.delete(f"/v1/projects/{project_id}", headers=as_user("alice")).status_code == 204
    project = Project.objects.get(pk=project_id)
    assert project.archived_by is not None
    assert project.archived_by.username == "alice"
    assert client.get("/v1/projects/", headers=as_user("bob")).json() == []
    assert (
        client.post(f"/v1/projects/{project_id}/unarchive", headers=as_user("bob")).status_code
        == 403
    )
    assert (
        client.post(f"/v1/projects/{project_id}/unarchive", headers=as_user("alice")).status_code
        == 200
    )


def test_issue_optimistic_locking(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    base = f"/v1/projects/{project_id}/issues"
    issue = client.post(f"{base}/", json={"title": "Crash on start"}, headers=as_user("bob")).json()

    read = client.get(f"{base}/{issue['id']}", headers=as_user("bob"))
    tag = read["ETag"]
    unchanged = client.get(
        f"{base}/{issue['id']}", headers={**as_user("bob"), "If-None-Match": tag}
    )
    assert unchanged.status_code == 304

    missing = client.patch(f"{base}/{issue['id']}", json={"priority": 1}, headers=as_user("bob"))
    assert missing.status_code == 428
    first = client.patch(
        f"{base}/{issue['id']}", json={"priority": 1}, headers={**as_user("bob"), "If-Match": tag}
    )
    assert first.status_code == 200
    stale = client.patch(
        f"{base}/{issue['id']}", json={"priority": 5}, headers={**as_user("alice"), "If-Match": tag}
    )
    assert (stale.status_code, stale.json()["code"]) == (412, "precondition_failed")


def test_closed_issues_are_a_conflict(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    project = Project.objects.get(pk=project_id)
    issue = Issue.objects.create(project=project, title="Done", status=Issue.Status.CLOSED)
    base = f"/v1/projects/{project_id}/issues/{issue.pk}"
    tag = client.get(base, headers=as_user("bob"))["ETag"]
    response = client.patch(
        base, json={"title": "Again"}, headers={**as_user("bob"), "If-Match": tag}
    )
    assert (response.status_code, response.json()["code"]) == (409, "issue_closed")


def test_internal_notes_are_for_admins(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    project = Project.objects.get(pk=project_id)
    Issue.objects.create(project=project, title="Bug", internal_notes="customer is angry")
    base = f"/v1/projects/{project_id}/issues/"
    assert "internal_notes" not in client.get(base, headers=as_user("bob")).json()["results"][0]
    assert client.get(base, headers=as_user("alice")).json()["results"][0]["internal_notes"] == (
        "customer is angry"
    )


def test_cursor_pagination_and_filters(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    project = Project.objects.get(pk=project_id)
    Issue.objects.bulk_create(
        Issue(project=project, title=f"Issue {i}", priority=i % 6) for i in range(45)
    )
    base = f"/v1/projects/{project_id}/issues/"
    first = client.get(f"{base}?ordering=priority&priority__gte=2", headers=as_user("bob")).json()
    assert len(first["results"]) == 20
    assert first["next"] is not None
    assert [issue["priority"] for issue in first["results"]] == sorted(
        issue["priority"] for issue in first["results"]
    )


def test_issue_writes_are_throttled(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    base = f"/v1/projects/{project_id}/issues/"
    with override_settings(
        NINJA_DEVX={
            "TENANT_RESOLVER": "tracker.tenancy.workspace_of",
            "THROTTLE_RATES": {"issue-writes": "2/min"},
        }
    ):
        codes = [
            client.post(base, json={"title": f"t{i}"}, headers=as_user("bob")).status_code
            for i in range(3)
        ]
    assert codes == [201, 201, 429]


def test_issues_of_another_workspace_project_are_hidden(
    client: TestClient, acme: Workspace
) -> None:
    globex = Workspace.objects.create(slug="globex", name="Globex")
    theirs = Project.objects.create(workspace=globex, name="Theirs")
    response = client.post(
        f"/v1/projects/{theirs.pk}/issues/", json={"title": "sneaky"}, headers=as_user("bob")
    )
    assert response.status_code == 404
    assert not Issue.objects.exists()


def test_system_checks_and_schema_drift() -> None:
    from django.core.management import call_command

    call_command("check", fail_level="WARNING")
    call_command("devx_scaffold", "--check")


def test_changes_are_audited_and_exported(client: TestClient, acme: Workspace) -> None:
    project_id = create_project(client)
    base = f"/v1/projects/{project_id}/issues"
    created = client.post(f"{base}/", json={"title": "Slow"}, headers=as_user("bob"))
    issue = created.json()
    tag = client.get(f"{base}/{issue['id']}", headers=as_user("bob"))["ETag"]
    client.patch(
        f"{base}/{issue['id']}",
        json={"priority": 5},
        headers={**as_user("bob"), "If-Match": tag, "X-Request-ID": "req-1"},
    )

    assert client.get(f"{base}/{issue['id']}/history", headers=as_user("bob")).status_code == 403
    history_page = client.get(f"{base}/{issue['id']}/history", headers=as_user("alice")).json()
    assert history_page["count"] == 2
    history = history_page["items"]
    assert [entry["action"] for entry in history] == ["update", "create"]
    assert history[0]["changes"]["priority"] == [3, 5]
    assert history[0]["request_id"] == "req-1"
    assert history[0]["metadata"] == {"workspace": "acme"}

    export = client.get(f"{base}/export?format=csv", headers=as_user("bob"))
    assert export["Content-Type"] == "text/csv; charset=utf-8"
    assert b"Slow" in export.content


def test_health_and_request_ids(client: TestClient, db: None) -> None:
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert {check["name"] for check in ready.json()["checks"]} == {"database", "cache"}
    assert ready["X-Request-ID"]
    assert ready["Server-Timing"].startswith("app;dur=")


def test_documents_are_private_until_shared(client: TestClient, acme: Workspace) -> None:
    created = client.post("/v1/documents/", json={"title": "Plan"}, headers=as_user("alice"))
    assert created.status_code == 201, created.json()
    document_id = created.json()["id"]
    bob = User.objects.get(username="bob")

    assert client.get("/v1/documents/", headers=as_user("bob")).json() == []
    assert client.get(f"/v1/documents/{document_id}", headers=as_user("bob")).status_code == 404

    share = client.put(
        f"/v1/documents/{document_id}/permissions",
        json={"user_id": bob.pk, "permissions": ["view"]},
        headers=as_user("alice"),
    )
    assert share.status_code == 200, share.json()
    assert [
        doc["title"] for doc in client.get("/v1/documents/", headers=as_user("bob")).json()
    ] == ["Plan"]
    # bob can read but not edit (403, not 404: he can see it)
    edit = client.patch(
        f"/v1/documents/{document_id}", json={"title": "Mine"}, headers=as_user("bob")
    )
    assert edit.status_code == 403

    outsider = User.objects.create(username="mallory")
    rejected = client.put(
        f"/v1/documents/{document_id}/permissions",
        json={"user_id": outsider.pk, "permissions": ["view"]},
        headers=as_user("alice"),
    )
    assert rejected.status_code == 422
