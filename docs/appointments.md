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

Each new transition to `completed` credits the treating doctor with 100 Tk by
default, for every consultation type. Set `DOCTOR_APPOINTMENT_EARNING_TK` to a
nonnegative integer and restart the backend to change the amount for future
completions. Cancelled/expired appointments do not earn a credit. Repeating a
completion is safe and does not credit again, including after changing the rate.

The appointment's `earnings_amount_tk` and the increment to
`doctor_earnings/{doctor_uid}.total_earnings` commit in one Firestore transaction.
This private collection is authoritative and is blocked by the existing client
rules. Completion then mirrors `total_earnings` into the doctor's Firebase Auth
custom claims while preserving other claims. An Auth failure does not roll back
completion or lose its credit; another completion retry or `GET /api/v1/users/me`
repairs the mirror. Concurrent Auth mirrors may be temporarily stale; the self
endpoint reads the authoritative total. Existing ID tokens can retain old claim
values until refreshed, so clients should use the self endpoint for earnings.

For a doctor, `GET /api/v1/users/me` returns `data.total_earnings` as an integer in
Bangladeshi taka (BDT), starting at 0. Other account roles return null. This field
is response-only; profile updates cannot change it. This is an accrued earnings
total, not a payment transfer or payout balance. Previously completed appointments
are not backfilled automatically.

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
