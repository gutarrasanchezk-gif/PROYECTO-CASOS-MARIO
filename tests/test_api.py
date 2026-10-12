"""Integration checks for the shared, anonymous case management API."""

import csv
from datetime import datetime
import io
import json
from pathlib import Path
import zipfile

import pytest

from app import create_app
from storage import connect_db


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-session-key",
            "DATABASE": str(tmp_path / "cases.sqlite3"),
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.get_json()["csrf_token"]}


def create_case(client, **overrides):
    payload = {
        "name": "Cliente de prueba",
        "a_number": "A-TEST-001",
        "reference": "EXP-TEST-001",
        "category": "Migratorio",
        "owner": "Mario",
        "country": "Perú",
        "court": "Juzgado de prueba",
        "hearing_date": "2030-01-10",
        "notes": "Información de prueba",
        "author": "Lucía",
    }
    payload.update(overrides)
    response = client.post("/api/cases", json=payload, headers=csrf(client))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def create_task(client, **overrides):
    payload = {
        "title": "Revisar documentos",
        "description": "Solicitar la documentación pendiente",
        "assignee": "Mario",
        "due_date": "2030-02-12",
        "priority": "alta",
        "author": "Lucía",
    }
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload, headers=csrf(client))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_fresh_database_and_health_are_ready(client):
    response = client.get("/api/bootstrap")
    data = response.get_json()
    assert data["cases"] == []
    assert data["tasks"] == []
    assert data["stats"] == {
        "case_count": 0,
        "active_cases": 0,
        "pending_tasks": 0,
        "overdue_tasks": 0,
        "activities_count": 0,
    }
    assert data["csrf_token"]
    assert "SameSite=Lax" in response.headers.get("Set-Cookie", "")
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}


def test_case_changes_persist_for_other_people_and_are_attributed(app, client):
    case = create_case(client)
    assert case["status"] == "activo"
    assert case["name"] == "Cliente de prueba"
    before = client.get(f"/api/cases/{case['id']}").get_json()

    editor = app.test_client()
    response = editor.patch(
        f"/api/cases/{case['id']}",
        json={"name": "Cliente actualizado", "status": "cerrado", "author": "Pedro"},
        headers=csrf(editor),
    )
    assert response.status_code == 200
    observed = client.get(f"/api/cases/{case['id']}").get_json()
    assert observed["name"] == "Cliente actualizado"
    assert observed["status"] == "cerrado"
    assert len(observed["activities"]) > len(before["activities"])
    assert any(item["author"] == "Pedro" for item in observed["activities"])
    assert isinstance(observed["sources"], list)

    bootstrap = client.get("/api/bootstrap").get_json()
    assert bootstrap["cases"][0]["name"] == "Cliente actualizado"
    assert bootstrap["stats"]["case_count"] == 1
    assert bootstrap["stats"]["active_cases"] == 0


def test_activity_history_has_data_and_sorts_by_occurrence_date(client):
    case = create_case(client)
    for date, title in [("2024-03-01", "Presentación"), ("2024-04-01", "Audiencia")]:
        response = client.post(
            f"/api/cases/{case['id']}/activities",
            json={
                "title": title,
                "description": "Se registró el actuado",
                "type": "actuacion",
                "occurred_on": date,
                "author": "Mario",
            },
            headers=csrf(client),
        )
        assert response.status_code == 201
        assert response.get_json()["author"] == "Mario"

    history = client.get(f"/api/cases/{case['id']}").get_json()["activities"]
    authored = [item for item in history if item["type"] == "actuacion"]
    assert [item["title"] for item in authored] == ["Audiencia", "Presentación"]
    assert authored[0]["description"] == "Se registró el actuado"
    assert authored[0]["occurred_on"] == "2024-04-01"
    assert client.get("/api/bootstrap").get_json()["stats"]["activities_count"] >= 2


