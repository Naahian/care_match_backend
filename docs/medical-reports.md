# Medical report uploads

An authenticated admin can upload a report for an existing patient:

```sh
curl -X POST "$API_BASE_URL/api/v1/admin/patients/$PATIENT_UID/medical-reports" \
  -H "Authorization: Bearer $ADMIN_FIREBASE_ID_TOKEN" \
  -F "filename=Blood Test.pdf" \
  -F "file=@/path/to/local-report.pdf"
```

Send exactly two multipart fields: `filename` (the admin's destination name;
extension optional) and `file` (binary upload). For example, `filename=Blood Test`
becomes `Blood Test.jpg` for an image or `Blood Test.pdf` for a PDF, detected from
the file contents. The device's original file
name and declared MIME type do not determine the stored name or content type.
The patient's Firebase UID comes from the URL. The backend checks the admin's
current Firebase role and requires the target account to have the `patient` role.

The object is stored at `medical_reports/{patient_uid}/{filename}`. Uploading
this object creates the patient's folder prefix automatically; there is no
separate folder creation request. Image reports are converted to JPEG at their
original resolution, with EXIF orientation applied and transparency flattened
onto white. The admin's filename stem is preserved and its extension becomes
`.jpg`: `Blood Test.png` is stored as `Blood Test.jpg`. Images are re-encoded at
quality 95 without chroma subsampling; image metadata is removed. PDF bytes and
filenames are preserved. No Firestore record is created.

HTTP 201 returns the standard envelope:

```json
{
  "status": 201,
  "msg": "Created",
  "data": {
    "patient_uid": "patient-uid",
    "filename": "Blood Test.pdf",
    "bucket": "medical_reports",
    "path": "patient-uid/Blood Test.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1024
  }
}
```

Files must be PDF, JPEG, PNG, or WebP and at most 20 MiB (20,971,520 bytes).
Omit the filename extension to detect the file type automatically, or use `.pdf`
for PDF content and an accepted image extension for image content. The source
image format is detected from the bytes. Images are verified
and decoded, with a maximum of 25 million pixels. Converted JPEGs must also fit
the 20 MiB size limit. Response `filename`, `path`, `content_type`, and `size_bytes`
describe the stored file after conversion. PDFs are screened for a PDF version header
and final `%%EOF` marker; this is not full document parsing or malware scanning.
Final filenames (including the generated extension) must start with an ASCII letter or digit, contain at most 200 characters,
and use only ASCII letters, digits, spaces, dots, underscores, hyphens, and
parentheses. Outer whitespace is trimmed. Folder separators and encoded paths
are rejected. Extensions are case insensitive. The UID must fit a single safe
folder segment, at most 128 characters (ASCII letters, digits, `_`, `.`, `-`,
spaces, or parentheses; `.` and `..` are forbidden).

An existing final filename for the same patient returns HTTP 409 and is never
overwritten. Use another filename for another report. The same name is allowed
in different patient folders. For example, `Scan.png` and `Scan.webp` both resolve
to `Scan.jpg` and conflict within one patient's folder. An unknown patient returns 404; a target account
with another role returns 422. Missing authentication returns 401, and a caller
without a live admin role returns 403. Invalid fields or files return 422,
oversize uploads return 413, and unsupported request media types return 415.
Malformed multipart requests return 400.

Storage failures return a sanitized 502. A missing `medical_reports` bucket
returns 503 with an instruction to create the private bucket. A network failure may happen after
storage accepted the file; the backend never deletes an object after an
uncertain upload. A retry can therefore return 409. Inspect that patient's
folder in Supabase before choosing a new filename.

## Supabase setup

Create a **private** bucket named `medical_reports` before using the endpoint.
Set its file size limit to at least 20 MiB (and the project's global limit no
lower). Allow `application/pdf` and `image/jpeg` for stored files.
Keep direct client access closed; uploads use the backend's secret or legacy
service-role key. Firebase UIDs are folder names, not Supabase Auth identities.

The endpoint reuses `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and
`SUPABASE_TIMEOUT_SECONDS` from [supabase.env.example](supabase.env.example).
It does not create buckets or modify access policies. `SUPABASE_STORAGE_PUBLIC`
applies only to profile photos and does not control report uploads. This upload
endpoint returns storage identifiers; it does not issue a public or signed URL.

## Patient reports

`GET /api/v1/users/me/medical-reports?limit=20&offset=0` requires a patient
Firebase ID token in `Authorization: Bearer ...`. The backend derives the folder
from that token's UID; there is no target UID parameter. Doctors and admins
receive 403 on this patient endpoint. Missing authentication returns 401.

The response is `{ "status": 200, "msg": "Success", "data": { "reports": [],
"next_offset": null } }` for a patient without reports. Each report includes the
upload response fields plus `created_at`, `download_url`, and `expires_at`.
Open `download_url` to view or download the file. Links are signed for
`SUPABASE_SIGNED_URL_SECONDS` (default 86400); fetch this endpoint again to renew
expired links. Treat signed links as private. Responses use `Cache-Control: no-store`.

Reports are ordered by filename ascending. `limit` defaults to 20 (1–100), and
`offset` defaults to 0 (nonnegative). Follow `next_offset` until it is null.
Folder placeholders and unsupported files are omitted. Concurrent uploads can
shift offsets, so refresh from zero to see the latest complete list.

Provider references: [standard uploads](https://supabase.com/docs/guides/storage/uploads/standard-uploads),
[storage error codes](https://supabase.com/docs/guides/storage/debugging/error-codes).
