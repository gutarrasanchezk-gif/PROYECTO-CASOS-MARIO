"""Private intranet authentication and authorization integration tests."""

import io
from datetime import timedelta
import hashlib
import time

from itsdangerous import TimestampSigner
import pytest
from werkzeug.security import check_password_hash

from app import create_app
from storage import connect_db, init_db


ADMIN_PASSWORD = "ClaveDePrueba2026!"
EDITOR_PASSWORD = "OtraClaveDePrueba2026!"
NEW_PASSWORD = "NuevaClaveDePrueba2026!"
USER_FIELDS = {"id", "username", "name", "role", "active", "created_at", "updated_at"}
SESSION_USER_FIELDS = {"id", "username", "name", "role"}


@pytest.fixture
def app(tmp_path):
    # Deliberately omit AUTH_REQUIRED: production must be private by default.
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-authentication-session-key",
        "DATABASE": str(tmp_path / "private.sqlite3"),
    })


@pytest.fixture
def client(app):
    return app.test_client()


def session(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    return response.get_json()


def csrf(client):
    return {"X-CSRF-Token": session(client)["csrf_token"]}


def provision_admin(app, username="admin", name="Administrador", password=ADMIN_PASSWORD):
    result = app.test_cli_runner().invoke(
        args=["create-admin", "--username", username, "--name", name],
        input=f"{password}\n{password}\n",
    )
    assert result.exit_code == 0, result.output
    assert password not in result.output
    return result


def login(client, username="admin", password=ADMIN_PASSWORD):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}, headers=csrf(client),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["authenticated"] is True
    return response.get_json()


@pytest.fixture
def admin_client(app):
    provision_admin(app)
    client = app.test_client()
    login(client)
    return client


def create_user(admin_client, username="editor", name="Lucía", password=EDITOR_PASSWORD, role="editor"):
    response = admin_client.post(
        "/api/users", json={"username": username, "name": name, "password": password, "role": role},
        headers=csrf(admin_client),
    )
    assert response.status_code == 201, response.get_json()
    assert set(response.get_json()) == USER_FIELDS
    return response.get_json()


@pytest.fixture
def editor_client(app, admin_client):
    create_user(admin_client)
    client = app.test_client()
    login(client, username="editor", password=EDITOR_PASSWORD)
    return client


def test_new_installation_has_no_public_account_creation(app, client):
    assert app.config["AUTH_REQUIRED"] is True
    state = session(client)
    assert state["authenticated"] is False
    assert state["setup_required"] is True
    assert state["user"] is None
    assert state["csrf_token"]
    assert "cases" not in state and "tasks" not in state
    response = client.post(
        "/api/users", json={"username": "public", "name": "Public", "password": ADMIN_PASSWORD, "role": "admin"},
        headers=csrf(client),
    )
    assert response.status_code == 401
    assert client.post("/api/auth/register", json={}, headers=csrf(client)).status_code in {401, 404}
    assert session(client)["setup_required"] is True


