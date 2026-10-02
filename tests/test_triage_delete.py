"""Deletion of a patient's triage assessments.

The deletion endpoint takes the patient id from the bearer token, never from
the request, so these tests assert that a caller cannot aim the delete at
someone else's records.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.services.triage import TriageService

TRIAGE = "/api/v1/triage"


class _StubRepo:
    def __init__(self) -> None:
        self.deleted_for: list[str] = []
        self.delete_result = 1

    def delete_for_patient(self, patient_id: str) -> int:
        self.deleted_for.append(patient_id)
        return self.delete_result


@pytest.fixture
def repo(monkeypatch) -> _StubRepo:
    stub = _StubRepo()
    monkeypatch.setattr(
        TriageService,
        "__init__",
        lambda self, repository=None, llm=None: setattr(self, "_repo", stub),
    )
    return stub


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def as_patient(client: TestClient):
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="patient-uid", claims={"role": "patient"}
    )
    yield client
    app.dependency_overrides.clear()


def test_delete_requires_auth(client: TestClient) -> None:
    assert client.delete(TRIAGE).status_code == 401


def test_delete_uses_callers_uid(as_patient: TestClient, repo: _StubRepo) -> None:
    response = as_patient.delete(TRIAGE)

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    assert repo.deleted_for == ["patient-uid"]


def test_delete_ignores_patient_id_in_body(as_patient: TestClient, repo: _StubRepo) -> None:
    """A patient_id in the body must not redirect the delete."""
    response = as_patient.request(
        "DELETE", TRIAGE, json={"patient_id": "someone-else"}
    )

    assert response.status_code == 200
    assert repo.deleted_for == ["patient-uid"]


def test_delete_ignores_patient_id_query(as_patient: TestClient, repo: _StubRepo) -> None:
    response = as_patient.delete(TRIAGE, params={"patient_id": "someone-else"})

    assert response.status_code == 200
    assert repo.deleted_for == ["patient-uid"]


def test_delete_ignores_patient_id_path(as_patient: TestClient, repo: _StubRepo) -> None:
    """No per-patient path exists, so it cannot be targeted."""
    response = as_patient.delete(f"{TRIAGE}/someone-else")

    assert response.status_code == 404
    assert repo.deleted_for == []


def test_delete_returns_404_when_nothing_to_delete(repo: _StubRepo) -> None:
    repo.delete_result = 0
    service = TriageService(repository=repo, llm=None)

    with pytest.raises(HTTPException) as exc:
        service.delete_assessments("patient-uid")

    assert exc.value.status_code == 404


def test_delete_reports_actual_count(repo: _StubRepo) -> None:
    repo.delete_result = 7
    service = TriageService(repository=repo, llm=None)

    assert service.delete_assessments("patient-uid") == 7


# --- repository batching --------------------------------------------------


class _Batch:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink
        self._ops: list[str] = []

    def delete(self, ref) -> None:
        self._ops.append(ref._id)

    def commit(self) -> None:
        self._sink.extend(self._ops)
        self._ops.clear()


class _Doc:
    def __init__(self, doc_id: str) -> None:
        self._id = doc_id

    def delete(self) -> None:
        raise AssertionError("batch.delete must be used, not a bare delete")


class _Query:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def stream(self):
        for doc_id in self._ids:
            yield type("Snap", (), {"id": doc_id})()


class _Coll:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.filters: list = []

    def where(self, filter=None):
        self.filters.append(filter)
        return _Query(self._ids)

    def document(self, doc_id=None):
        return _Doc(doc_id)


class _Db:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.commits = 0
        self.coll = _Coll(ids)

    def collection(self, name):
        assert name == "triage_assessments"
        return self.coll

    def batch(self) -> _Batch:
        self.commits += 1
        return _Batch(self.deleted)

    deleted: list[str]


def _repo_with(ids: list[str]):
    from app.repositories.triage import TriageRepository

    db = _Db(ids)
    db.deleted = []
    return TriageRepository(db=db), db


def test_repository_deletes_every_assessment_for_patient() -> None:
    repo, db = _repo_with(["a", "b", "c"])

    assert repo.delete_for_patient("patient-uid") == 3
    assert sorted(db.deleted) == ["a", "b", "c"]


def test_repository_deletes_nothing_when_patient_has_no_records() -> None:
    repo, db = _repo_with([])

    assert repo.delete_for_patient("ghost") == 0
    assert db.deleted == []
    assert db.commits == 0


def test_repository_splits_writes_into_batches_of_500() -> None:
    """Firestore rejects a batch over 500 writes, so long histories must chunk."""
    repo, db = _repo_with([f"doc-{i}" for i in range(1201)])

    assert repo.delete_for_patient("patient-uid") == 1201
    assert len(db.deleted) == 1201
    assert db.commits == 3


def test_repository_ignores_other_patients_records() -> None:
    """The filter is server-side, so only the target patient's docs are touched."""
    repo, db = _repo_with(["mine-1", "mine-2"])
    repo.delete_for_patient("patient-uid")

    filter_ = db.coll.filters[0]
    assert (filter_.field_path, filter_.op_string, filter_.value) == (
        "patient_id",
        "==",
        "patient-uid",
    )


def test_repository_streams_ids_rather_than_truncating(monkeypatch) -> None:
    """ids_for_patient must not inherit list_for_patient's 20-item default page."""
    repo, _ = _repo_with([f"doc-{i}" for i in range(45)])

    assert len(repo.ids_for_patient("patient-uid")) == 45
