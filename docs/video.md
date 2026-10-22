# Video RTC tokens

Set `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE` in the server environment or `.env`.
Use the same Agora App ID in the RTC client; keep the certificate on the server.

Call `POST /api/v1/video/token` with a Firebase bearer token and this body:

```json
{"appointment_id": "appointment-123"}
```

The Firebase UID in the caller's bearer token must match either the appointment's
`doctor_id` or `patient_id`, regardless of whether the account role is doctor or
patient. Admins can access any existing appointment. Creating an appointment records the
caller in `requested_by`; this alone does not grant participant access.
Use the doctor's `uid` from the doctor directory when booking an appointment.

The response is:

```json
{
  "status": 200,
  "msg": "Success",
  "data": {
    "token": "<Agora RTC token>",
    "channelName": "appointment-123"
  }
}
```

Join the RTC channel using `data.channelName` and `data.token`. Tokens allow
publishing audio and video for one hour and use UID 0 (the SDK can assign a
numeric UID when joining). Both participants use the appointment ID as the
channel name, regardless of any previously stored `video_room_id`.
Request another token before expiration and renew it through the RTC SDK.

The response fields replace the former `appointment_id`, `room_id`,
`access_token`, and `expires_at` fields. Missing Agora credentials return 503;
an appointment that is missing or inaccessible to the caller returns 404.

Token generation uses Agora's
[Python token builder](https://github.com/AgoraIO-Community/python-token-builder).