@pytest.mark.parametrize("value, expected", [
    ("", True), ("tru", True), ("garbage", True), ("false", False), ("0", False), ("no", False),
])
def test_auth_environment_values_fail_closed_except_explicit_optout(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("AUTH_REQUIRED", value)
    configured = create_app({
        "TESTING": True, "SECRET_KEY": "environment-test-key", "DATABASE": str(tmp_path / "environment.sqlite3"),
    })
    assert configured.config["AUTH_REQUIRED"] is expected
    response = configured.test_client().get("/api/bootstrap")
    assert response.status_code == (401 if expected else 200)


@pytest.mark.parametrize("path", [
    "/api/bootstrap", "/api/cases/1", "/api/export/cases.csv", "/api/export/tasks.csv", "/api/users",
])
def test_anonymous_people_cannot_read_cases_or_users(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert set(response.get_json()) == {"error"}
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_anonymous_people_cannot_write_or_import_even_with_csrf(client):
    headers = csrf(client)
    requests = [
        ("post", "/api/cases", {"name": "Intento"}),
        ("patch", "/api/cases/1", {"name": "Intento"}),
        ("post", "/api/cases/1/activities", {"title": "Intento"}),
        ("post", "/api/tasks", {"title": "Intento"}),
        ("patch", "/api/tasks/1", {"status": "completada"}),
    ]
    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload, headers=headers)
        assert response.status_code == 401
    response = client.post(
        "/api/import", data={"file": (io.BytesIO(b"not a workbook"), "cases.xlsx")}, headers=headers,
    )
    assert response.status_code == 401
    assert client.get("/api/health").get_json() == {"status": "ok"}


def test_cli_provisions_private_admin_with_hashed_password(app, client):
    provision_admin(app)
    assert session(client)["setup_required"] is False
    connection = connect_db(app.config["DATABASE"])
    try:
        user = connection.execute("SELECT * FROM users WHERE username = ?", ("admin",)).fetchone()
        assert user["role"] == "admin"
        assert user["password_hash"] != ADMIN_PASSWORD
        assert check_password_hash(user["password_hash"], ADMIN_PASSWORD)
    finally:
        connection.close()


def test_cli_rejects_duplicate_username_and_weak_password(app):
    weak = app.test_cli_runner().invoke(
        args=["create-admin", "--username", "admin", "--name", "Administrador"], input="corto\ncorto\n",
    )
    assert weak.exit_code != 0
    assert weak.output and "corto" not in weak.output
    provision_admin(app)
    duplicate = app.test_cli_runner().invoke(
        args=["create-admin", "--username", "admin", "--name", "Duplicado"],
        input=f"{ADMIN_PASSWORD}\n{ADMIN_PASSWORD}\n",
    )
    assert duplicate.exit_code != 0
    connection = connect_db(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        connection.close()


def test_admin_cli_accepts_password_only_through_hidden_prompt(app):
    help_result = app.test_cli_runner().invoke(args=["create-admin", "--help"])
    assert help_result.exit_code == 0
    assert "--password" not in help_result.output
    result = app.test_cli_runner().invoke(args=[
        "create-admin", "--username", "admin", "--name", "Administrador", "--password", ADMIN_PASSWORD,
    ])
    assert result.exit_code != 0
    assert ADMIN_PASSWORD not in result.output
    connection = connect_db(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        connection.close()


def test_login_requires_csrf_and_rotates_anonymous_token(app, client):
    provision_admin(app)
    initial = session(client)
    for headers in ({}, {"X-CSRF-Token": "incorrect"}, csrf(app.test_client()),
                    {**csrf(client), "Origin": "https://another-origin.example"}):
        response = client.post(
            "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}, headers=headers,
        )
        assert response.status_code == 403
        assert session(client)["authenticated"] is False
    state = login(client)
    assert state["csrf_token"] != initial["csrf_token"]
    assert set(state["user"]) == SESSION_USER_FIELDS
    assert state["user"]["username"] == "admin"
    assert state["user"]["name"] == "Administrador"
    assert state["user"]["role"] == "admin"
    assert client.get("/api/bootstrap").status_code == 200
    response = client.post(
        "/api/cases", json={"name": "Cliente"}, headers={"X-CSRF-Token": initial["csrf_token"]},
    )
    assert response.status_code == 403


def test_login_does_not_reveal_whether_username_exists(app, client):
    provision_admin(app)
    responses = [client.post(
        "/api/auth/login", json={"username": username, "password": "PasswordIncorrecta"}, headers=csrf(client),
    ) for username in ("admin", "does-not-exist")]
    assert [response.status_code for response in responses] == [401, 401]
    assert responses[0].get_json() == responses[1].get_json()
    assert session(client)["authenticated"] is False


def test_repeated_invalid_login_is_rate_limited(app, client):
    provision_admin(app)
    app.config["LOGIN_MAX_ATTEMPTS"] = 3
    app.config["LOGIN_WINDOW_SECONDS"] = 900
    for attempt in range(3):
        response = client.post(
            "/api/auth/login", json={"username": "admin", "password": "PasswordIncorrecta"}, headers=csrf(client),
        )
        assert response.status_code == 401, attempt
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}, headers=csrf(client),
    )
    assert response.status_code == 429
    assert response.get_json()["error"]
    assert session(client)["authenticated"] is False


def test_login_limit_expires_and_successful_login_clears_bucket(app, client):
    provision_admin(app)
    app.config["LOGIN_MAX_ATTEMPTS"] = 1
    app.config["LOGIN_WINDOW_SECONDS"] = 900
    wrong = client.post(
        "/api/auth/login", json={"username": "admin", "password": "PasswordIncorrecta"}, headers=csrf(client),
    )
    assert wrong.status_code == 401
    blocked = client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}, headers=csrf(client),
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    connection = connect_db(app.config["DATABASE"])
    try:
        with connection:
            connection.execute("UPDATE login_throttle SET window_started = ?", (time.time() - 901,))
    finally:
        connection.close()
    login(client)
    connection = connect_db(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) FROM login_throttle").fetchone()[0] == 0
    finally:
        connection.close()


