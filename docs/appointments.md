# Appointments

`POST /api/v1/appointments` accepts `appointment_type`: `video`, `chat`, or
`in_person`. The create, list, and status responses include this value and
`patient_name`, resolved from the patient's Firebase Auth display name. Missing
or deleted profiles return a null name. Legacy records and clients omitting the
type use `video`; the original type cannot be recovered for older bookings that
never stored it. `scheduled_for` must include a timezone and be in the future.

Only the treating doctor or an admin can update an appointment's status through
`PATCH /api/v1/appointments/{appointment_id}/status?new_status=completed` (or
`cancelled`). Completion supports all three consultation types. Patients cannot
change status. Completed and cancelled appointments cannot be reopened.

Requested and confirmed appointments are automatically cancelled when their
calendar date has passed in `APPOINTMENT_TIMEZONE` (default `Asia/Dhaka`). A
consultation scheduled earlier today remains active until the next local day.
Completed appointments retain their status. The running API checks every 60
seconds and also checks on appointment reads/status updates. The startup sweep
catches appointments missed while the API was stopped. Firestore transactions
recheck the latest status before changing it, protecting concurrent completion
and multiple API workers. System cancellations record `updated_by: "system"`.

Deploy/restart the API to activate the lifespan worker; no Firestore migration
or new composite index is required. No live database changes are made by the
unit tests.
