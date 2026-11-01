# Prescriptions

Doctors upload an image using a Firebase bearer token:

```sh
curl -X POST "$API_BASE_URL/api/v1/prescriptions/patients/$PATIENT_UID" \
  -H "Authorization: Bearer $DOCTOR_FIREBASE_ID_TOKEN" \
  -F "file=@/path/to/prescription.png"
```

Send only the multipart `file` field. JPEG, PNG, and WebP images are accepted,
up to 20 MiB and 25 million pixels. The backend verifies and converts images to
JPEG, preserves resolution, applies EXIF orientation, removes metadata, and
flattens transparency onto white. PDFs are rejected. Converted images must
also fit the size limit.

The backend checks both the caller's live doctor role and the target account's
patient role. The doctor's UID and name come from Firebase Auth, never request
fields. Patient/admin callers cannot upload. No appointment ID is required.

Storage uses the private **`prescription`** bucket (singular), at
`{patient_uid}/{doctor_name}_{index}.jpg`, e.g. `patient-uid/Dr Ahmed_0.jpg`.
The folder prefix is created by uploading the object. The name is normalized
to safe ASCII filename characters and at most 120 characters; a name with no
usable characters falls back to `Doctor`. The response preserves the original
doctor name separately.

The initial index is the number of files currently in that patient's folder,
starting at 0 for an empty folder. All pages of files are counted; folder
placeholders are excluded. Uploads never overwrite. If a simultaneous upload
or a deleted file causes a filename collision, the suffix advances until a
free name is found, with at most ten attempts. Different doctors can use the
same suffix with different names. Suffixes are filenames, not prescription IDs.
An uncertain upload returns 502 and is not deleted or automatically retried;
inspect the patient's prescriptions before retrying to avoid duplicates.

HTTP 201 returns the standard envelope with `data.patient_uid`, `doctor_uid`,
`doctor_name`, `index`, `filename`, `bucket`, `path`, `content_type`, and
`size_bytes` describing the stored JPEG.

Patients read their own prescriptions:

```sh
curl "$API_BASE_URL/api/v1/users/me/prescriptions?limit=20&offset=0" \
  -H "Authorization: Bearer $PATIENT_FIREBASE_ID_TOKEN"
```

HTTP 200 returns `{ "status": 200, "msg": "Success", "data": {
"prescriptions": [], "next_offset": null } }` for an empty folder.
Each prescription contains `patient_uid`, `filename`, `bucket`, `path`,
`content_type`, `size_bytes`, `created_at`, `download_url`, and `expires_at`.
Open the signed `download_url` to view or download the prescription. Links
expire after `SUPABASE_SIGNED_URL_SECONDS` (default 86400); fetch the list again
to renew them. Responses use `Cache-Control: no-store`.

The patient folder always comes from the authenticated UID. No target UID is
accepted by the GET endpoint. Doctors/admins cannot read through this patient
endpoint. Listing uses filename order, with `limit` from 1 to 100 (default 20)
and nonnegative `offset`. Follow `next_offset` until null. Concurrent uploads
can shift offsets; refresh from zero to get a new complete list.

Create a private `prescription` bucket with JPEG allowed and a file size limit
of at least 20 MiB. Keep direct client access closed. The backend reuses
`SUPABASE_URL` and `SUPABASE_SECRET_KEY`; the profile-photo public flag does not
apply. No Firestore documents or indexes are needed.