def test_linked_task_updates_history_counts_and_completion(client):
    case = create_case(client)
    previous_history_size = len(client.get(f"/api/cases/{case['id']}").get_json()["activities"])
    task = create_task(client, case_id=case["id"], due_date="2000-01-01")
    assert task["status"] == "pendiente"
    assert task["case_id"] == case["id"]
    snapshot = client.get("/api/bootstrap").get_json()
    assert snapshot["tasks"][0]["case_name"] == case["name"]
    assert snapshot["cases"][0]["task_count"] == 1
    assert snapshot["stats"]["pending_tasks"] == 1
    assert snapshot["stats"]["overdue_tasks"] == 1
    creation_history = client.get(f"/api/cases/{case['id']}").get_json()["activities"]
    assert len(creation_history) > previous_history_size
    assert any(
        item["author"] == "Lucía" and task["title"] in item["title"] + item["description"]
        for item in creation_history
    )

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "completada", "assignee": "Pedro", "author": "Pedro"},
        headers=csrf(client),
    )
    assert response.status_code == 200
    completed = response.get_json()
    assert completed["completed_at"]
    assert completed["updated_by"] == "Pedro"
    detail = client.get(f"/api/cases/{case['id']}").get_json()
    assert detail["tasks"][0]["status"] == "completada"
    assert any(item["author"] == "Pedro" for item in detail["activities"])
    snapshot = client.get("/api/bootstrap").get_json()
    assert snapshot["stats"]["pending_tasks"] == 0
    assert snapshot["stats"]["overdue_tasks"] == 0
    assert snapshot["cases"][0]["task_count"] == 0

    reopened = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "en_curso", "author": "Lucía"},
        headers=csrf(client),
    )
    assert reopened.status_code == 200
    assert not reopened.get_json()["completed_at"]
    assert client.get("/api/bootstrap").get_json()["stats"]["pending_tasks"] == 1


def test_general_task_can_have_no_case_or_deadline(app, client):
    task = create_task(client, case_id=None, due_date="")
    assert task["case_id"] is None
    observer = app.test_client()
    tasks = observer.get("/api/bootstrap").get_json()["tasks"]
    assert tasks[0]["id"] == task["id"]
    assert tasks[0]["due_date"] == ""


