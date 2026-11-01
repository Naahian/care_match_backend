# User profile

Authenticated `PATCH /api/v1/users/me` accepts JSON for profile fields, or
`multipart/form-data` to upload a photo with those fields:

```json
{
  "display_name": "Jane Doe",
  "phone_number": "+8801712345678",
  "date_of_birth": "1994-02-28",
  "gender": "Female",
  "blood_group": "AB+"
}
```

`date_of_birth` must be a valid `YYYY-MM-DD` date, no later than the server's
current date. Gender accepts `Male`, `Female`, or `Other`. Blood group accepts
`A+`, `A-`, `B+`, `B-`, `AB+`, `AB-`, `O+`, or `O-`. Gender and blood group
are case insensitive and surrounding whitespace is removed.

Omitted fields remain unchanged. Explicit `null`, an empty string, or whitespace
clears a field. An empty object returns HTTP 400; invalid values or attempts to
change UID, credentials, or role return HTTP 422.

For a new profile picture, send the file as `photo`; `photo_url` is response-only
and is rejected in requests. For example:

```sh
curl -X PATCH "$API_BASE_URL/api/v1/users/me" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -F "display_name=Jane Doe" \
  -F "gender=Female" \
  -F "photo=@/path/to/photo.jpg"
```

Multipart text fields use an empty string to clear a value. Omitting `photo`
preserves the current image. Send `remove_photo=true` (JSON boolean or form text)
to remove it; combining removal with an uploaded photo returns HTTP 422.

Photos must be JPEG, PNG, or WebP, at most 5 MB and 25 million pixels. The backend
decodes and resizes them to fit 1024×1024, strips metadata, and uploads a JPEG to
Supabase as `profile_images/{uid}.jpg`, overwriting that user's previous image.
The bucket is selected in the media code rather than an environment variable.
A saved version query parameter refreshes the displayed image after replacement.
It persists the object path in `user_profiles/{uid}` and returns its display
URL as `photo_url`. Legacy Firebase Auth pictures remain visible until replaced
or removed. Legacy objects with other names are deleted after replacement is saved.
The UID object is deleted when photo removal is saved. Cleanup failure is logged and does not undo a successful profile update.

Supabase upload failure prevents other edits from starting. If Auth rejects an
edit after upload, a newly referenced object is removed only when it does not
replace an existing UID object. An existing UID object is retained and partial
success is reported. An uncertain Firestore write keeps
the uploaded object because the saved profile may already reference it; the API
returns HTTP 503 and asks the client to reload. An unreferenced object can remain
after a failed write and may require later storage cleanup.

Set the following server-only environment variables and restart the backend:

```dotenv
SUPABASE_URL=https://xtqupofodiubgawqxggj.supabase.co
SUPABASE_SECRET_KEY=
SUPABASE_STORAGE_PUBLIC=true
```

Create/configure `profile_images` as a public bucket with image upload limits
matching the API. Set `SUPABASE_SECRET_KEY` to a Supabase secret key or legacy
service-role key. It must stay on the backend; the Flutter app uses its Firebase
token to upload to this API. The API does not create buckets or change their
access policy. Existing profile text updates work without Supabase credentials.
With a private bucket, set `SUPABASE_STORAGE_PUBLIC=false`; GET/PATCH then issue
fresh signed URLs lasting `SUPABASE_SIGNED_URL_SECONDS` (default 86400).

Both GET and PATCH `/api/v1/users/me` return the three personal fields with the
existing account fields in the standard `data` envelope. Users without stored
personal details receive nulls.

Account fields remain in Firebase Auth. Personal details are stored privately
in Firestore `user_profiles/{uid}` with merge writes, so the first edit creates
the document and later edits preserve omitted fields. The backend derives the
UID from the authenticated user. Existing Firestore rules block direct client
access to this collection. No data migration or new index is needed. Account
deletion also removes the personal profile document.

The Flutter edit screen selects a photo from the device, previews it, and sends
multipart bytes rather than a URL. iOS photo library usage text and macOS file
selection entitlements are included. Fully restart/rebuild the app after adding
the image picker plugin; verify gallery selection on a supported device.

Auth and Firestore do not share a transaction. If a mixed update fails after an
Auth write, HTTP 503 explicitly reports that some changes may have been saved
and asks the client to reload before retrying. A profile read failure prevents
the update from starting.

Implementation references: [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys),
[Storage uploads](https://supabase.com/docs/guides/storage/uploads/standard-uploads),
[FastAPI forms and files](https://fastapi.tiangolo.com/tutorial/request-forms-and-files/).