def test_login_throttle_table_cannot_grow_beyond_configured_cap(app, client):
    provision_admin(app)
    app.config["LOGIN_MAX_TRACKED"] = 2
    app.config["LOGIN_MAX_ATTEMPTS"] = 3
    app.config["LOGIN_WINDOW_SECONDS"] = 900
    payload = {"username": "admin", "password": "PasswordIncorrecta"}
    for address in ("192.0.2.1", "192.0.2.2"):
        response = client.post(
            "/api/auth/login", json=payload, headers=csrf(client), environ_overrides={"REMOTE_ADDR": address},
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/auth/login", json=payload, headers=csrf(client), environ_overrides={"REMOTE_ADDR": "192.0.2.3"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    # A tracked address can still use its remaining attempts at the cap.
    existing = client.post(
        "/api/auth/login", json=payload, headers=csrf(client), environ_overrides={"REMOTE_ADDR": "192.0.2.1"},
    )
    assert existing.status_code == 401
    connection = connect_db(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) FROM login_throttle").fetchone()[0] == 2
    finally:
        connection.close()


def test_authenticated_actor_overrides_client_supplied_attribution(editor_client):
    client = editor_client
    actor = session(client)["user"]["name"]
    headers = csrf(client)
    response = client.post("/api/cases", json={"name": "Cliente", "author": "Nombre falsificado"}, headers=headers)
    assert response.status_code == 201
    case = response.get_json()
    # Private clients do not have to send an author at all.
    activity = client.post(
        f"/api/cases/{case['id']}/activities",
        json={"title": "Actuado", "type": "nota", "occurred_on": "2025-01-01"}, headers=headers,
    )
    assert activity.status_code == 201
    assert activity.get_json()["author"] == actor
    task = client.post(
        "/api/tasks", json={"case_id": case["id"], "title": "Pendiente", "author": "Nombre falsificado"}, headers=headers,
    )
    assert task.status_code == 201
    assert task.get_json()["author"] == actor
    edited = client.patch(
        f"/api/tasks/{task.get_json()['id']}", json={"status": "completada", "author": "Nombre falsificado"}, headers=headers,
    )
    assert edited.status_code == 200
    assert edited.get_json()["updated_by"] == actor
    assert client.patch(f"/api/cases/{case['id']}", json={"notes": "Nota"}, headers=headers).status_code == 200
    activities = client.get(f"/api/cases/{case['id']}").get_json()["activities"]
    assert activities and all(item["author"] == actor for item in activities)


def test_excel_import_uses_authenticated_actor_instead_of_form_author(editor_client):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Assigments"
    sheet.append(["Last Name", "First Name", "A Number", "Assignment", "Assigned Clerk", "Deadline", "Date Completed"])
    sheet.append(["Prueba", "Ana", "A-TEST-PRIVATE", "Revisar expediente", "Mario", None, None])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    content = buffer.getvalue()
    actor = session(editor_client)["user"]["name"]
    response = editor_client.post(
        "/api/import", data={"author": "Nombre falsificado", "file": (io.BytesIO(content), "private.xlsx")},
        headers=csrf(editor_client),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["tasks_created"] == 1
    tasks = editor_client.get("/api/bootstrap").get_json()["tasks"]
    assert tasks[0]["author"] == actor
    assert tasks[0]["updated_by"] == actor
    # A private UI sends no free-form author field.
    repeated = editor_client.post(
        "/api/import", data={"file": (io.BytesIO(content), "private.xlsx")}, headers=csrf(editor_client),
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_imported"] is True


def test_user_responses_exclude_authentication_secrets(admin_client):
    created = create_user(admin_client)
    assert created["active"] is True
    assert set(created) == USER_FIELDS
    users = admin_client.get("/api/users").get_json()
    assert isinstance(users, list) and len(users) == 2
    assert all(set(user) == USER_FIELDS for user in users)
    assert all(type(user["active"]) is bool for user in users)
    assert ADMIN_PASSWORD not in str(users) and EDITOR_PASSWORD not in str(users)


def test_editors_cannot_manage_accounts(editor_client):
    client = editor_client
    user_id = session(client)["user"]["id"]
    headers = csrf(client)
    assert client.get("/api/users").status_code == 403
    requests = [
        ("post", "/api/users", {"username": "other", "name": "Other", "password": NEW_PASSWORD, "role": "admin"}),
        ("patch", f"/api/users/{user_id}", {"role": "admin"}),
        ("post", f"/api/users/{user_id}/reset-password", {"password": NEW_PASSWORD}),
    ]
    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload, headers=headers)
        assert response.status_code == 403
    assert session(client)["user"]["role"] == "editor"


def test_last_admin_cannot_be_demoted_and_own_account_cannot_be_disabled(admin_client):
    own_id = session(admin_client)["user"]["id"]
    headers = csrf(admin_client)
    for payload in ({"role": "editor"}, {"active": False}):
        response = admin_client.patch(f"/api/users/{own_id}", json=payload, headers=headers)
        assert response.status_code == 400
    create_user(admin_client, username="second-admin", name="Otro administrador", role="admin")
    response = admin_client.patch(f"/api/users/{own_id}", json={"active": False}, headers=headers)
    assert response.status_code == 400
    assert session(admin_client)["authenticated"] is True


@pytest.mark.parametrize("payload", [
    {"username": "a", "name": "Prueba", "password": NEW_PASSWORD, "role": "editor"},
    {"username": "ab", "name": "Prueba", "password": NEW_PASSWORD, "role": "editor"},
    {"username": "bad user", "name": "Prueba", "password": NEW_PASSWORD, "role": "editor"},
    {"username": "valid", "name": "  ", "password": NEW_PASSWORD, "role": "editor"},
    {"username": "valid", "name": "Prueba", "password": "corto", "role": "editor"},
    {"username": "valid", "name": "Prueba", "password": " " * 12, "role": "editor"},
    {"username": "valid", "name": "Prueba", "password": NEW_PASSWORD, "role": "owner"},
    {"username": "valid", "name": "Prueba", "password": NEW_PASSWORD, "role": "editor", "active": False},
])
def test_user_creation_validates_fields(admin_client, payload):
    response = admin_client.post("/api/users", json=payload, headers=csrf(admin_client))
    assert response.status_code == 400
    assert len(admin_client.get("/api/users").get_json()) == 1


def test_usernames_are_normalized_and_duplicates_rejected(app, admin_client):
    user = create_user(admin_client, username="  EDITOR  ")
    assert user["username"] == "editor"
    response = admin_client.post(
        "/api/users", json={"username": "Editor", "name": "Otro", "password": NEW_PASSWORD, "role": "editor"},
        headers=csrf(admin_client),
    )
    assert response.status_code == 400
    client = app.test_client()
    state = login(client, username="  EDITOR  ", password=EDITOR_PASSWORD)
    assert state["user"]["username"] == "editor"


def test_password_whitespace_is_preserved_not_trimmed(app, admin_client):
    password = " " + EDITOR_PASSWORD + " "
    create_user(admin_client, password=password)
    client = app.test_client()
    wrong = client.post(
        "/api/auth/login", json={"username": "editor", "password": EDITOR_PASSWORD}, headers=csrf(client),
    )
    assert wrong.status_code == 401
    login(client, "editor", password)


def test_account_update_requires_boolean_active_and_excludes_internal_fields(admin_client):
    user = create_user(admin_client)
    for payload in ({"active": "false"}, {"active": 0}, {"password_hash": "forged"}, {}):
        response = admin_client.patch(f"/api/users/{user['id']}", json=payload, headers=csrf(admin_client))
        assert response.status_code == 400
    response = admin_client.patch(f"/api/users/{user['id']}", json={"name": "Lucía actualizada"}, headers=csrf(admin_client))
    assert response.status_code == 200
    assert set(response.get_json()) == USER_FIELDS
    assert response.get_json()["name"] == "Lucía actualizada"


def test_account_operations_handle_missing_and_huge_ids_without_server_errors(admin_client):
    headers = csrf(admin_client)
    for user_id, expected in ((9999, 404), (2**80, 400)):
        changed = admin_client.patch(f"/api/users/{user_id}", json={"name": "Actualizado"}, headers=headers)
        assert changed.status_code == expected
        reset = admin_client.post(f"/api/users/{user_id}/reset-password", json={"password": NEW_PASSWORD}, headers=headers)
        assert reset.status_code == expected


def test_administrator_reset_revokes_existing_sessions_and_old_password(app, admin_client):
    user = create_user(admin_client)
    editor = app.test_client()
    another_editor = app.test_client()
    login(editor, "editor", EDITOR_PASSWORD)
    login(another_editor, "editor", EDITOR_PASSWORD)
    response = admin_client.post(
        f"/api/users/{user['id']}/reset-password", json={"password": NEW_PASSWORD}, headers=csrf(admin_client),
    )
    assert response.status_code == 200
    assert "password_hash" not in response.get_json()
    assert editor.get("/api/bootstrap").status_code == 401
    assert another_editor.get("/api/bootstrap").status_code == 401
    old_login = editor.post(
        "/api/auth/login", json={"username": "editor", "password": EDITOR_PASSWORD}, headers=csrf(editor),
    )
    assert old_login.status_code == 401
    login(editor, "editor", NEW_PASSWORD)
    assert editor.get("/api/bootstrap").status_code == 200


def test_password_change_keeps_current_session_and_revokes_other_sessions(app, admin_client):
    create_user(admin_client)
    editor = app.test_client()
    another_editor = app.test_client()
    current = login(editor, "editor", EDITOR_PASSWORD)
    login(another_editor, "editor", EDITOR_PASSWORD)
    response = editor.post(
        "/api/auth/change-password", json={"current_password": EDITOR_PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf(editor),
    )
    assert response.status_code == 200
    state = response.get_json()
    assert state["authenticated"] is True
    assert state["csrf_token"] != current["csrf_token"]
    assert editor.get("/api/bootstrap").status_code == 200
    assert another_editor.get("/api/bootstrap").status_code == 401
    wrong = another_editor.post(
        "/api/auth/login", json={"username": "editor", "password": EDITOR_PASSWORD}, headers=csrf(another_editor),
    )
    assert wrong.status_code == 401
    login(another_editor, "editor", NEW_PASSWORD)


def test_wrong_current_password_or_weak_new_password_cannot_change_login(app, admin_client):
    for current, new in (("PasswordIncorrecta", NEW_PASSWORD), (ADMIN_PASSWORD, "corto")):
        response = admin_client.post(
            "/api/auth/change-password", json={"current_password": current, "new_password": new}, headers=csrf(admin_client),
        )
        assert response.status_code in {400, 401, 403}
        assert response.get_json()["error"]
    assert admin_client.get("/api/bootstrap").status_code == 200
    login(app.test_client())


def test_deactivation_revokes_access_and_reactivation_does_not_restore_old_session(app, admin_client):
    user = create_user(admin_client)
    editor = app.test_client()
    login(editor, "editor", EDITOR_PASSWORD)
    response = admin_client.patch(f"/api/users/{user['id']}", json={"active": False}, headers=csrf(admin_client))
    assert response.status_code == 200
    assert response.get_json()["active"] is False
    assert editor.get("/api/bootstrap").status_code == 401
    inactive = editor.post(
        "/api/auth/login", json={"username": "editor", "password": EDITOR_PASSWORD}, headers=csrf(editor),
    )
    assert inactive.status_code == 401
    assert admin_client.patch(f"/api/users/{user['id']}", json={"active": True}, headers=csrf(admin_client)).status_code == 200
    assert editor.get("/api/bootstrap").status_code == 401
    login(editor, "editor", EDITOR_PASSWORD)


def test_role_change_revokes_sessions_and_new_login_receives_new_permissions(app, admin_client):
    user = create_user(admin_client)
    editor = app.test_client()
    login(editor, "editor", EDITOR_PASSWORD)
    response = admin_client.patch(f"/api/users/{user['id']}", json={"role": "admin"}, headers=csrf(admin_client))
    assert response.status_code == 200
    assert editor.get("/api/bootstrap").status_code == 401
    state = login(editor, "editor", EDITOR_PASSWORD)
    assert state["user"]["role"] == "admin"
    assert editor.get("/api/users").status_code == 200


def test_logout_invalidates_session_and_rotates_csrf(admin_client):
    previous = session(admin_client)
    assert admin_client.post("/api/auth/logout", json={}).status_code == 403
    response = admin_client.post("/api/auth/logout", json={}, headers=csrf(admin_client))
    assert response.status_code == 200
    state = response.get_json()
    assert state["authenticated"] is False
    assert state["user"] is None
    assert state["csrf_token"] != previous["csrf_token"]
    assert admin_client.get("/api/bootstrap").status_code == 401


def cloned_login_cookie(app, client):
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    assert cookie is not None
    clone = app.test_client()
    clone.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie.value)
    assert clone.get("/api/bootstrap").status_code == 200
    return clone


def test_logout_revokes_copied_cookie_on_server(app, admin_client):
    clone = cloned_login_cookie(app, admin_client)
    assert admin_client.post("/api/auth/logout", json={}, headers=csrf(admin_client)).status_code == 200
    assert clone.get("/api/bootstrap").status_code == 401
    assert session(clone)["authenticated"] is False


def test_successful_login_rotation_revokes_previous_cookie(app, admin_client):
    clone = cloned_login_cookie(app, admin_client)
    login(admin_client)
    assert admin_client.get("/api/bootstrap").status_code == 200
    assert clone.get("/api/bootstrap").status_code == 401


@pytest.mark.parametrize("offset", [0, -1])
def test_server_expiry_rejects_recently_signed_cookie(app, admin_client, monkeypatch, offset):
    clone = cloned_login_cookie(app, admin_client)
    now = time.time()
    monkeypatch.setattr("auth.time.time", lambda: now)
    connection = connect_db(app.config["DATABASE"])
    try:
        with connection:
            connection.execute("UPDATE auth_sessions SET expires_at = ?", (now + offset,))
    finally:
        connection.close()
    assert admin_client.get("/api/bootstrap").status_code == 401
    assert clone.get("/api/bootstrap").status_code == 401


def test_server_stores_only_a_digest_of_the_login_token(app, admin_client):
    with admin_client.session_transaction() as current:
        raw_token = current["login_session"]
    connection = connect_db(app.config["DATABASE"])
    try:
        rows = connection.execute("SELECT token_hash FROM auth_sessions").fetchall()
        assert len(rows) == 1
        assert rows[0]["token_hash"] != raw_token
        assert rows[0]["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
    finally:
        connection.close()


def test_auth_session_cookies_and_responses_are_private(client):
    response = client.get("/api/auth/session")
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_session_lifetime_default_and_explicit_override(app, tmp_path):
    assert app.permanent_session_lifetime == timedelta(hours=8)
    configured = create_app({
        "TESTING": True, "SECRET_KEY": "lifetime-test-key", "DATABASE": str(tmp_path / "custom.sqlite3"),
        "PERMANENT_SESSION_LIFETIME": timedelta(minutes=30), "SESSION_COOKIE_SECURE": True,
    })
    assert configured.permanent_session_lifetime == timedelta(minutes=30)
    response = configured.test_client().get("/api/auth/session")
    assert "Secure" in response.headers.get("Set-Cookie", "")


def test_expired_signed_session_cannot_read_private_cases(app, admin_client, monkeypatch):
    with admin_client.session_transaction() as current:
        values = dict(current)
    serializer = app.session_interface.get_signing_serializer(app)
    expired_timestamp = int(time.time() - app.permanent_session_lifetime.total_seconds() - 60)
    with monkeypatch.context() as patch:
        patch.setattr(TimestampSigner, "get_timestamp", lambda self: expired_timestamp)
        cookie = serializer.dumps(values)
    admin_client.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    assert admin_client.get("/api/bootstrap").status_code == 401
    assert session(admin_client)["authenticated"] is False


def test_existing_case_data_and_historical_attribution_survive_auth_initialization(tmp_path):
    database = tmp_path / "existing.sqlite3"
    init_db(database)
    connection = connect_db(database)
    try:
        with connection:
            case_id = connection.execute("INSERT INTO cases (name) VALUES (?)", ("Caso existente",)).lastrowid
            connection.execute(
                "INSERT INTO activities (case_id, title, author, occurred_on) VALUES (?, ?, ?, ?)",
                (case_id, "Actuado histórico", "Autor histórico", "2024-01-01"),
            )
            connection.execute(
                "INSERT INTO tasks (case_id, title, author, updated_by) VALUES (?, ?, ?, ?)",
                (case_id, "Pendiente existente", "Autor histórico", "Autor histórico"),
            )
            # Reproduce the old database, which predates all authentication tables.
            connection.execute("DROP TABLE auth_sessions")
            connection.execute("DROP TABLE login_throttle")
            connection.execute("DROP TABLE users")
    finally:
        connection.close()
    app = create_app({"TESTING": True, "SECRET_KEY": "migration-test-key", "DATABASE": str(database)})
    provision_admin(app)
    client = app.test_client()
    login(client)
    response = client.get(f"/api/cases/{case_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == "Caso existente"
    assert data["activities"][0]["author"] == "Autor histórico"
    assert data["tasks"][0]["author"] == "Autor histórico"
    assert data["tasks"][0]["status"] == "pendiente"
