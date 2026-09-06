from __future__ import annotations

from dataclasses import dataclass


SHEETS_READ_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
GOOGLE_IDENTITY_SCOPES = (
    "openid",
    "email",
)
CLASSROOM_READ_SCOPES = (
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
)
CLASSROOM_COURSEWORK_SCOPES = (
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
)
CLASSROOM_MATERIALS_READ_SCOPE = "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly"


@dataclass(frozen=True)
class GoogleIntegrationConfig:
    spreadsheet_id: str | None
    classroom_course_id: str | None
    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    @property
    def sheets_ready(self) -> bool:
        return bool(self.oauth_ready and self.spreadsheet_id)

    @property
    def classroom_ready(self) -> bool:
        return bool(self.oauth_ready and self.classroom_course_id)

    @property
    def redirect_uri_example(self) -> str:
        return self.redirect_uri or "http://localhost:8000/api/integrations/google/callback"


@dataclass(frozen=True)
class ClassroomCourse:
    external_id: str
    internal_group_id: str
    title: str


@dataclass(frozen=True)
class ClassroomCourseWork:
    external_id: str
    course_external_id: str
    title: str
    description: str
    due_at: str | None
    alternate_link: str | None


@dataclass(frozen=True)
class ClassroomStudentSubmission:
    external_id: str
    coursework_external_id: str
    external_student_id: str
    state: str
    assigned_grade: float | None