@pytest.mark.parametrize("author", [None, "", "   "])
def test_changes_require_a_nonblank_author(client, author):
    response = client.post(
        "/api/cases", json={"name": "Prueba", "author": author}, headers=csrf(client)
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []


def test_author_is_required_for_every_write(client):
    case = create_case(client)
    task = create_task(client, case_id=case["id"])
    operations = [
        ("post", "/api/cases", {"name": "Sin responsable"}),
        ("patch", f"/api/cases/{case['id']}", {"name": "Cambio sin responsable"}),
        ("post", f"/api/cases/{case['id']}/activities", {
            "title": "Sin responsable", "type": "nota", "occurred_on": "2025-01-01"
        }),
        ("post", "/api/tasks", {"title": "Sin responsable"}),
        ("patch", f"/api/tasks/{task['id']}", {"status": "completada"}),
    ]
    headers = csrf(client)
    for method, url, payload in operations:
        assert getattr(client, method)(url, json=payload, headers=headers).status_code == 400
    assert client.get(f"/api/cases/{case['id']}").get_json()["name"] == case["name"]
    assert client.get("/api/bootstrap").get_json()["tasks"][0]["status"] == "pendiente"


def test_csrf_is_required_and_scoped_to_each_session(app, client):
    foreign_token = csrf(app.test_client())
    for headers in ({}, {"X-CSRF-Token": "incorrect"}, foreign_token):
        response = client.post(
            "/api/cases", json={"name": "Prueba", "author": "Mario"}, headers=headers
        )
        assert response.status_code == 403
        assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []


def test_malformed_security_headers_are_rejected_without_server_errors(client):
    headers = csrf(client)
    for extra in ({"X-CSRF-Token": "é"}, {"Origin": "http://["}):
        response = client.post(
            "/api/cases", json={"name": "Prueba", "author": "Mario"},
            headers={**headers, **extra},
        )
        assert response.status_code == 403
        assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []


def test_mutations_require_json_and_valid_json_objects(client):
    headers = csrf(client)
    response = client.post("/api/cases", data="name=Prueba", headers=headers)
    assert response.status_code == 415
    response = client.post(
        "/api/cases", data="{broken", content_type="application/json", headers=headers
    )
    assert response.status_code == 400
    for body in ([], "text", 123, None):
        response = client.post(
            "/api/cases", data=json.dumps(body),
            content_type="application/json", headers=headers,
        )
        assert response.status_code == 400


@pytest.mark.parametrize("date", ["2025-02-30", "2025-2-01", "01/02/2025", "2025-01-01T00:00:00"])
def test_dates_are_strict_iso_calendar_dates(client, date):
    headers = csrf(client)
    response = client.post(
        "/api/cases", json={"name": "Prueba", "hearing_date": date, "author": "Mario"},
        headers=headers,
    )
    assert response.status_code == 400
    response = client.post(
        "/api/tasks", json={"title": "Prueba", "due_date": date, "author": "Mario"},
        headers=headers,
    )
    assert response.status_code == 400
    case = create_case(client)
    response = client.post(
        f"/api/cases/{case['id']}/activities",
        json={"title": "Prueba", "type": "nota", "occurred_on": date, "author": "Mario"},
        headers=headers,
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case_id", [True, False, "1", 1.5, 0, -1, 2**80])
def test_task_case_id_rejects_nonpositive_and_noninteger_values(client, case_id):
    create_case(client)
    response = client.post(
        "/api/tasks", json={"title": "Prueba", "case_id": case_id, "author": "Mario"},
        headers=csrf(client),
    )
    assert response.status_code == 400


def test_enums_cannot_store_unknown_values(client):
    case = create_case(client)
    task = create_task(client)
    requests = [
        ("post", "/api/cases", {"name": "Prueba", "status": "desconocido"}),
        ("post", "/api/tasks", {"title": "Prueba", "priority": "urgente"}),
        ("patch", f"/api/tasks/{task['id']}", {"status": "cancelada"}),
        ("post", f"/api/cases/{case['id']}/activities", {
            "title": "Prueba", "type": "desconocido", "occurred_on": "2025-01-01"
        }),
    ]
    headers = csrf(client)
    for method, url, payload in requests:
        response = getattr(client, method)(url, json={**payload, "author": "Mario"}, headers=headers)
        assert response.status_code == 400
        assert response.get_json()["error"]


def test_missing_resources_and_empty_updates_are_rejected(client):
    assert client.get("/api/cases/9999").status_code == 404
    assert client.get("/api/cases/not-an-id").status_code == 404
    headers = csrf(client)
    assert client.patch(
        "/api/cases/9999", json={"name": "Prueba", "author": "Mario"}, headers=headers
    ).status_code == 404
    assert client.post(
        "/api/tasks", json={"title": "Prueba", "case_id": 9999, "author": "Mario"}, headers=headers
    ).status_code == 404
    case = create_case(client)
    assert client.patch(
        f"/api/cases/{case['id']}", json={"author": "Mario"}, headers=headers
    ).status_code == 400


def test_csv_exports_have_bom_and_neutralize_formula_cells(client):
    create_case(client, name="=2+2", notes="+SUM(A1:A2)")
    create_task(client, title="@SUM(A1:A2)", description="-2+3", assignee="=HYPERLINK(\"x\")")
    for path, expected in [
        ("/api/export/cases.csv", ["'=2+2", "'+SUM(A1:A2)"]),
        ("/api/export/tasks.csv", ["'@SUM(A1:A2)", "'-2+3", "'=HYPERLINK(\"x\")"]),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.data.startswith(b"\xef\xbb\xbf")
        assert "text/csv" in response.content_type
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        assert len(rows) == 2
        for value in expected:
            assert value in rows[1]


@pytest.mark.parametrize("path", ["/api/bootstrap", "/api/health", "/api/cases/9999", "/api/export/cases.csv"])
def test_api_responses_do_not_cache_case_information(client, path):
    assert "no-store" in client.get(path).headers.get("Cache-Control", "")


@pytest.mark.parametrize("path", [f"/api/cases/{2**80}", f"/api/tasks/{2**80}"])
def test_huge_identifiers_do_not_reach_sqlite(client, path):
    if "/cases/" in path:
        assert client.get(path).status_code == 400
    response = client.patch(path, json={"title": "Cambio", "author": "Mario"} if "/tasks/" in path
                            else {"name": "Cambio", "author": "Mario"}, headers=csrf(client))
    assert response.status_code == 400


def test_imported_whitespace_formulas_are_neutralized_without_losing_content(app, client):
    case = create_case(client)
    task = create_task(client)
    formula_name = "  =SUM(A1:A2)"
    formula_note = "\ttexto importado"
    formula_title = " \n+2+2"
    connection = connect_db(app.config["DATABASE"])
    try:
        with connection:
            connection.execute("UPDATE cases SET name = ?, notes = ? WHERE id = ?",
                               (formula_name, formula_note, case["id"]))
            connection.execute("UPDATE tasks SET title = ? WHERE id = ?", (formula_title, task["id"]))
    finally:
        connection.close()
    for path, originals in [
        ("/api/export/cases.csv", [formula_name, formula_note]),
        ("/api/export/tasks.csv", [formula_title]),
    ]:
        response = client.get(path)
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        for value in originals:
            assert "'" + value in rows[1]


def test_mutation_rejects_another_origin_even_with_valid_csrf(client):
    headers = {**csrf(client), "Origin": "https://another-origin.example"}
    response = client.post("/api/cases", json={"name": "Prueba", "author": "Mario"}, headers=headers)
    assert response.status_code == 403
    assert client.get("/api/bootstrap").get_json()["cases"] == []
    response = client.post(
        "/api/cases", json={"name": "Prueba", "author": "Mario"},
        headers={**csrf(client), "Origin": "http://localhost"},
    )
    assert response.status_code == 201


def test_json_body_size_limit_prevents_large_writes(app, client):
    app.config["MAX_JSON_CONTENT_LENGTH"] = 128
    response = client.post(
        "/api/cases", json={"name": "Prueba", "notes": "x" * 512, "author": "Mario"},
        headers=csrf(client),
    )
    assert response.status_code == 413
    assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []


def test_upload_body_size_limit_is_enforced(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 128
    response = client.post(
        "/api/import", data={"author": "Mario", "file": (io.BytesIO(b"x" * 512), "cases.xlsx")},
        headers=csrf(client),
    )
    assert response.status_code == 413
    assert response.get_json()["error"]


def test_mutation_whitelists_prevent_metadata_changes(client):
    case = create_case(client)
    task = create_task(client, case_id=case["id"])
    operations = [
        ("post", "/api/cases", {"name": "Prueba", "id": 900}),
        ("patch", f"/api/cases/{case['id']}", {"name": "Prueba", "created_at": "2030-01-01"}),
        ("post", f"/api/cases/{case['id']}/activities", {
            "title": "Prueba", "type": "nota", "occurred_on": "2025-01-01", "created_at": "2030-01-01"
        }),
        ("post", "/api/tasks", {"title": "Prueba", "status": "completada"}),
        ("patch", f"/api/tasks/{task['id']}", {"title": "Prueba", "case_id": None}),
    ]
    headers = csrf(client)
    for method, url, payload in operations:
        response = getattr(client, method)(url, json={**payload, "author": "Mario"}, headers=headers)
        assert response.status_code == 400
        assert response.get_json()["error"]
    detail = client.get(f"/api/cases/{case['id']}").get_json()
    assert detail["name"] == case["name"]
    assert detail["tasks"][0]["title"] == task["title"]
    assert detail["tasks"][0]["case_id"] == case["id"]


def test_public_link_discourages_search_engine_indexing(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "User-agent: *" in response.get_data(as_text=True)
    assert "Disallow: /" in response.get_data(as_text=True)
    assert "noindex" in client.get("/api/bootstrap").headers.get("X-Robots-Tag", "")


def workbook_bytes():
    """Build only fictitious records; no uploaded client data belongs in tests."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Assigments"
    sheet.append(["Last Name", "First Name", "A Number", "Assignment", "Assigned Clerk", "Deadline", "Date Completed"])
    sheet.append(["Prueba", "Ana", "A-TEST-001", "Revisar expediente", "Mario", datetime(2030, 3, 1), None])
    calls = workbook.create_sheet("Client Calls")
    calls.append(["Date", "Client Name", "Reason for Calling", "Recipient"])
    calls.append([datetime(2025, 3, 1), "Ana Prueba", "Consulta de prueba", "Mario"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_browser_excel_import_is_shared_attributed_and_idempotent(app, client):
    content = workbook_bytes()
    response = client.post(
        "/api/import", data={"author": "Lucía", "file": (io.BytesIO(content), "pruebas.xlsx")},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.get_json()
    first = response.get_json()
    assert first["already_imported"] is False
    assert first["cases_created"] >= 1
    assert first["tasks_created"] == 1
    assert first["activities_created"] >= 1
    assert first["sources_imported"] == 2
    shared = app.test_client().get("/api/bootstrap").get_json()
    assert shared["import_summary"]["sources_imported"] == 2
    assert shared["tasks"][0]["author"] == "Lucía"
    assert shared["tasks"][0]["updated_by"] == "Lucía"
    details = [client.get(f"/api/cases/{case['id']}").get_json() for case in shared["cases"]]
    activities = [item for detail in details for item in detail["activities"]]
    sources = [item for detail in details for item in detail["sources"]]
    assert any(item["author"] == "Lucía" for item in activities)
    assert all(isinstance(source["data"], dict) for source in sources)

    second = client.post(
        "/api/import", data={"author": "Pedro", "file": (io.BytesIO(content), "pruebas.xlsx")},
        headers=csrf(client),
    )
    assert second.status_code == 200
    repeated = second.get_json()
    assert repeated["already_imported"] is True
    assert all(repeated[field] == 0 for field in ("cases_created", "tasks_created", "activities_created", "sources_imported"))
    assert client.get("/api/bootstrap").get_json()["stats"] == shared["stats"]


def test_browser_import_rejects_invalid_workbook_and_cleans_temporary_file(app, client):
    response = client.post(
        "/api/import", data={"author": "Mario", "file": (io.BytesIO(b"not an Excel zip"), "cases.xlsx")},
        headers=csrf(client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []
    assert not list(Path(app.config["DATABASE"]).parent.glob("import-*.xlsx"))


def test_browser_import_rejects_corrupt_workbook_xml(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "malformed XML")
        archive.writestr("xl/workbook.xml", "malformed XML")
    response = client.post(
        "/api/import", data={"author": "Mario", "file": (io.BytesIO(buffer.getvalue()), "cases.xlsx")},
        headers=csrf(client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert client.get("/api/bootstrap").get_json()["cases"] == []


def test_browser_import_requires_csrf_and_a_recording_author(client):
    content = workbook_bytes()
    response = client.post(
        "/api/import", data={"author": "Mario", "file": (io.BytesIO(content), "cases.xlsx")},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/import", data={"file": (io.BytesIO(content), "cases.xlsx")}, headers=csrf(client),
    )
    assert response.status_code == 400
